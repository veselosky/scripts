from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import yaml
from slugify import slugify

SEARCH_URL = "https://openlibrary.org/search.json"
OPENLIBRARY_BASE_URL = "https://openlibrary.org"
COVERS_URL = "https://covers.openlibrary.org"

DATE_PATTERNS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%Y-%m",
    "%Y/%m",
    "%B %Y",
    "%b %Y",
    "%Y",
)

PRINT_HARDCOVER_TERMS = (
    "hardcover",
    "hard cover",
    "hardback",
    "library binding",
    "casebound",
    "cloth",
)
PRINT_PAPERBACK_TERMS = (
    "paperback",
    "paper back",
    "softcover",
    "soft cover",
    "trade paperback",
    "mass market paperback",
    "pbk",
)
NON_PRINT_TERMS = (
    "ebook",
    "e-book",
    "kindle",
    "epub",
    "pdf",
    "audiobook",
    "audio book",
    "cd",
    "mp3",
    "digital",
)


@dataclass(slots=True)
class BookRequest:
    title: str
    author: str
    source_line: int


@dataclass(slots=True)
class EditionSelection:
    work_key: str
    work_title: str
    author_name: str
    edition: dict[str, Any]
    book_format: str
    date_rank: tuple[int, int, int, int]


class OpenLibraryClient:
    def __init__(
        self,
        logger: logging.Logger,
        min_interval_seconds: float = 1.0,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._logger = logger
        self._session = requests.Session()
        self._min_interval_seconds = min_interval_seconds
        self._timeout_seconds = timeout_seconds
        self._last_request_time = 0.0

    def _wait_for_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self._min_interval_seconds:
            time.sleep(self._min_interval_seconds - elapsed)

    def _get(self, url: str, params: dict[str, Any] | None = None) -> requests.Response:
        self._wait_for_rate_limit()

        response = self._session.get(url, params=params, timeout=self._timeout_seconds)
        self._last_request_time = time.monotonic()

        if response.request is not None and response.request.url is not None:
            queried_url = response.request.url
        else:
            queried_url = url

        self._logger.info("QUERY URL: %s", queried_url)

        content_type = response.headers.get("content-type", "").casefold()
        if (
            "json" in content_type
            or content_type.startswith("text/")
            or "xml" in content_type
        ):
            text_preview = response.text
        else:
            text_preview = (
                f"<non-text response omitted; content-type={content_type or 'unknown'}; "
                f"bytes={len(response.content)}>"
            )

        self._logger.info("RESPONSE TEXT START")
        self._logger.info(text_preview)
        self._logger.info("RESPONSE TEXT END")

        response.raise_for_status()
        return response

    def search_works(self, title: str, author: str, limit: int = 5) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "title": title,
            "author": author,
            "fields": "key,title,author_name",
            "limit": limit,
        }
        response = self._get(SEARCH_URL, params=params)
        payload = response.json()
        docs = payload.get("docs", [])
        if not isinstance(docs, list):
            return []
        return [item for item in docs if isinstance(item, dict)]

    def fetch_work_editions(self, work_key: str) -> list[dict[str, Any]]:
        editions: list[dict[str, Any]] = []
        offset = 0
        limit = 200

        while True:
            url = f"{OPENLIBRARY_BASE_URL}{work_key}/editions.json"
            response = self._get(url, params={"limit": limit, "offset": offset})
            payload = response.json()
            entries = payload.get("entries", [])
            if not isinstance(entries, list) or not entries:
                break

            for entry in entries:
                if isinstance(entry, dict):
                    editions.append(entry)

            offset += len(entries)
            if len(entries) < limit:
                break

        return editions

    def download_cover(self, cover_id: int) -> tuple[bytes, str]:
        url = f"{COVERS_URL}/b/id/{cover_id}-L.jpg"
        response = self._get(url)

        content_type = response.headers.get("content-type", "").lower()
        if "png" in content_type:
            suffix = ".png"
        elif "webp" in content_type:
            suffix = ".webp"
        else:
            suffix = ".jpg"

        return response.content, suffix


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch latest English print editions from Open Library and output Markdown files."
    )
    parser.add_argument("input_file", help="Path to input text file containing 'Title by Author' lines")
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory for generated markdown and cover image files (default: current directory)",
    )
    parser.add_argument(
        "--log-file",
        default="bookfetch.log",
        help="Path to log file (default: ./bookfetch.log)",
    )
    return parser.parse_args()


def setup_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("bookfetch")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.propagate = False
    return logger


def parse_input_file(input_path: Path, logger: logging.Logger) -> list[BookRequest]:
    requests_to_process: list[BookRequest] = []

    with input_path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if "by" not in line.lower():
                logger.warning("Skipping line %d: no 'by' separator found", line_number)
                continue

            parts = re.split(r"\s+by\s+", line, maxsplit=1, flags=re.IGNORECASE)
            if len(parts) != 2:
                logger.warning("Skipping line %d: unable to parse title/author", line_number)
                continue

            title = parts[0].strip()
            author = parts[1].strip()
            if not title or not author:
                logger.warning("Skipping line %d: empty title or author", line_number)
                continue

            requests_to_process.append(
                BookRequest(title=title, author=author, source_line=line_number)
            )

    return requests_to_process


def normalize_text(value: str) -> str:
    lowered = value.casefold()
    return re.sub(r"\s+", " ", lowered).strip()


def pick_work_candidate(
    docs: list[dict[str, Any]],
    input_title: str,
    input_author: str,
) -> dict[str, Any] | None:
    if not docs:
        return None

    norm_title = normalize_text(input_title)
    norm_author = normalize_text(input_author)

    def score_doc(doc: dict[str, Any]) -> int:
        score = 0

        title = str(doc.get("title", ""))
        norm_doc_title = normalize_text(title)
        if norm_doc_title == norm_title:
            score += 3
        elif norm_title and norm_title in norm_doc_title:
            score += 1

        author_names = doc.get("author_name", [])
        if isinstance(author_names, list):
            for item in author_names:
                candidate = normalize_text(str(item))
                if candidate == norm_author:
                    score += 3
                    break
                if norm_author and (norm_author in candidate or candidate in norm_author):
                    score += 1

        return score

    return max(docs, key=score_doc)


def extract_language_codes(edition: dict[str, Any]) -> set[str]:
    languages = edition.get("languages", [])
    result: set[str] = set()

    if not isinstance(languages, list):
        return result

    for entry in languages:
        if isinstance(entry, str):
            result.add(entry.casefold())
            continue

        if isinstance(entry, dict):
            key = str(entry.get("key", ""))
            if "/languages/" in key:
                result.add(key.rsplit("/", maxsplit=1)[-1].casefold())

    return result


def detect_print_format(edition: dict[str, Any]) -> str | None:
    format_values: list[str] = []

    physical_format = edition.get("physical_format")
    if isinstance(physical_format, str):
        format_values.append(physical_format)

    raw_format = edition.get("format")
    if isinstance(raw_format, str):
        format_values.append(raw_format)
    elif isinstance(raw_format, list):
        format_values.extend([str(item) for item in raw_format])

    format_blob = " ".join(format_values).casefold()

    if any(term in format_blob for term in NON_PRINT_TERMS):
        return None

    if any(term in format_blob for term in PRINT_HARDCOVER_TERMS):
        return "Hardcover"

    if any(term in format_blob for term in PRINT_PAPERBACK_TERMS):
        return "Paperback"

    return None


def parse_date_rank(raw_date: str | None) -> tuple[int, int, int, int]:
    if not raw_date:
        return (0, 0, 0, 0)

    value = raw_date.strip()
    if not value:
        return (0, 0, 0, 0)

    best_rank = (0, 0, 0, 0)

    for pattern in DATE_PATTERNS:
        try:
            dt = datetime.strptime(value, pattern)
        except ValueError:
            continue

        if pattern in {"%Y-%m-%d", "%Y/%m/%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y"}:
            rank = (dt.year, dt.month, dt.day, 3)
        elif pattern in {"%Y-%m", "%Y/%m", "%B %Y", "%b %Y"}:
            rank = (dt.year, dt.month, 0, 2)
        else:
            rank = (dt.year, 0, 0, 1)

        if rank > best_rank:
            best_rank = rank

    if best_rank != (0, 0, 0, 0):
        return best_rank

    years = [int(match) for match in re.findall(r"\b(1[5-9]\d\d|20\d\d|2100)\b", value)]
    if years:
        return (max(years), 0, 0, 1)

    return (0, 0, 0, 0)


def pick_publish_date(edition: dict[str, Any]) -> str:
    publish_date = edition.get("publish_date")

    if isinstance(publish_date, str):
        return publish_date.strip()

    if isinstance(publish_date, list):
        candidates = [str(item).strip() for item in publish_date if str(item).strip()]
        if not candidates:
            return ""
        return max(candidates, key=parse_date_rank)

    return ""


def pick_isbn(edition: dict[str, Any]) -> str:
    isbn_13 = edition.get("isbn_13")
    if isinstance(isbn_13, list):
        for value in isbn_13:
            text = str(value).strip()
            if text:
                return text

    isbn_10 = edition.get("isbn_10")
    if isinstance(isbn_10, list):
        for value in isbn_10:
            text = str(value).strip()
            if text:
                return text

    return ""


def pick_description(edition: dict[str, Any]) -> str:
    description = edition.get("description")

    if isinstance(description, str):
        return description.strip()

    if isinstance(description, dict):
        value = description.get("value")
        if isinstance(value, str):
            return value.strip()

    return ""


def pick_publisher(edition: dict[str, Any]) -> str:
    publishers = edition.get("publishers")
    if isinstance(publishers, list):
        for value in publishers:
            text = str(value).strip()
            if text:
                return text

    if isinstance(publishers, str):
        return publishers.strip()

    return ""


def pick_subtitle(edition: dict[str, Any]) -> str:
    subtitle = edition.get("subtitle")
    if isinstance(subtitle, str):
        return subtitle.strip()
    return ""


def extract_cover_id(edition: dict[str, Any]) -> int | None:
    covers = edition.get("covers")
    if isinstance(covers, list):
        for value in covers:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue

    cover_i = edition.get("cover_i")
    try:
        return int(cover_i)
    except (TypeError, ValueError):
        return None


def choose_latest_print_edition(
    work_key: str,
    work_title: str,
    author_name: str,
    editions: list[dict[str, Any]],
) -> EditionSelection | None:
    candidates: list[EditionSelection] = []

    for edition in editions:
        language_codes = extract_language_codes(edition)
        if "eng" not in language_codes:
            continue

        book_format = detect_print_format(edition)
        if book_format not in {"Hardcover", "Paperback"}:
            continue

        publish_date = pick_publish_date(edition)
        date_rank = parse_date_rank(publish_date)

        candidates.append(
            EditionSelection(
                work_key=work_key,
                work_title=work_title,
                author_name=author_name,
                edition=edition,
                book_format=book_format,
                date_rank=date_rank,
            )
        )

    if not candidates:
        return None

    def sort_key(selection: EditionSelection) -> tuple[tuple[int, int, int, int], str]:
        key = str(selection.edition.get("key", ""))
        return (selection.date_rank, key)

    return max(candidates, key=sort_key)


def build_front_matter(
    title: str,
    subtitle: str,
    author: str,
    date_published: str,
    isbn: str,
    book_format: str,
    cover_reference: str,
    publisher: str,
) -> dict[str, Any]:
    return {
        "title": title,
        "authors": [author],
        "genre": [],
        "series": [],
        "series_weight": 0,
        "tags": [],
        "params": {
            "creative_work": {
                "name": title,
                "alternateName": subtitle,
                "author": [{"name": author}],
                "datePublished": date_published,
                "image": [
                    {
                        "url": cover_reference,
                        "description": "front cover",
                    }
                ],
            },
            "book": [
                {
                    "inLanguage": "en",
                    "isbn": isbn,
                    "bookFormat": book_format,
                    "datePublished": date_published,
                    "publisher": publisher,
                }
            ],
        },
    }


def ensure_unique_basename(base_slug: str, used_slugs: set[str]) -> str:
    slug = base_slug or "book"
    if slug not in used_slugs:
        used_slugs.add(slug)
        return slug

    index = 2
    while True:
        candidate = f"{slug}-{index}"
        if candidate not in used_slugs:
            used_slugs.add(candidate)
            return candidate
        index += 1


def write_markdown(
    output_path: Path,
    front_matter: dict[str, Any],
    description: str,
) -> None:
    yaml_text = yaml.safe_dump(
        front_matter,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=False,
    ).strip()

    body = description.strip()
    if body:
        content = f"---\n{yaml_text}\n---\n\n{body}\n"
    else:
        content = f"---\n{yaml_text}\n---\n"

    output_path.write_text(content, encoding="utf-8")


def process_book(
    book: BookRequest,
    client: OpenLibraryClient,
    output_dir: Path,
    used_slugs: set[str],
    logger: logging.Logger,
) -> None:
    logger.info("Processing line %d: %s by %s", book.source_line, book.title, book.author)

    docs = client.search_works(book.title, book.author)
    work_doc = pick_work_candidate(docs, book.title, book.author)
    if work_doc is None:
        logger.warning("No matching work found for '%s' by '%s'", book.title, book.author)
        return

    work_key = str(work_doc.get("key", "")).strip()
    if not work_key.startswith("/works/"):
        logger.warning("Search returned invalid work key for '%s' by '%s'", book.title, book.author)
        return

    work_title = str(work_doc.get("title", "")).strip() or book.title
    author_names = work_doc.get("author_name", [])
    if isinstance(author_names, list) and author_names:
        chosen_author = str(author_names[0]).strip() or book.author
    else:
        chosen_author = book.author

    editions = client.fetch_work_editions(work_key)
    if not editions:
        logger.warning("No editions found for work %s", work_key)
        return

    selected = choose_latest_print_edition(work_key, work_title, chosen_author, editions)
    if selected is None:
        logger.warning(
            "No English print editions found for work %s (%s by %s)",
            work_key,
            book.title,
            book.author,
        )
        return

    edition = selected.edition
    resolved_title = str(edition.get("title", "")).strip() or selected.work_title or book.title
    resolved_subtitle = pick_subtitle(edition)
    publish_date = pick_publish_date(edition)
    isbn = pick_isbn(edition)
    publisher = pick_publisher(edition)
    description = pick_description(edition)

    base_slug = slugify(resolved_title)
    basename = ensure_unique_basename(base_slug, used_slugs)
    markdown_path = output_dir / f"{basename}.md"

    cover_reference = ""
    cover_id = extract_cover_id(edition)
    if cover_id is not None:
        try:
            cover_bytes, suffix = client.download_cover(cover_id)
            cover_path = output_dir / f"{basename}{suffix}"
            cover_path.write_bytes(cover_bytes)
            cover_reference = cover_path.name
        except requests.RequestException as exc:
            logger.warning("Cover download failed for %s: %s", resolved_title, exc)

    front_matter = build_front_matter(
        title=resolved_title,
        subtitle=resolved_subtitle,
        author=selected.author_name,
        date_published=publish_date,
        isbn=isbn,
        book_format=selected.book_format,
        cover_reference=cover_reference,
        publisher=publisher,
    )

    write_markdown(markdown_path, front_matter, description)

    logger.info(
        "SUCCESS: wrote %s using edition %s (%s %s)",
        markdown_path.name,
        str(edition.get("key", "")),
        selected.book_format,
        publish_date,
    )


def main() -> int:
    args = parse_args()
    input_path = Path(args.input_file)
    output_dir = Path(args.output_dir)
    log_path = Path(args.log_file)

    if not input_path.is_file():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)

    logger = setup_logger(log_path)
    logger.info("Starting bookfetch")

    try:
        books = parse_input_file(input_path, logger)
    except OSError as exc:
        logger.exception("Failed to read input file")
        print(f"Error: failed to read input file: {exc}", file=sys.stderr)
        return 1

    if not books:
        logger.warning("No valid book lines found in input file")
        return 0

    client = OpenLibraryClient(logger=logger, min_interval_seconds=1.0)
    used_slugs: set[str] = set()

    for book in books:
        try:
            process_book(book, client, output_dir, used_slugs, logger)
        except requests.RequestException:
            logger.exception("Network error while processing '%s' by '%s'", book.title, book.author)
        except OSError:
            logger.exception("Filesystem error while processing '%s' by '%s'", book.title, book.author)
        except Exception:
            logger.exception("Unexpected error while processing '%s' by '%s'", book.title, book.author)

    logger.info("Completed bookfetch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

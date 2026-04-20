# Book Fetch

GOAL: Given the title and author of a book, locate the latest print edition of the book and save a set of information about it, including the title, author, publisher, publication date, ISBN, and description.

## Input

Input will be provided in the form of a text file with a title and author on each line, separated by the word "by". For example:

```
The Great Gatsby by F. Scott Fitzgerald
To Kill a Mockingbird by Harper Lee
```

Ignore blank lines, lines beginning with a hash (#), and lines that do not contain the word "by".

## Output

Output a Markdown file (named according to the book title, but slugified) with YAML front matter containing the following information about the book:

```yaml
genre: []  # List of genres the book belongs to (e.g. Fiction, Mystery, Science Fiction, etc.)
series: []  # If book is part of a series, title of the series as item 0 of the array
series_weight: 0  # If book is part of a series, position of the book in the series (e.g. 1 for first, 2 for second, etc.)
tags: []
params:
  creative_work:
    name: ''  # Book title
    alternateName: ''  # Book subtitle, if applicable
    author:
      - name: ''
    datePublished: ''
    image:
      - url: ''
        description: 'front cover'
  book:
    - inLanguage: 'en'  # We are only interested in English language editions
      isbn: ''
      bookFormat: ''  # One of: 'EBook', 'Hardcover', 'Paperback', 'Audiobook'
      datePublished: ''
```

In the body of the markdown file, include the description of the book, if available.

Save the front cover image of the book in a separate file with the same basename as the markdown file.

## Constraints

- The script should only consider print editions of the book (i.e. Hardcover and Paperback formats). EBooks and Audiobooks should be ignored.
- If multiple print editions are found, the script should select the latest one based on the publication date.
- The script should only consider English language editions of the book.
- The script should handle cases where some information is missing (e.g. if the publisher or publication date is not available, it should still save the other information that is available).
- The script should use the Open Library API to fetch information about the book. Documentation for the API can be found here: https://openlibrary.org/dev/docs/api/search
- The script must be polite, limiting requests to the Open Library API to no more than 1 request per second to avoid overwhelming the server.
- Write in Python.
- Use the `requests` library to make HTTP requests to the Open Library API.
- Use the `PyYAML` library to handle YAML front matter in the markdown files.
- Use the `slugify` library to create slugified filenames for the markdown files and cover images.
- Handle exceptions gracefully, logging any errors encountered during the process and continuing with the next book in the input file.
- For testing and debugging, the script should log each URL it queries from the Open Library API, and the text of the response. Write to a log file named `bookfetch.log` in the current directory.
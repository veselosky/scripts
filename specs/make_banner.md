We're going to write a script that will use Pillow to quickly generate a website banner from an input image. 

The resulting image will have an aspect ratio of 10:3. 

## Script Arguments

- `input_image_path`: The file path to the input image
- `output_image_path`: The file path where the output banner image will be saved. Default is `banner.png` in the current directory.
- `crop`: A string that specifies how to crop the image. It can be one of the following:
  - `center` (default): Crop the image from the center
  - `top`: Crop the image from the top
  - `bottom`: Crop the image from the bottom
  - integer: Crop the image from a specific pixel value (e.g., `100` to crop from 100 pixels down from the top)
- `gradient_width`: An optional integer that specifies the width of the gradient overlay as a percentage of image width (default is 66)
- `gradient_color`: An optional color spec (e.g. #RRGGBBAA) of the gradient in RGBA format (default is white)
- `text`: An optional string that specifies the text to overlay on the banner (default is no text)
- `font`: The name of an installed system font to use for the text (default to Noto Sans)

## Steps

- Load the input image
- If necessary, convert the image to RGBA mode
- Determine the crop area based on the chosen option
- Crop the image to the desired aspect ratio
- Overlay a `gradient_color` to transparent gradient on the cropped image, left to right, covering `gradient_width` of the width
- If `text` is provided, overlay the text on the banner using the specified `font`. The text should be centered vertically and positioned towards the left side of the banner, with equal spacing to the top, bottom, and left edges equivalent to ~10% of the banner height.
- Save the resulting banner image to the specified output path as a .png file

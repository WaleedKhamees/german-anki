"""Image helpers shared by card and sentence import."""
from PIL import Image

SCREENSHOT_WIDTH = 720   # px; screenshots in Anki are this wide, height follows the video's aspect ratio


def fit_width(img, width=SCREENSHOT_WIDTH):
    """Scale `img` to `width` px keeping its aspect ratio (1280x720 -> 720x405, 4:3 -> 720x540).
    Never upscales: a frame that is already `width` px or narrower is returned unchanged."""
    w, h = img.size
    if w <= width:
        return img
    return img.resize((width, max(1, round(h * width / w))), Image.LANCZOS)

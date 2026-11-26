"""Captured screenshots and audio clips (in <data>/assets) -> Anki's media folder.
Shared by word cards and sentence cards."""
import base64
import io
import subprocess

from PIL import Image

from .images import fit_width


def notify(title, message, is_error=False):
    subprocess.run(["notify-send", "-u", "critical" if is_error else "normal", title, message])


def find(assets_dir, filename):
    """The file in the assets folder, matched case-insensitively; None if it isn't there."""
    if not assets_dir.is_dir():
        return None
    direct = assets_dir / filename
    if direct.is_file():
        return direct
    lower = filename.lower()
    return next((p for p in assets_dir.iterdir() if p.name.lower() == lower and p.is_file()), None)


def upload_audio(anki, assets_dir, filename):
    """Store the clip in Anki's media folder; -> media filename, or None if it isn't found."""
    audio_file = find(assets_dir, filename)
    if not audio_file:
        print(f"[!] Audio '{filename}' not found in {assets_dir}")
        return None
    media_filename = audio_file.name.lower()
    anki("storeMediaFile", filename=media_filename, path=str(audio_file.resolve()))
    return media_filename


def upload_image(anki, assets_dir, filename):
    """Scale the screenshot to the card width and store it; -> media filename, or None if not found."""
    image_file = find(assets_dir, filename)
    if not image_file:
        print(f"[!] Image '{filename}' not found in {assets_dir}")
        return None
    with open(image_file, "rb") as f:
        img = Image.open(f)
        img.load()
    img = fit_width(img.convert("RGBA" if img.mode in ("RGBA", "LA") else "RGB"))
    buf = io.BytesIO()
    img.save(buf, format="PNG" if image_file.suffix.lower() == ".png" else "WEBP", optimize=True)
    media_filename = image_file.name.lower()
    anki("storeMediaFile", filename=media_filename, data=base64.b64encode(buf.getvalue()).decode("ascii"))
    return media_filename

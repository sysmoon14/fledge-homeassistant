"""Album art for the thermostat: a media player's picture, square and small
enough for its card, as the PNG nestlocal reads on <base>/set/art/<entity>."""

from __future__ import annotations

from io import BytesIO

# How wide the thermostat shows album art, and a camera's still (the whole
# display; nestlocal takes up to 480).
SIZE = 168
SCREEN = 480


def thumbnail(data: bytes, size: int = SIZE) -> bytes | None:
    """The picture cropped square from its middle and scaled to `size`, as
    an RGB PNG; None if it isn't a picture Pillow reads."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    try:
        with Image.open(BytesIO(data)) as image:
            fitted = ImageOps.fit(image.convert("RGB"), (size, size), Image.Resampling.LANCZOS)
    except (OSError, ValueError):
        return None
    out = BytesIO()
    fitted.save(out, "PNG", optimize=True)
    return out.getvalue()


def screen(data: bytes, size: int = SCREEN) -> bytes | None:
    """A camera's picture for the thermostat to fill its display with, as
    cheaply as can be: a JPEG already small enough (Home Assistant asks the
    camera for one that size) goes as it is, not even decoded; anything
    else is decoded at a fraction of its size (JPEG's own scaling), cropped
    square and sent as a JPEG. None if it isn't a picture."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format == "JPEG" and max(image.size) <= size * 5 // 3:
                return data
            image.draft("RGB", (size, size))
            fitted = ImageOps.fit(image.convert("RGB"), (size, size), Image.Resampling.BILINEAR)
    except (OSError, ValueError):
        return None
    out = BytesIO()
    fitted.save(out, "JPEG", quality=80)
    return out.getvalue()

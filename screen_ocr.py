"""
Screen capture + OCR (F9 / F12)
- capture_image(bbox): grabs ALL monitors (or just a dragged region) as a PIL image
- ocr_image(img): local RapidOCR text (used as a hint for the vision model, or alone as fallback)
- image_to_b64(img): downscaled JPEG for the local vision model
The stealth HUD is excluded from captures by Windows (WDA flag).
"""
import base64
import io

import numpy as np
from PIL import Image, ImageGrab

_ocr_engine = None
OCR_MAX_SIDE = 2560


def _get_ocr():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    return _ocr_engine


def capture_image(bbox=None):
    """bbox = (left, top, right, bottom) in screen pixels, or None for every monitor."""
    try:
        if bbox:
            img = ImageGrab.grab(bbox=tuple(int(v) for v in bbox), all_screens=True)
        else:
            img = ImageGrab.grab(all_screens=True)
        return img.convert("RGB")
    except Exception as e:
        print(f"[ScreenOCR] capture error: {e}")
        return None


def _shrink(img: Image.Image, max_side: int) -> Image.Image:
    w, h = img.size
    scale = max_side / max(w, h)
    if scale >= 1:
        return img
    return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)


def image_to_b64(img: Image.Image, max_side: int = 1120) -> str:
    buf = io.BytesIO()
    _shrink(img, max_side).save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def ocr_image(img: Image.Image) -> str:
    try:
        arr = np.array(_shrink(img, OCR_MAX_SIDE))
        result, _ = _get_ocr()(arr)
        if not result:
            return ""
        lines = [item[1].strip() for item in result if item[1] and float(item[2]) > 0.50]
        return "\n".join(lines)[:3000]
    except Exception as e:
        print(f"[ScreenOCR] OCR error: {e}")
        return ""


def capture_screen_text(bbox=None) -> str:
    """Back-compat helper: capture + OCR in one call."""
    img = capture_image(bbox)
    return ocr_image(img) if img is not None else ""


if __name__ == "__main__":
    print("Testing Screen OCR...")
    text = capture_screen_text()
    print(f"Extracted {len(text)} chars:\n{text[:400]}")

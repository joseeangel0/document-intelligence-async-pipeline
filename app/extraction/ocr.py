"""OCR engines behind one interface so they can be benchmarked and swapped per request."""

from __future__ import annotations

import logging
import math
import os
import statistics
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageOps

from app.config import settings

log = logging.getLogger(__name__)


@dataclass
class OcrOutput:
    text: str
    confidence: float | None  # 0..100, mean word confidence
    rotated_degrees: int = 0


class OcrEngine:
    name = "base"

    def recognize(self, image: Image.Image, languages: str) -> OcrOutput:  # pragma: no cover
        raise NotImplementedError


def _prepare(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("L", "RGB"):
        image = image.convert("RGB")
    # Very small images (screenshots, thumbnails) OCR noticeably better when upscaled.
    if min(image.size) < 600:
        factor = 2
        image = image.resize((image.width * factor, image.height * factor), Image.Resampling.LANCZOS)
    return image


class TesseractEngine(OcrEngine):
    name = "tesseract"

    def __init__(self):
        import pytesseract

        self._tess = pytesseract
        self.version = str(pytesseract.get_tesseract_version())
        self.languages = set(pytesseract.get_languages(config=""))

    def _validate_languages(self, languages: str) -> str:
        requested = [lang for lang in languages.split("+") if lang]
        missing = [lang for lang in requested if lang not in self.languages]
        if missing:
            raise ValueError(f"OCR language(s) not installed: {', '.join(missing)}")
        return "+".join(requested)

    def _run(self, image: Image.Image, lang: str) -> OcrOutput:
        data = self._tess.image_to_data(
            image, lang=lang, config="--oem 1 --psm 3", output_type=self._tess.Output.DICT
        )
        lines: dict[tuple, list[str]] = {}
        confidences: list[float] = []
        for i, word in enumerate(data["text"]):
            if not word or not word.strip():
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines.setdefault(key, []).append(word)
            conf = float(data["conf"][i])
            if conf >= 0:
                confidences.append(conf)
        # Rebuild text: blank line between paragraphs/blocks, newline between lines.
        out, previous = [], None
        for key in sorted(lines):
            if previous is not None and key[:2] != previous[:2]:
                out.append("")
            out.append(" ".join(lines[key]))
            previous = key
        confidence = round(sum(confidences) / len(confidences), 1) if confidences else None
        return OcrOutput(text="\n".join(out).strip(), confidence=confidence)

    def recognize(self, image: Image.Image, languages: str) -> OcrOutput:
        lang = self._validate_languages(languages)
        image = _prepare(image)
        result = self._run(image, lang)
        # Low confidence can mean a rotated page (phone photos, sideways scans): ask OSD and retry once.
        if result.confidence is None or result.confidence < 50:
            try:
                osd = self._tess.image_to_osd(image, config="--psm 0", output_type=self._tess.Output.DICT)
                angle = int(osd.get("rotate", 0))
                if angle and float(osd.get("orientation_conf", 0)) > 1.5:
                    rotated = self._run(image.rotate(-angle, expand=True), lang)
                    if (rotated.confidence or 0) > (result.confidence or 0):
                        rotated.rotated_degrees = angle
                        return rotated
            except self._tess.TesseractError:
                pass  # OSD needs enough text; not being able to decide is fine
        return result


# ---------------------------------------------------------------------------------------------- RapidOCR


def _group_lines(boxes: list[dict]) -> str:
    """Join boxes into lines: top-to-bottom, boxes on the same line left-to-right."""
    if not boxes:
        return ""
    boxes = sorted(boxes, key=lambda b: (b["y0"] + b["y1"]) / 2)
    median_h = statistics.median(b["y1"] - b["y0"] for b in boxes) or 1
    lines: list[list[dict]] = []
    for b in boxes:
        cy = (b["y0"] + b["y1"]) / 2
        if lines and abs(cy - statistics.mean((x["y0"] + x["y1"]) / 2 for x in lines[-1])) < 0.5 * median_h:
            lines[-1].append(b)
        else:
            lines.append([b])
    return "\n".join(" ".join(b["text"] for b in sorted(line, key=lambda b: b["x0"])) for line in lines)


def _largest_gap(intervals: list[tuple[float, float]]) -> tuple[float, float]:
    """Largest empty gap in the 1-D projection of intervals -> (gap size, cut position)."""
    intervals = sorted(intervals)
    best, cut, reach = 0.0, 0.0, intervals[0][1]
    for lo, hi in intervals[1:]:
        if lo - reach > best:
            best, cut = lo - reach, (lo + reach) / 2
        reach = max(reach, hi)
    return best, cut


def _xy_cut(boxes: list[dict], median_h: float) -> list[list[dict]]:
    """Recursive XY-cut: split on the widest whitespace gap (columns need a gap >= 1 line height)."""
    if len(boxes) <= 1:
        return [boxes]
    gap_y, cut_y = _largest_gap([(b["y0"], b["y1"]) for b in boxes])
    gap_x, cut_x = _largest_gap([(b["x0"], b["x1"]) for b in boxes])
    if gap_x >= median_h and gap_x > gap_y:
        left = [b for b in boxes if (b["x0"] + b["x1"]) / 2 < cut_x]
        right = [b for b in boxes if (b["x0"] + b["x1"]) / 2 >= cut_x]
        return _xy_cut(left, median_h) + _xy_cut(right, median_h)
    if gap_y > 0:
        top = [b for b in boxes if (b["y0"] + b["y1"]) / 2 < cut_y]
        bottom = [b for b in boxes if (b["y0"] + b["y1"]) / 2 >= cut_y]
        return _xy_cut(top, median_h) + _xy_cut(bottom, median_h)
    return [boxes]


def reading_order(items: list[tuple[list, str]]) -> str:
    """Order detector boxes (4 corner points + text) the way a person reads the page.

    Deskew by the median text-line angle, split columns/blocks with XY-cut, then group lines.
    (Same algorithm that was scored in the benchmark.)
    """
    if not items:
        return ""
    angles = []
    for box, _ in items:
        (x0, y0), (x1, y1) = box[0], box[1]
        if x1 - x0 > 2 * abs(y1 - y0):
            angles.append(math.atan2(y1 - y0, x1 - x0))
    theta = -statistics.median(angles) if angles else 0.0
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    boxes = []
    for box, text in items:
        pts = [(x * cos_t - y * sin_t, x * sin_t + y * cos_t) for x, y in box]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        boxes.append({"x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys), "text": text})
    median_h = statistics.median(b["y1"] - b["y0"] for b in boxes) or 1
    return "\n".join(_group_lines(block) for block in _xy_cut(boxes, median_h) if block)


class RapidOcrEngine(OcrEngine):
    """PaddleOCR (PP-OCR) models on ONNX Runtime. Most accurate in the benchmark; script-based, not language-based."""

    name = "rapidocr"

    def __init__(self):
        from rapidocr import RapidOCR

        threads = int(os.environ.get("OCR_THREADS", "1"))
        self._engine = RapidOCR(params={
            "Global.log_level": "error",
            # The 0/180° line classifier flipped some upright lines in the benchmark; page orientation is
            # handled below with Tesseract OSD instead.
            "Global.use_cls": False,
            "EngineConfig.onnxruntime.intra_op_num_threads": threads,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        })
        self._tess = None

    def _run(self, image: Image.Image) -> tuple[OcrOutput, float]:
        import numpy as np

        out = self._engine(np.asarray(image.convert("RGB")))
        if out.txts is None or not len(out.txts):
            return OcrOutput(text="", confidence=None), 0.0
        items = [(box.tolist(), txt) for box, txt in zip(out.boxes, out.txts)]
        # Share of boxes taller than wide: high values mean the page is rotated 90/270 degrees.
        vertical = sum(1 for box, _ in items if _box_h(box) > 1.5 * _box_w(box)) / len(items)
        confidence = round(100 * float(sum(out.scores)) / len(out.scores), 1)
        return OcrOutput(text=reading_order(items), confidence=confidence), vertical

    def _orientation(self, image: Image.Image) -> int:
        try:
            import pytesseract

            osd = pytesseract.image_to_osd(image, config="--psm 0", output_type=pytesseract.Output.DICT)
            if float(osd.get("orientation_conf", 0)) > 1.5:
                return int(osd.get("rotate", 0))
        except Exception:
            pass  # not enough text to decide, or Tesseract not installed
        return 0

    def recognize(self, image: Image.Image, languages: str) -> OcrOutput:
        image = _prepare(image)
        result, vertical = self._run(image)
        if not result.text or vertical > 0.5 or (result.confidence or 0) < 75:
            angle = self._orientation(image)
            if angle:
                rotated, _ = self._run(image.rotate(-angle, expand=True))
                if len(rotated.text) >= len(result.text) * 0.8 and (rotated.confidence or 0) >= (result.confidence or 0):
                    rotated.rotated_degrees = angle
                    return rotated
        return result


def _box_w(box) -> float:
    return math.dist(box[0], box[1])


def _box_h(box) -> float:
    return math.dist(box[1], box[2])


ENGINES: dict[str, type[OcrEngine]] = {"rapidocr": RapidOcrEngine, "tesseract": TesseractEngine}
ENGINE_DESCRIPTIONS = {
    "rapidocr": "PP-OCR models on ONNX Runtime: most accurate on noisy scans, photos, skew and columns (~0.9 s/page).",
    "tesseract": "Tesseract 5 LSTM: ~3x faster and ~4x less memory, excellent on clean scans, fragile on noisy photos.",
}


def available_engines() -> list[str]:
    return list(ENGINES)


@lru_cache
def installed_languages(engine: str) -> frozenset[str]:
    """Languages an engine can read, without loading its models (cheap enough for the API)."""
    if engine in ENGINES:
        # RapidOCR's models are script-based (Latin covers en/es/fr/pt/…); the language hint is used for
        # Tesseract, so both engines accept the Tesseract language codes installed in the image.
        import pytesseract

        return frozenset(lang for lang in pytesseract.get_languages(config="") if lang != "osd")
    return frozenset()


@lru_cache
def get_engine(name: str | None = None) -> OcrEngine:
    name = name or settings.default_ocr_engine
    if name not in ENGINES:
        raise ValueError(f"Unknown OCR engine '{name}'. Available: {', '.join(ENGINES)}")
    log.info("Loading OCR engine %s (pid %s)", name, os.getpid())
    return ENGINES[name]()

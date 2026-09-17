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
from app.extraction.layout import group_lines as _group_lines, xy_cut as _xy_cut

log = logging.getLogger(__name__)


@dataclass
class OcrOutput:
    text: str
    confidence: float | None  # 0..100, mean word confidence
    rotated_degrees: int = 0
    markdown: str = ""  # structure inferred from geometry (engines that expose boxes); "" -> derived from text


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


def _ordered_blocks(items: list[tuple[list, str]]) -> list[list[dict]]:
    """Deskew by the median text-line angle, then split columns/blocks with XY-cut (benchmarked algorithm)."""
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
    return [block for block in _xy_cut(boxes, median_h) if block]


def reading_order(items: list[tuple[list, str]]) -> str:
    """Order detector boxes (4 corner points + text) the way a person reads the page."""
    if not items:
        return ""
    return "\n".join(_group_lines(block) for block in _ordered_blocks(items))


def reading_order_markdown(items: list[tuple[list, str]]) -> tuple[str, str]:
    """(text, markdown). Lines clearly taller than the page's body text become headings; bullets become list items."""
    from app.extraction.layout import line_records, merge_wrapped_headings
    from app.extraction.markdown import BULLET_RE, NUMBERED_RE

    if not items:
        return "", ""
    blocks = [line_records(block) for block in _ordered_blocks(items)]
    heights = [r["height"] for block in blocks for r in block]
    body = statistics.median(heights) or 1
    heading_heights = sorted({round(h / body, 1) for h in heights if h >= body * 1.35}, reverse=True)[:3]
    parts: list[str] = []
    for block in blocks:
        paragraph: list[str] = []
        for record in block:
            text = record["text"].strip()
            ratio = round(record["height"] / body, 1)
            if ratio in heading_heights and len(text) <= 90:
                if paragraph:
                    parts.append("\n".join(paragraph))
                    paragraph = []
                parts.append(f"{'#' * (heading_heights.index(ratio) + 1)} {text}")
            elif NUMBERED_RE.match(text) or BULLET_RE.match(text):
                if paragraph:
                    parts.append("\n".join(paragraph))
                    paragraph = []
                numbered = NUMBERED_RE.match(text)
                parts.append(f"{numbered.group(1)}. {text[numbered.end():]}" if numbered
                             else "- " + BULLET_RE.sub("", text, count=1))
            else:
                paragraph.append(text)
        if paragraph:
            parts.append("\n".join(paragraph))
    # Tiny XY-cut blocks (cells of a table the OCR can't structure) read better as consecutive lines than as
    # dozens of one-word paragraphs.
    compact: list[str] = []
    for part in parts:
        short = len(part) < 60 and "\n" not in part and not part.startswith(("#", "- ")) and not NUMBERED_RE.match(part)
        if compact and short and _is_short_run(compact[-1]):
            compact[-1] += "\n" + part
        else:
            compact.append(part)
    text = "\n".join(_group_lines_from_records(block) for block in blocks)
    return text, "\n\n".join(merge_wrapped_headings(compact))


def _is_short_run(part: str) -> bool:
    return not part.startswith(("#", "- ")) and all(len(line) < 60 for line in part.split("\n"))


def _group_lines_from_records(records: list[dict]) -> str:
    return "\n".join(r["text"] for r in records)


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
        text, markdown = reading_order_markdown(items)
        return OcrOutput(text=text, confidence=confidence, markdown=markdown), vertical

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

"""Generate a deterministic benchmark dataset with ground truth.

Outputs (under data/generated/):
  images/*.png|jpg      OCR inputs (clean and degraded renders)
  pdf_text/*.pdf        born-digital PDFs (text layer present)
  pdf_scanned/*.pdf     image-only PDFs built from rendered pages
  real/*.png            rasterized pages of the assignment PDF (data/real/U1T02.pdf)
  gt/*.txt              ground-truth text per sample
  manifest.json         list of samples with category, kind and paths
"""

from __future__ import annotations

import io
import json
import random
import shutil
from pathlib import Path

import fitz  # PyMuPDF (used only for rasterizing the real PDF and building scanned PDFs)
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

SEED = 1234
ROOT = Path(__file__).parent
OUT = ROOT / "data" / "generated"
REAL_PDF = ROOT / "data" / "real" / "U1T02.pdf"

FONTS = {
    "dejavu": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "liberation_serif": "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "liberation_sans": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
}

EN = [
    "Real services rarely process a request synchronously. Documents arrive faster than they can be "
    "processed, and users cannot hold a connection open while heavy work runs in the background. "
    "A reliable design accepts the upload, stores it, queues the work and lets users track progress.",
    "The warehouse received 1,250 units of product SKU-4471 on March 3, 2024. Quality control rejected "
    "37 units because of damaged packaging, and the remaining stock was moved to aisle 14, shelf B.",
    "Optical character recognition converts images of text into machine readable characters. Accuracy "
    "depends on resolution, contrast, skew and noise, so preprocessing often matters as much as the model.",
    "Please send the signed contract to legal@example.com before Friday. If you have questions about "
    "clause 7.2, call our office at +1 (415) 555-0199 between 9:00 and 17:30 Pacific time.",
    "Our quarterly revenue grew 18.4% year over year, reaching $2.37 million, while operating costs "
    "decreased by 6%. The board approved a new hiring plan for the engineering and support teams.",
]

ES = [
    "Los servicios reales rara vez procesan una solicitud de forma síncrona. ¿Qué ocurre cuando llegan "
    "más documentos de los que el sistema puede atender? La solución es aceptar el archivo, guardarlo "
    "y encolar el trabajo para procesarlo después.",
    "El niño comió piñatas de azúcar en la fiesta de cumpleaños de su abuela, en Mérida, Yucatán. "
    "¡Qué día tan bonito! Después caminaron por el malecón y compraron café y pan dulce.",
    "La facturación electrónica exige que cada comprobante incluya el RFC del emisor, la fecha de "
    "expedición, el régimen fiscal y el uso del CFDI. Los errores más comunes están en el código postal.",
    "Estimada señora Núñez: le confirmamos que su pedido número 88213 será entregado el próximo "
    "miércoles 12 de junio. Si necesita cambiar la dirección, escríbanos a atención@tienda.mx.",
    "La inteligencia de documentos combina extracción de texto, reconocimiento óptico de caracteres y "
    "análisis de metadatos. Así, un archivo escaneado se convierte en información útil y consultable.",
]

INVOICE_ITEMS = [
    ("Consulting services (April)", 12, 85.00),
    ("Cloud hosting - standard plan", 1, 249.99),
    ("Licencia de software anual", 3, 1200.50),
    ("Soporte técnico prioritario", 5, 64.75),
    ("USB-C docking station", 2, 139.90),
    ("Capacitación en línea", 8, 45.00),
    ("Data migration fee", 1, 780.00),
]


def invoice_lines(rng: random.Random) -> list[str]:
    number = rng.randint(10000, 99999)
    day, month = rng.randint(1, 28), rng.randint(1, 12)
    lines = [
        f"INVOICE / FACTURA No. INV-{number}",
        f"Date: {day:02d}/{month:02d}/2024   Due: {day:02d}/{(month % 12) + 1:02d}/2024",
        "Bill to: Comercializadora del Sureste S.A. de C.V.",
        "Email: cuentas.por.pagar@sureste.com.mx   Tel: +52 999 123 4567",
    ]
    subtotal = 0.0
    for desc, qty, price in rng.sample(INVOICE_ITEMS, 4):
        total = qty * price
        subtotal += total
        lines.append(f"{desc}   {qty}   ${price:,.2f}   ${total:,.2f}")
    tax = subtotal * 0.16
    lines += [f"Subtotal: ${subtotal:,.2f}", f"IVA 16%: ${tax:,.2f}", f"Total: ${subtotal + tax:,.2f} MXN"]
    return lines


# ---------------------------------------------------------------- image rendering

def wrap(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if font.getlength(candidate) <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def render_lines(lines: list[str], font_path: str, size: int, width: int = 1800, margin: int = 90) -> Image.Image:
    font = ImageFont.truetype(font_path, size)
    line_h = int(size * 1.45)
    height = margin * 2 + line_h * len(lines)
    img = Image.new("L", (width + margin * 2, height), 255)
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((margin, margin + i * line_h), line, font=font, fill=0)
    return img


def render_paragraphs(paragraphs: list[str], font_path: str, size: int, width: int = 1800) -> tuple[Image.Image, str]:
    font = ImageFont.truetype(font_path, size)
    lines: list[str] = []
    for p in paragraphs:
        lines += wrap(p, font, width)
        lines.append("")
    lines = lines[:-1]
    return render_lines(lines, font_path, size, width), "\n".join(paragraphs)


def render_two_columns(paragraphs: list[str], font_path: str, size: int) -> tuple[Image.Image, str]:
    col_w, gap, margin = 850, 100, 90
    font = ImageFont.truetype(font_path, size)
    half = (len(paragraphs) + 1) // 2
    cols = [paragraphs[:half], paragraphs[half:]]
    col_lines = []
    for col in cols:
        lines: list[str] = []
        for p in col:
            lines += wrap(p, font, col_w) + [""]
        col_lines.append(lines[:-1])
    line_h = int(size * 1.45)
    height = margin * 2 + line_h * max(len(c) for c in col_lines)
    img = Image.new("L", (margin * 2 + col_w * 2 + gap, height), 255)
    draw = ImageDraw.Draw(img)
    for c, lines in enumerate(col_lines):
        x = margin + c * (col_w + gap)
        for i, line in enumerate(lines):
            draw.text((x, margin + i * line_h), line, font=font, fill=0)
    return img, "\n".join(cols[0] + cols[1])


def degrade(img: Image.Image, kind: str, rng: random.Random, nrng: np.random.Generator) -> Image.Image:
    if kind == "blur":
        return img.filter(ImageFilter.GaussianBlur(radius=3.0))
    if kind == "noise":
        arr = np.asarray(img, dtype=np.float32)
        arr = arr + nrng.normal(0, 60, arr.shape)
        # salt & pepper speckles
        mask = nrng.random(arr.shape)
        arr[mask < 0.02] = 0
        arr[mask > 0.98] = 255
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    if kind == "rotated":
        angle = rng.choice([-1, 1]) * rng.uniform(1.0, 4.0)
        return img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
    if kind == "lowres":  # ~120 dpi equivalent of a 300 dpi render
        w, h = img.size
        return img.resize((int(w * 0.4), int(h * 0.4)), Image.LANCZOS)
    if kind == "photo":  # phone photo: perspective, uneven lighting with a shadow, low contrast, blur
        w, h = img.size
        pad = int(0.06 * w)
        canvas = Image.new("L", (w + 2 * pad, h + 2 * pad), 255)
        canvas.paste(img, (pad, pad))
        W, H = canvas.size
        j = lambda: rng.uniform(0, 0.05) * W  # noqa: E731
        quad = (j(), j() * 0.5, j(), H - j() * 0.5, W - j(), H - j() * 0.5, W - j(), j() * 0.5)
        canvas = canvas.transform((W, H), Image.QUAD, quad, resample=Image.BICUBIC, fillcolor=255)
        arr = np.asarray(canvas, dtype=np.float32) / 255.0
        xx = np.linspace(0, 1, W)[None, :]
        yy = np.linspace(0, 1, H)[:, None]
        light = 0.62 + 0.3 * xx * (0.7 + 0.3 * yy)          # light falls off to the left
        shadow = 1.0 - 0.35 * np.exp(-((xx - 0.25) ** 2) / 0.02) * (yy > 0.5)  # hand/phone shadow
        paper = light * shadow
        ink = 0.3
        gray = ink * paper + (paper - ink * paper) * arr
        gray = gray + nrng.normal(0, 0.04, gray.shape)
        rgb = np.stack([gray * 1.0, gray * 0.95, gray * 0.82], axis=-1)
        out = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8), "RGB")
        out = out.resize((int(W * 0.6), int(H * 0.6)), Image.BILINEAR)
        return out.filter(ImageFilter.GaussianBlur(radius=1.0))
    if kind == "inverted":  # slide-like: every other paragraph is white text on a colored band
        rgb = img.convert("RGB")
        arr = np.asarray(rgb).copy()
        rows = np.where((np.asarray(img) < 128).any(axis=1))[0]
        if len(rows):
            mid = rows[0] + (rows[-1] - rows[0]) // 2
            band = slice(max(rows[0] - 30, 0), mid)
            region = arr[band]
            dark = region[..., 0] < 128
            region[:] = (32, 72, 140)
            region[dark] = (255, 255, 255)
            arr[band] = region
        return Image.fromarray(arr)
    raise ValueError(kind)


def save_jpeg(img: Image.Image, path: Path, quality: int) -> None:
    img.convert("RGB").save(path, "JPEG", quality=quality)


# ---------------------------------------------------------------- PDFs (born digital)

def register_fonts() -> None:
    pdfmetrics.registerFont(TTFont("DejaVu", FONTS["dejavu"]))
    pdfmetrics.registerFont(TTFont("LibSerif", FONTS["liberation_serif"]))


def pdf_single(path: Path, paragraphs: list[str]) -> str:
    style = ParagraphStyle("body", fontName="LibSerif", fontSize=11, leading=15)
    doc = SimpleDocTemplate(str(path), pagesize=A4, title="Single column sample", author="benchmark")
    story = []
    for p in paragraphs:
        story += [Paragraph(p, style), Spacer(1, 10)]
    doc.build(story)
    return "\n".join(paragraphs)


def pdf_two_column(path: Path, paragraphs: list[str]) -> str:
    style = ParagraphStyle("body", fontName="DejaVu", fontSize=9.5, leading=13)
    doc = BaseDocTemplate(str(path), pagesize=A4)
    w, h = A4
    margin, gap = 2 * cm, 0.8 * cm
    col_w = (w - 2 * margin - gap) / 2
    frames = [Frame(margin, margin, col_w, h - 2 * margin, id="c1"),
              Frame(margin + col_w + gap, margin, col_w, h - 2 * margin, id="c2")]
    doc.addPageTemplates([PageTemplate(id="two", frames=frames)])
    story = []
    for p in paragraphs:
        story += [Paragraph(p, style), Spacer(1, 8)]
    doc.build(story)
    return "\n".join(paragraphs)


def pdf_table(path: Path, rng: random.Random) -> str:
    header = ["Description", "Qty", "Unit price", "Amount"]
    rows = [header]
    for desc, qty, price in rng.sample(INVOICE_ITEMS, 6):
        rows.append([desc, str(qty), f"${price:,.2f}", f"${qty * price:,.2f}"])
    table = Table(rows, colWidths=[8 * cm, 2 * cm, 3 * cm, 3 * cm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "DejaVu"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, "#888888"),
        ("BACKGROUND", (0, 0), (-1, 0), "#DDDDDD"),
    ]))
    title_style = ParagraphStyle("t", fontName="DejaVu", fontSize=14, leading=18)
    title = "Factura / Invoice INV-20931"
    SimpleDocTemplate(str(path), pagesize=A4).build([Paragraph(title, title_style), Spacer(1, 12), table])
    return "\n".join([title] + [" ".join(r) for r in rows])


def pdf_multipage(path: Path, rng: random.Random, pages: int = 20) -> str:
    style = ParagraphStyle("body", fontName="DejaVu", fontSize=10.5, leading=14.5)
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    story, gt = [], []
    corpus = EN + ES
    # ~ 4,000 characters per A4 page at this font size
    while sum(len(p) for p in gt) < pages * 3600:
        p = rng.choice(corpus)
        gt.append(p)
        story += [Paragraph(p, style), Spacer(1, 8)]
    doc.build(story)
    return "\n".join(gt)


def _canvas_lines(text: str, font: str, size: float, width: float) -> list[str]:
    from reportlab.lib.utils import simpleSplit
    return simpleSplit(text, font, size, width)


def pdf_out_of_order(path: Path, paragraphs: list[str]) -> str:
    """Two columns whose content stream is written in a different order than the visual reading order
    (footer first, right column, left column, title last) - common in PDFs exported from design tools."""
    from reportlab.pdfgen import canvas as rl_canvas

    w, h = A4
    margin, gap, size, leading = 2 * cm, 0.8 * cm, 10, 14
    col_w = (w - 2 * margin - gap) / 2
    half = (len(paragraphs) + 1) // 2
    cols = [paragraphs[:half], paragraphs[half:]]
    title, footer = "Informe trimestral / Quarterly report", "Page 1 of 1 - Confidential"
    c = rl_canvas.Canvas(str(path), pagesize=A4)
    c.setFont("DejaVu", 9)
    c.drawString(margin, margin - 0.8 * cm, footer)
    c.setFont("DejaVu", size)
    for col_index in (1, 0):
        y = h - margin - 1.5 * cm
        x = margin + col_index * (col_w + gap)
        for p in cols[col_index]:
            for line in _canvas_lines(p, "DejaVu", size, col_w):
                c.drawString(x, y, line)
                y -= leading
            y -= leading * 0.6
    c.setFont("DejaVu", 16)
    c.drawString(margin, h - margin, title)
    c.showPage()
    c.save()
    return "\n".join([title] + cols[0] + cols[1] + [footer])


def pdf_char_positioned(path: Path, paragraphs: list[str]) -> str:
    """Every glyph is placed individually and spaces are never emitted (only a gap in x) - typical of
    PDFs produced by some reporting tools and print drivers; the extractor must infer word breaks."""
    from reportlab.pdfgen import canvas as rl_canvas

    w, h = A4
    margin, size, leading = 2 * cm, 11, 15
    c = rl_canvas.Canvas(str(path), pagesize=A4)
    c.setFont("LibSerif", size)
    y = h - margin
    for p in paragraphs:
        for line in _canvas_lines(p, "LibSerif", size, w - 2 * margin):
            x = margin
            for ch in line:
                if ch != " ":
                    c.drawString(x, y, ch)
                x += pdfmetrics.stringWidth(ch, "LibSerif", size)
            y -= leading
        y -= leading * 0.6
    c.showPage()
    c.save()
    return "\n".join(paragraphs)


def pdf_rotated(path: Path, paragraphs: list[str]) -> str:
    """Landscape content on a page with /Rotate 90 (scanner/driver output)."""
    from reportlab.pdfgen import canvas as rl_canvas

    w, h = A4
    margin, size, leading = 2 * cm, 11, 15
    c = rl_canvas.Canvas(str(path), pagesize=A4)
    c.setPageRotation(90)  # reportlab stores a landscape MediaBox (h x w) for rotated pages
    c.setFont("DejaVu", size)
    y = w - margin
    for p in paragraphs:
        for line in _canvas_lines(p, "DejaVu", size, h - 2 * margin):
            c.drawString(margin, y, line)
            y -= leading
        y -= leading * 0.6
    c.showPage()
    c.save()
    return "\n".join(paragraphs)


def images_to_pdf(images: list[Image.Image], path: Path, dpi: int = 300) -> None:
    doc = fitz.open()
    for img in images:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=85)
        w_pt, h_pt = img.width * 72 / dpi, img.height * 72 / dpi
        page = doc.new_page(width=w_pt, height=h_pt)
        page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(path)


# ---------------------------------------------------------------- main

def main() -> None:
    rng = random.Random(SEED)
    nrng = np.random.default_rng(SEED)
    if OUT.exists():
        shutil.rmtree(OUT)
    for sub in ("images", "pdf_text", "pdf_scanned", "real", "gt"):
        (OUT / sub).mkdir(parents=True)
    register_fonts()
    manifest: list[dict] = []

    def add(sample_id: str, kind: str, category: str, path: Path, gt: str, **extra) -> None:
        gt_path = OUT / "gt" / f"{sample_id}.txt"
        gt_path.write_text(gt, encoding="utf-8")
        manifest.append({"id": sample_id, "kind": kind, "category": category,
                         "path": str(path.relative_to(ROOT)), "gt": str(gt_path.relative_to(ROOT)), **extra})

    font_cycle = list(FONTS.values())
    samples_per_category = 3
    base_pages: list[Image.Image] = []

    # --- images: clean + degraded variants from the same kind of content
    for i in range(samples_per_category):
        lang_pool = EN if i % 2 == 0 else ES
        paragraphs = rng.sample(lang_pool, 2)
        font = font_cycle[i % len(font_cycle)]
        clean, gt = render_paragraphs(paragraphs, font, 42)  # ~10 pt at 300 dpi
        base_pages.append(clean)
        p = OUT / "images" / f"clean_{i}.png"
        clean.save(p)
        add(f"clean_{i}", "image", "clean", p, gt)
        for kind in ("blur", "noise", "rotated", "lowres", "photo", "inverted"):
            img = degrade(clean, kind, rng, nrng)
            ext = "jpg" if kind == "photo" else "png"
            p = OUT / "images" / f"{kind}_{i}.{ext}"
            if ext == "jpg":
                save_jpeg(img, p, 80)
            else:
                img.save(p)
            add(f"{kind}_{i}", "image", kind, p, gt)
        p = OUT / "images" / f"jpeg_q15_{i}.jpg"
        save_jpeg(clean.resize((int(clean.width * 0.5), int(clean.height * 0.5)), Image.LANCZOS), p, 15)
        add(f"jpeg_q15_{i}", "image", "jpeg_q15", p, gt)

    # --- layout variants
    for i in range(samples_per_category):
        paragraphs = rng.sample(EN + ES, 4)
        img, gt = render_two_columns(paragraphs, font_cycle[i % 3], 36)
        p = OUT / "images" / f"two_column_{i}.png"
        img.save(p)
        add(f"two_column_{i}", "image", "two_column", p, gt)

        paragraphs = rng.sample(EN + ES, 3)
        img, gt = render_paragraphs(paragraphs, font_cycle[(i + 1) % 3], 25)  # ~6 pt at 300 dpi
        p = OUT / "images" / f"small_font_{i}.png"
        img.save(p)
        add(f"small_font_{i}", "image", "small_font", p, gt)

        lines = invoice_lines(rng)
        img = render_lines(lines, font_cycle[i % 3], 40)
        p = OUT / "images" / f"invoice_{i}.png"
        img.save(p)
        add(f"invoice_{i}", "image", "invoice", p, "\n".join(lines))

    # --- scanned PDFs (image only), 2 pages each: clean page + noisy page
    for i in range(samples_per_category):
        page_a = base_pages[i]
        page_b = degrade(base_pages[(i + 1) % len(base_pages)], "noise", rng, nrng)
        p = OUT / "pdf_scanned" / f"scanned_{i}.pdf"
        images_to_pdf([page_a, page_b], p)
        gt = (OUT / "gt" / f"clean_{i}.txt").read_text() + "\n" + \
             (OUT / "gt" / f"clean_{(i + 1) % len(base_pages)}.txt").read_text()
        add(f"scanned_{i}", "pdf", "scanned_pdf", p, gt, pages=2)

    # --- born-digital PDFs
    for i in range(2):
        p = OUT / "pdf_text" / f"single_{i}.pdf"
        add(f"pdf_single_{i}", "pdf", "pdf_single", p, pdf_single(p, rng.sample(EN + ES, 5)), pages=1)
        p = OUT / "pdf_text" / f"two_column_{i}.pdf"
        add(f"pdf_two_column_{i}", "pdf", "pdf_two_column", p, pdf_two_column(p, rng.sample(EN + ES, 8)), pages=1)
        p = OUT / "pdf_text" / f"table_{i}.pdf"
        add(f"pdf_table_{i}", "pdf", "pdf_table", p, pdf_table(p, rng), pages=1)
        p = OUT / "pdf_text" / f"out_of_order_{i}.pdf"
        add(f"pdf_out_of_order_{i}", "pdf", "pdf_out_of_order", p, pdf_out_of_order(p, rng.sample(EN + ES, 6)))
        p = OUT / "pdf_text" / f"char_positioned_{i}.pdf"
        add(f"pdf_char_positioned_{i}", "pdf", "pdf_char_positioned", p, pdf_char_positioned(p, rng.sample(EN + ES, 4)))
        p = OUT / "pdf_text" / f"rotated_{i}.pdf"
        add(f"pdf_rotated_{i}", "pdf", "pdf_rotated", p, pdf_rotated(p, rng.sample(EN + ES, 4)))
    p = OUT / "pdf_text" / "multipage.pdf"
    gt = pdf_multipage(p, rng)
    add("pdf_multipage", "pdf", "pdf_multipage", p, gt, pages=fitz.open(p).page_count)
    for s in manifest:
        if s["kind"] == "pdf" and s["category"] != "scanned_pdf":
            s["pages"] = fitz.open(ROOT / s["path"]).page_count

    # --- real document: the assignment PDF. Its text layer is the reference for OCR on its rasters.
    if REAL_PDF.exists():
        doc = fitz.open(REAL_PDF)
        for page in doc:
            ref = page.get_text("text", sort=True)
            for dpi in (150, 300):
                pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
                p = OUT / "real" / f"u1t02_p{page.number + 1}_{dpi}dpi.png"
                pix.save(p)
                add(f"real_p{page.number + 1}_{dpi}", "image", f"real_{dpi}dpi", p, ref)

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    counts: dict[str, int] = {}
    for s in manifest:
        counts[s["category"]] = counts.get(s["category"], 0) + 1
    print(f"Generated {len(manifest)} samples:", json.dumps(counts))


if __name__ == "__main__":
    main()

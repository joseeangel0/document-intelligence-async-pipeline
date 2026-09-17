"""Generate a deterministic STRUCTURE dataset: documents whose Markdown structure is known.

Every document is described once as a list of blocks (headings, paragraphs, bullet lists, tables),
then rendered to PDF (reportlab), DOCX (python-docx) or images, and the same block list is serialized
as ground-truth Markdown. Scoring therefore checks headings (with level), list items, table cells
and text, not only characters.

Outputs (under data/structure/):
  pdf/*.pdf, scanned/*.pdf, images/*.png, docx/*.docx, real/*    inputs
  gt/*.json   {"markdown", "plain", "headings": [[level, text]], "list_items": [...], "tables": [[[cell]]]}
  manifest.json
"""

from __future__ import annotations

import io
import json
import random
import shutil
from pathlib import Path

import fitz  # PyMuPDF: rasterization for scanned variants and page counts
import numpy as np
from PIL import Image, ImageFilter
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, ListFlowable, ListItem, NextPageTemplate, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)

SEED = 4321
ROOT = Path(__file__).parent
OUT = ROOT / "data" / "structure"
REAL_SRC = ROOT / "data" / "real_structure"  # optional real documents (see README)

pdfmetrics.registerFont(TTFont("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))

# ---------------------------------------------------------------- document specs (block lists)


def report_en() -> list:
    rng = random.Random(SEED)
    components = ["API gateway", "OCR worker", "Object storage", "Broker", "Database", "Scheduler"]
    severities = ["Low", "Medium", "High", "Critical"]
    resolutions = ["Restarted service", "Scaled workers", "Rotated credentials", "Patched parser",
                   "Increased memory limit", "Re-queued jobs", "Replaced disk", "Updated certificate"]
    incidents = [["ID", "Date", "Component", "Severity", "Resolution"]]
    for i in range(1, 61):
        incidents.append([f"INC-{1000 + i}", f"2026-{(i % 9) + 1:02d}-{(i * 7) % 27 + 1:02d}",
                          rng.choice(components), rng.choice(severities), rng.choice(resolutions)])
    return [
        ("h", 1, "Quarterly Operations Report"),
        ("p", "This report summarizes the document processing platform during the third quarter. It covers "
              "volume, regional teams and the incidents that affected availability."),
        ("h", 2, "1. Summary"),
        ("p", "The platform processed more documents than in any previous quarter while keeping the failure "
              "rate below the agreed service level."),
        ("ul", ["Processed 1.2 million pages across all tenants",
                "Median time to first result fell to 4.1 seconds",
                "Two critical incidents were resolved within the target window",
                "Storage costs decreased by 12 percent after lifecycle rules"]),
        ("h", 2, "2. Processing volume"),
        ("h", 3, "2.1 Documents by type"),
        ("table", "grid", [["Type", "Documents", "Pages", "Avg time (s)"],
                           ["PDF (digital)", "182,400", "951,220", "0.8"],
                           ["PDF (scanned)", "41,730", "228,915", "38.2"],
                           ["Images", "65,110", "65,110", "1.4"],
                           ["Word", "22,905", "110,340", "0.3"],
                           ["Spreadsheets", "8,412", "20,101", "0.5"],
                           ["Email", "15,066", "15,066", "0.2"]]),
        ("p", "Scanned PDFs remain the most expensive category because every page requires OCR."),
        ("h", 3, "2.2 Regional teams"),
        ("table", "borderless", [["Region", "Lead", "Headcount", "Budget (USD)"],
                                 ["North America", "A. Johnson", "14", "1,240,000"],
                                 ["Latin America", "M. Hernández", "9", "610,000"],
                                 ["Europe", "K. Müller", "11", "980,000"],
                                 ["Asia Pacific", "S. Tanaka", "7", "540,000"]]),
        ("h", 2, "3. Incident log"),
        ("p", "The following table lists every incident opened during the quarter, ordered by identifier."),
        ("table", "long", incidents),
        ("h", 2, "4. Next steps"),
        ("ul", ["Split large PDFs into page-range subtasks",
                "Offer Markdown and chunked output for retrieval pipelines",
                "Add antivirus scanning before processing"]),
        ("p", "The operations team will review these actions at the next monthly meeting."),
    ]


def informe_es() -> list:
    return [
        ("h", 1, "Informe de digitalización documental"),
        ("p", "Este informe describe el proyecto de digitalización de expedientes de la universidad y los "
              "resultados obtenidos durante el primer semestre."),
        ("h", 2, "Resumen ejecutivo"),
        ("p", "Se digitalizaron más de cuarenta mil expedientes con una tasa de error menor al dos por ciento."),
        ("ul", ["Reducción del tiempo de búsqueda de días a minutos",
                "Expedientes disponibles para asistentes basados en IA",
                "Cumplimiento de la política de protección de datos"]),
        ("pagebreak_columns",),
        ("h", 2, "Metodología"),
        ("p", "Cada expediente se escaneó a doscientos puntos por pulgada. Las páginas con baja calidad se "
              "volvieron a capturar antes de continuar con el reconocimiento óptico de caracteres."),
        ("p", "El texto extraído se revisó mediante muestreo aleatorio. Los revisores compararon cada página con "
              "el documento original y registraron las diferencias encontradas en una bitácora compartida."),
        ("h", 3, "Muestreo"),
        ("p", "Se eligió una muestra estratificada por área académica para que cada facultad tuviera la misma "
              "representación en la evaluación de calidad."),
        ("ul", ["Tamaño de muestra de mil doscientas páginas",
                "Nivel de confianza del noventa y cinco por ciento",
                "Revisión doble en páginas con tablas"]),
        ("p", "Las discrepancias se clasificaron como omisiones, sustituciones o errores de orden de lectura, "
              "lo que permitió priorizar las mejoras del proceso de captura en las siguientes semanas."),
        ("h", 3, "Validación"),
        ("p", "Un segundo equipo verificó de manera independiente una submuestra de doscientas páginas. La "
              "concordancia entre ambos equipos superó el noventa y ocho por ciento en todas las facultades."),
        ("p", "Cuando los revisores no coincidían, un especialista en archivo tomaba la decisión final y la "
              "registraba junto con una breve justificación que después sirvió para capacitar al personal."),
        ("p", "Las páginas con sellos, firmas o anotaciones manuscritas se marcaron para revisión manual, ya que el "
              "reconocimiento automático no garantiza una lectura confiable de esos elementos."),
        ("ul", ["Registro de cada decisión en la bitácora", "Capacitación mensual del personal de captura",
                "Revisión trimestral de los criterios de calidad"]),
        ("p", "Con estos controles, el proceso quedó documentado y puede repetirse en otras dependencias de la "
              "universidad que deseen digitalizar sus archivos históricos."),
        ("p", "El equipo recomienda mantener el mismo procedimiento para los expedientes que se reciban en el "
              "futuro, de modo que toda la colección conserve una calidad homogénea."),
        ("pagebreak_single",),
        ("h", 2, "Resultados"),
        ("table", "grid", [["Área", "Expedientes", "Páginas", "Errores"],
                           ["Ingeniería", "12,480", "96,310", "1.2%"],
                           ["Medicina", "9,125", "88,904", "1.9%"],
                           ["Derecho", "7,770", "61,002", "0.8%"],
                           ["Arquitectura", "5,310", "30,447", "1.5%"],
                           ["Contaduría", "6,915", "41,218", "1.1%"]]),
        ("p", "Los errores se concentraron en expedientes antiguos escritos a máquina y con papel deteriorado."),
    ]


def invoice() -> list:
    items = [["Qty", "Description", "Unit price", "Amount"],
             ["1,200", "OCR processing (pages)", "$3.00", "$3,600.00"],
             ["50", "Object storage (GB-month)", "$5.00", "$250.00"],
             ["1", "Priority support", "$1,150.00", "$1,150.00"],
             ["3", "Custom extraction template", "$400.00", "$1,200.00"],
             ["10", "Webhook endpoints", "$12.00", "$120.00"],
             ["2", "Additional OCR language pack", "$75.00", "$150.00"],
             ["500", "Markdown conversion (pages)", "$0.40", "$200.00"],
             ["1", "Onboarding workshop", "$900.00", "$900.00"]]
    return [
        ("h", 1, "Invoice INV-2026-0142"),
        ("p", "ACME Data Services S.A. de C.V. · Mérida, Yucatán · billing@acme.example.mx"),
        ("p", "Bill to: Universidad Tecnológica · Date: 2026-09-17 · Due: 2026-10-17"),
        ("h", 2, "Line items"),
        ("table", "grid", items),
        ("h", 2, "Totals"),
        ("table", "borderless", [["Concept", "Amount"], ["Subtotal", "$7,570.00"], ["IVA 16%", "$1,211.20"],
                                 ["Total", "$8,781.20"]]),
        ("p", "Payment by bank transfer within 30 days. Thank you for your business."),
    ]


def report_docx_en() -> list:
    return [
        ("h", 1, "Platform Migration Plan"),
        ("p", "This document describes how the document intelligence platform moves to the new cluster."),
        ("h", 2, "Goals"),
        ("ul", ["Zero lost jobs during the migration", "Less than five minutes of upload downtime",
                "Keep every stored document and result"]),
        ("h", 2, "Timeline"),
        ("table", "grid", [["Phase", "Start", "End", "Owner"],
                           ["Preparation", "2026-10-01", "2026-10-07", "Platform team"],
                           ["Data copy", "2026-10-08", "2026-10-10", "Storage team"],
                           ["Cut-over", "2026-10-11", "2026-10-11", "On-call engineer"],
                           ["Clean-up", "2026-10-12", "2026-10-20", "Platform team"]]),
        ("h", 3, "Rollback"),
        ("p", "If the error rate doubles after the cut-over, traffic returns to the old cluster within ten minutes."),
        ("h", 2, "Risks"),
        ("table", "grid", [["Risk", "Likelihood", "Mitigation"],
                           ["Queue backlog", "Medium", "Scale OCR workers before cut-over"],
                           ["Certificate expiry", "Low", "Renew certificates one week earlier"]]),
    ]


def informe_docx_es() -> list:
    return [
        ("h", 1, "Política de retención de documentos"),
        ("p", "La presente política define cuánto tiempo se conservan los documentos procesados por la plataforma."),
        ("h", 2, "Alcance"),
        ("ul", ["Documentos originales cargados por los usuarios", "Texto extraído y archivos Markdown",
                "Fragmentos indexados para búsqueda semántica"]),
        ("h", 2, "Plazos de conservación"),
        ("table", "grid", [["Tipo de dato", "Plazo", "Responsable"],
                           ["Documento original", "5 años", "Archivo general"],
                           ["Texto extraído", "5 años", "Equipo de datos"],
                           ["Índices de búsqueda", "1 año", "Equipo de IA"],
                           ["Bitácoras técnicas", "90 días", "Operaciones"]]),
        ("h", 2, "Excepciones"),
        ("p", "Los documentos sujetos a un proceso legal se conservan hasta que el área jurídica autorice su baja."),
    ]


# ---------------------------------------------------------------- ground truth


def ground_truth(blocks: list) -> dict:
    md, plain, headings, items, tables = [], [], [], [], []
    for block in blocks:
        kind = block[0]
        if kind == "h":
            _, level, text = block
            md.append("#" * level + " " + text)
            plain.append(text)
            headings.append([level, text])
        elif kind == "p":
            md.append(block[1])
            plain.append(block[1])
        elif kind == "ul":
            md.append("\n".join(f"- {t}" for t in block[1]))
            plain.extend(block[1])
            items.extend(block[1])
        elif kind == "table":
            rows = block[2]
            lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
            lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
            md.append("\n".join(lines))
            plain.extend(" ".join(r) for r in rows)
            tables.append(rows)
    return {"markdown": "\n\n".join(md) + "\n", "plain": "\n".join(plain), "headings": headings,
            "list_items": items, "tables": tables}


# ---------------------------------------------------------------- renderers


STYLES = {
    "h1": ParagraphStyle("h1", fontName="DejaVu-Bold", fontSize=20, leading=25, spaceBefore=4, spaceAfter=10),
    "h2": ParagraphStyle("h2", fontName="DejaVu-Bold", fontSize=15, leading=19, spaceBefore=12, spaceAfter=6),
    "h3": ParagraphStyle("h3", fontName="DejaVu-Bold", fontSize=12, leading=15, spaceBefore=8, spaceAfter=4),
    "p": ParagraphStyle("p", fontName="DejaVu", fontSize=10, leading=14, spaceAfter=6, alignment=TA_LEFT),
    "li": ParagraphStyle("li", fontName="DejaVu", fontSize=10, leading=13),
}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _table(style: str, rows: list[list[str]]) -> Table:
    table = Table(rows, repeatRows=1 if style == "long" else 0, hAlign="LEFT")
    commands = [("FONT", (0, 0), (-1, -1), "DejaVu", 9), ("FONT", (0, 0), (-1, 0), "DejaVu-Bold", 9),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3), ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10)]
    if style in ("grid", "long"):
        commands += [("GRID", (0, 0), (-1, -1), 0.6, colors.black),
                     ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8"))]
    table.setStyle(TableStyle(commands))
    return table


def render_pdf(blocks: list, path: Path, title: str) -> None:
    doc = BaseDocTemplate(str(path), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm,
                          bottomMargin=2 * cm, title=title)
    w, h = A4
    single = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="single")
    gap = 0.8 * cm
    col_w = (doc.width - gap) / 2
    left = Frame(doc.leftMargin, doc.bottomMargin, col_w, doc.height, id="left")
    right = Frame(doc.leftMargin + col_w + gap, doc.bottomMargin, col_w, doc.height, id="right")
    doc.addPageTemplates([PageTemplate("single", [single]), PageTemplate("columns", [left, right])])
    story = []
    for block in blocks:
        kind = block[0]
        if kind == "h":
            story.append(Paragraph(_escape(block[2]), STYLES[f"h{block[1]}"]))
        elif kind == "p":
            story.append(Paragraph(_escape(block[1]), STYLES["p"]))
        elif kind == "ul":
            story.append(ListFlowable([ListItem(Paragraph(_escape(t), STYLES["li"]), leftIndent=14)
                                       for t in block[1]], bulletType="bullet", start="•", leftIndent=14,
                                      bulletFontName="DejaVu", bulletFontSize=10, bulletOffsetY=0))
            story.append(Spacer(1, 6))
        elif kind == "table":
            story.append(_table(block[1], block[2]))
            story.append(Spacer(1, 8))
        elif kind == "pagebreak_columns":
            story += [NextPageTemplate("columns"), PageBreak()]
        elif kind == "pagebreak_single":
            story += [NextPageTemplate("single"), PageBreak()]
    doc.build(story)


def render_docx(blocks: list, path: Path, title: str) -> None:
    import docx

    d = docx.Document()
    d.core_properties.title = title
    for block in blocks:
        kind = block[0]
        if kind == "h":
            d.add_heading(block[2], level=block[1])
        elif kind == "p":
            d.add_paragraph(block[1])
        elif kind == "ul":
            for t in block[1]:
                d.add_paragraph(t, style="List Bullet")
        elif kind == "table":
            rows = block[2]
            table = d.add_table(rows=len(rows), cols=len(rows[0]))
            table.style = "Table Grid"
            for r, row in enumerate(rows):
                for c, value in enumerate(row):
                    table.cell(r, c).text = value
    d.save(path)


def degrade(img: Image.Image, rng: np.random.Generator, angle: float, noise_sigma: float, blur: float) -> Image.Image:
    img = img.convert("L").rotate(angle, expand=True, fillcolor=255, resample=Image.Resampling.BICUBIC)
    arr = np.asarray(img).astype(np.float32)
    arr += rng.normal(0, noise_sigma, arr.shape)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    return img.filter(ImageFilter.GaussianBlur(blur)) if blur else img


def scanned_pdf(src: Path, dst: Path, rng: np.random.Generator, dpi: int = 200) -> None:
    out = fitz.open()
    with fitz.open(src) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
            img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
            img = degrade(img, rng, angle=float(rng.uniform(-0.6, 0.6)), noise_sigma=10, blur=0.4)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            new = out.new_page(width=page.rect.width, height=page.rect.height)
            new.insert_image(new.rect, stream=buf.getvalue())
    out.save(dst)


def table_photo(rows: list[list[str]], dst: Path, rng: np.random.Generator) -> None:
    tmp = dst.with_suffix(".tmp.pdf")
    render_pdf([("table", "grid", rows)], tmp, "table")
    with fitz.open(tmp) as doc:
        page = doc[0]
        blocks = page.get_text("blocks")
        x0 = min(b[0] for b in blocks) - 12
        y0 = min(b[1] for b in blocks) - 12
        x1 = max(b[2] for b in blocks) + 24
        y1 = max(b[3] for b in blocks) + 12
        pix = page.get_pixmap(dpi=220, clip=fitz.Rect(x0, y0, x1, y1), colorspace=fitz.csGRAY)
    tmp.unlink()
    img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    # warm paper tint + uneven lighting, slight skew, blur and noise ("phone photo")
    img = degrade(img, rng, angle=1.8, noise_sigma=14, blur=0.8)
    arr = np.asarray(img).astype(np.float32)
    gradient = np.linspace(0.82, 1.0, arr.shape[1])[None, :]
    arr = arr * gradient
    rgb = np.stack([arr, arr * 0.96, arr * 0.88], axis=-1)
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(dst)


# ---------------------------------------------------------------- main


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    for sub in ("pdf", "scanned", "images", "docx", "gt", "real"):
        (OUT / sub).mkdir(parents=True)
    rng = np.random.default_rng(SEED)
    samples = []

    def add(sample_id: str, doc_type: str, path: Path, blocks: list | None, lang: str, gt: dict | None = None):
        gt = gt or ground_truth(blocks)
        (OUT / "gt" / f"{sample_id}.json").write_text(json.dumps(gt, ensure_ascii=False, indent=1), encoding="utf-8")
        pages = 1
        if path.suffix == ".pdf":
            with fitz.open(path) as d:
                pages = d.page_count
        samples.append({"id": sample_id, "doc_type": doc_type, "lang": lang, "path": str(path.relative_to(ROOT)),
                        "pages": pages, "scanned": doc_type in ("scanned_pdf", "table_photo"),
                        "gt": str((OUT / "gt" / f"{sample_id}.json").relative_to(ROOT))})

    specs = {"report_en": (report_en(), "en"), "informe_es": (informe_es(), "es"), "invoice": (invoice(), "en")}
    for name, (blocks, lang) in specs.items():
        path = OUT / "pdf" / f"{name}.pdf"
        render_pdf(blocks, path, name)
        add(name, "digital_pdf", path, blocks, lang)

    scanned = OUT / "scanned" / "report_en_scanned.pdf"
    scanned_pdf(OUT / "pdf" / "report_en.pdf", scanned, rng)
    add("report_en_scanned", "scanned_pdf", scanned, specs["report_en"][0], "en")

    scanned_inv = OUT / "scanned" / "invoice_scanned.pdf"
    scanned_pdf(OUT / "pdf" / "invoice.pdf", scanned_inv, rng)
    add("invoice_scanned", "scanned_pdf", scanned_inv, specs["invoice"][0], "en")

    items = specs["invoice"][0][4][2]
    photo = OUT / "images" / "table_photo.png"
    table_photo(items, photo, rng)
    add("table_photo", "table_photo", photo, [("table", "grid", items)], "en")

    for name, blocks, lang in (("docx_plan_en", report_docx_en(), "en"), ("docx_politica_es", informe_docx_es(), "es")):
        path = OUT / "docx" / f"{name}.docx"
        render_docx(blocks, path, name)
        add(name, "docx", path, blocks, lang)

    # Optional real documents (only if present in data/real_structure with a reference markdown)
    if REAL_SRC.exists():
        for src in sorted(REAL_SRC.glob("*.pdf")):
            ref = src.with_suffix(".gt.json")
            if not ref.exists():
                continue
            dst = OUT / "real" / src.name
            shutil.copy(src, dst)
            add(f"real_{src.stem}", "real_pdf", dst, None, "en", gt=json.loads(ref.read_text(encoding="utf-8")))

    (OUT / "manifest.json").write_text(json.dumps(samples, indent=2, ensure_ascii=False), encoding="utf-8")
    for s in samples:
        print(f"{s['id']:22} {s['doc_type']:12} pages={s['pages']}")


if __name__ == "__main__":
    main()

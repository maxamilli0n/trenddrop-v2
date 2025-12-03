import os
import time
import csv
from pathlib import Path
from typing import List, Dict, Optional

import requests

# Optional reportlab imports – guarded so local/CI doesn’t crash if missing
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
except Exception:
    letter = None  # type: ignore
    ImageReader = None  # type: ignore
    canvas = None  # type: ignore
    SimpleDocTemplate = None  # type: ignore
    Table = None  # type: ignore
    TableStyle = None  # type: ignore
    Paragraph = None  # type: ignore
    getSampleStyleSheet = lambda: None  # type: ignore
    colors = None  # type: ignore
    pdfmetrics = None  # type: ignore
    TTFont = None  # type: ignore


def _safe_text(val: Optional[str]) -> str:
    return (val or "").strip()


BULLET_PREFIXES = ("■", "▪", "•", "●", "◼", "◾", "▫", "◻", "●", "●")


def _strip_leading_bullet(text: str) -> str:
    if not text:
        return text
    for bullet in BULLET_PREFIXES:
        if text.startswith(bullet):
            return text[len(bullet):].lstrip()
    return text


# Register a Unicode font with star glyphs if available
DEJAVU_REGISTERED = False
try:
    if pdfmetrics is not None:
        candidate_paths = [
            os.path.join("fonts", "DejaVuSans.ttf"),
            os.path.join(os.path.dirname(__file__), "..", "fonts", "DejaVuSans.ttf"),
            os.path.join(os.path.dirname(__file__), "fonts", "DejaVuSans.ttf"),
        ]
        for fp in candidate_paths:
            if os.path.exists(fp):
                pdfmetrics.registerFont(TTFont("DejaVuSans", fp))
                DEJAVU_REGISTERED = True
                break
except Exception:
    DEJAVU_REGISTERED = False


def seller_fb_to_stars(seller_fb: Optional[object]) -> str:
    """Convert seller feedback count into a 1–5 star rating."""
    try:
        fb = int(seller_fb or 0)
    except Exception:
        fb = 0

    if fb >= 100_000:
        n = 5
    elif fb >= 50_000:
        n = 4
    elif fb >= 10_000:
        n = 3
    elif fb >= 1_000:
        n = 2
    else:
        n = 1
    return "★" * n + "☆" * (5 - n)


def _fetch_image_bytes(url: str) -> Optional[bytes]:
    try:
        r = requests.get(url, timeout=12)
        if r.status_code == 200 and r.content:
            return r.content
    except Exception:
        pass
    return None


def generate_weekly_pdf(products: List[Dict], outfile_path: str) -> None:
    """
    Build a simple “Top N” PDF: cover + one product per page
    (image, headline, price, link).
    This is used as a fallback when REPORT_LAYOUT != "table".
    """
    if canvas is None or letter is None:
        # No reportlab installed; write a very simple text fallback
        with open(outfile_path, "wb") as f:
            f.write(b"TrendDrop Weekly Report\n\n")
            for i, p in enumerate(products, start=1):
                line = (
                    f"{i}. "
                    f"{_safe_text(p.get('headline') or p.get('title'))} — "
                    f"{_safe_text(str(p.get('currency') or 'USD'))} "
                    f"{_safe_text(str(p.get('price') or ''))} "
                    f"-> {_safe_text(p.get('url'))}\n"
                ).encode("utf-8")
                f.write(line)
        return

    c = canvas.Canvas(outfile_path, pagesize=letter)
    width, height = letter

    # Cover page
    c.setFillColorRGB(0.06, 0.09, 0.16)  # dark bg
    c.rect(0, 0, width, height, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 36)
    c.drawString(72, height - 144, "TrendDrop")
    c.setFont("Helvetica", 18)
    c.drawString(72, height - 180, "Weekly Top Picks")
    c.setFont("Helvetica", 12)
    c.setFillColor(colors.whitesmoke)
    c.drawString(72, 72, time.strftime("Generated %Y-%m-%d", time.gmtime()))
    c.showPage()

    # Product pages (one per product for clarity)
    for i, p in enumerate(products, start=1):
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 18)
        title = _safe_text(p.get("headline") or p.get("title"))[:100]
        c.drawString(72, height - 90, f"#{i}  {title}")

        # Price
        c.setFont("Helvetica", 12)
        currency = _safe_text(p.get("currency") or "USD")
        price = p.get("price")
        if isinstance(price, (int, float)):
            price_text = f"{currency} {price:.2f}"
        else:
            price_text = f"{currency} {_safe_text(str(price))}"
        c.drawString(72, height - 120, price_text)

        # Image
        img_y_top = height - 140
        box_w, box_h = width - 144, 360
        img_url = _safe_text(p.get("image_url"))
        img_bytes = _fetch_image_bytes(img_url) if img_url else None
        if img_bytes:
            try:
                img = ImageReader(img_bytes)
                iw, ih = img.getSize()
                scale = min(box_w / iw, box_h / ih)
                dw, dh = iw * scale, ih * scale
                x = 72 + (box_w - dw) / 2
                y = img_y_top - dh
                c.drawImage(
                    img,
                    x,
                    y,
                    width=dw,
                    height=dh,
                    preserveAspectRatio=True,
                    mask="auto",
                )
            except Exception:
                pass

        # Link
        url = _safe_text(p.get("url"))
        if url:
            y_link = 72
            c.setFillColor(colors.blue)
            c.setFont("Helvetica", 12)
            link_text = "View product"
            c.drawString(72, y_link, link_text)
            text_w = c.stringWidth(link_text, "Helvetica", 12)
            c.linkURL(url, (72, y_link - 2, 72 + text_w, y_link + 10))
            c.setFillColor(colors.black)

        c.showPage()

    c.save()


def _value_for_column(p: Dict, key: str) -> str:
    if key == "title":
        raw = _safe_text(p.get("title") or p.get("headline"))
        return _strip_leading_bullet(raw)
    if key == "price":
        price = p.get("price")
        return f"{price:.2f}" if isinstance(price, (int, float)) else _safe_text(str(price))
    if key == "currency":
        return _safe_text(p.get("currency") or "USD")
    if key == "signals":
        sig = p.get("signals")
        try:
            return f"{float(sig):.2f}"
        except Exception:
            return _safe_text(str(sig or "0"))
    return _safe_text(str(p.get(key)))


def generate_table_pdf(
    products: List[Dict],
    outfile_path: str,
    columns: List[Dict[str, str]],
    title: Optional[str] = None,
    subtitle_lines: Optional[List[str]] = None,
) -> None:
    """
    Create a compact table PDF with dynamic columns.

    columns: list of {"key": "price", "label": "Price"}
    """
    if SimpleDocTemplate is None or Table is None or Paragraph is None:
        # Fallback: write a simple TSV file if reportlab not available
        with open(outfile_path, "wb") as f:
            headers = [c.get("label") or c.get("key") for c in columns]
            f.write(("\t".join(headers) + "\n").encode("utf-8"))
            for p in products:
                row = [_value_for_column(p, c.get("key")) for c in columns]
                f.write(("\t".join(row) + "\n").encode("utf-8"))
        return

    doc = SimpleDocTemplate(
        outfile_path,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=48,
        bottomMargin=48,
    )
    styles = getSampleStyleSheet()

    # Title + subtitle styles
    try:
        from reportlab.lib.styles import ParagraphStyle

        TitleStyle = ParagraphStyle(
            "ReportTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            alignment=1,
            spaceAfter=6,
        )
        SubTitleStyle = ParagraphStyle(
            "SubTitle",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=11,
            textColor=colors.HexColor("#666666"),
            alignment=1,
            spaceAfter=10,
        )
        TitleLink = ParagraphStyle(
            "TitleLink",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=10.5,
            textColor=colors.black,
            linkColor=colors.black,
            wordWrap="CJK",
        )
        StarCell = ParagraphStyle(
            "StarCell",
            parent=styles["BodyText"],
            fontName=("DejaVuSans" if DEJAVU_REGISTERED else "Helvetica"),
            fontSize=9,
            leading=11,
            textColor=colors.black,
            alignment=1,
        )
    except Exception:
        TitleStyle = styles["Title"]
        SubTitleStyle = styles["Normal"]
        TitleLink = styles["BodyText"]
        StarCell = styles["BodyText"]

    elements: List[object] = []

    if title:
        elements.append(Paragraph(_safe_text(title), TitleStyle))
        if subtitle_lines:
            for line in subtitle_lines:
                elements.append(Paragraph(line, SubTitleStyle))

    # Build table data
    header = [c.get("label") or c.get("key") for c in columns]
    data: List[List[object]] = [header]

    for p in products:
        row: List[object] = []
        for cdef in columns:
            key = cdef.get("key")
            val = _value_for_column(p, key)
            if key == "title":
                url = _safe_text(p.get("url"))
                if url:
                    val = Paragraph(f"<a href='{url}'>{val}</a>", TitleLink)
                else:
                    val = Paragraph(val, TitleLink)
            elif key == "signals":
                val = Paragraph(val, StarCell)
            row.append(val)
        data.append(row)

    col_widths = None
    if len(header) == 5 and header[0].lower().startswith("title"):
        col_widths = [None, 60, 45, 55, 50]

    table = Table(data, repeatRows=1, colWidths=col_widths, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6E6E6")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("ALIGN", (1, 1), (-1, -1), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#9E9E9E")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5F5")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    elements.append(table)

    # Simple footer: page X of Y • label
    footer_label = os.environ.get("REPORT_FOOTER") or (title or "TrendDrop Report")

    class NumberedCanvas(canvas.Canvas):  # type: ignore
        def __init__(self, *args, **kwargs):
            self._saved_page_states = []
            self._footer_label = kwargs.pop("footer_label", "TrendDrop Report")
            super().__init__(*args, **kwargs)

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            num_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self._draw_footer(num_pages)
                canvas.Canvas.showPage(self)
            canvas.Canvas.save(self)

        def _draw_footer(self, page_count: int):
            width, height = letter
            page_num = self._pageNumber
            footer_text = f"Page {page_num} of {page_count} • {self._footer_label}"
            self.setFont("Helvetica", 8)
            self.setFillColor(colors.HexColor("#666666"))
            text_w = self.stringWidth(footer_text, "Helvetica", 8)
            self.drawString((width - text_w) / 2.0, 24, footer_text)

    doc.build(
        elements,
        canvasmaker=lambda *a, **k: NumberedCanvas(*a, footer_label=footer_label, **k),
    )


def write_csv(products: List[Dict], outfile_path: str, columns: List[Dict[str, str]]) -> None:
    """Write the full dataset to CSV using the same column definitions."""
    headers = [c.get("label") or c.get("key") for c in columns]
    keys = [c.get("key") for c in columns]
    with open(outfile_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for p in products:
            w.writerow([_value_for_column(p, k) for k in keys])

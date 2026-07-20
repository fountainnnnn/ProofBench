"""Polished PDF export for completed ProofBench reports."""

from __future__ import annotations

import os
import re
from datetime import datetime
from html import escape


def _number(value: object, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def _percent(value: object) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "-"


def _prose_from_markdown(markdown: str) -> list[str]:
    """Keep report prose while dropping Markdown tables and formatting markers."""
    paragraphs: list[str] = []
    current: list[str] = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line or line.startswith("|") or line.startswith("#"):
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        if line.startswith(">"):
            line = line.lstrip("> ")
        line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
        line = re.sub(r"`(.*?)`", r"\1", line)
        current.append(line)
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs[:4]


def write_pdf_report(metrics: dict, markdown: str, out_path: str) -> str:
    """Create a landscape A4 report PDF and return its absolute output path."""
    from reportlab.lib import colors
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    page_width, page_height = landscape(A4)
    document = SimpleDocTemplate(
        out_path,
        pagesize=landscape(A4),
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=15 * mm,
        title="ProofBench Report",
        author="ProofBench",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "ProofBenchTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=27,
        textColor=HexColor("#1f2a44"),
        alignment=TA_LEFT,
        spaceAfter=3 * mm,
    )
    body = ParagraphStyle(
        "ProofBenchBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        textColor=HexColor("#44506a"),
        spaceAfter=3 * mm,
    )
    caption = ParagraphStyle(
        "ProofBenchCaption",
        parent=body,
        fontSize=8,
        leading=10,
        textColor=HexColor("#6b7486"),
    )
    heading = ParagraphStyle(
        "ProofBenchHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=HexColor("#1f2a44"),
        spaceBefore=4 * mm,
        spaceAfter=2 * mm,
    )

    generic_assessment = any("rating" in values for values in metrics.values())
    ranked = sorted(
        metrics.items(),
        key=(
            (lambda item: -float(item[1].get("rating", 0) or 0))
            if generic_assessment
            else (lambda item: (-float(item[1].get("exact_accuracy", 0) or 0), -float(item[1].get("field_f1", 0) or 0)))
        ),
    )
    demo = any(values.get("is_demo") for _, values in ranked)
    status = "IMPLEMENTATION ASSESSMENT" if generic_assessment else ("DEMO RESULTS" if demo else "MEASURED RESULTS")
    status_color = "#4056a1" if generic_assessment else ("#9a6700" if demo else "#147a51")
    status_bg = "#eef1ff" if generic_assessment else ("#fff5d8" if demo else "#e7f7ef")

    story = [
        Paragraph("ProofBench benchmark report", title),
        Table(
            [[
                Paragraph(f"<b>{status}</b>", ParagraphStyle("status", parent=caption, textColor=HexColor(status_color))),
                Paragraph(f"Generated {datetime.now().strftime('%d %b %Y, %H:%M')}", caption),
            ]],
            colWidths=[42 * mm, page_width - 36 * mm - 42 * mm],
            hAlign="LEFT",
            style=TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), HexColor(status_bg)),
                ("BOX", (0, 0), (0, 0), 0.5, HexColor(status_color)),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]),
        ),
        Spacer(1, 5 * mm),
        Paragraph("Ranked results", heading),
    ]

    if generic_assessment:
        data = [["Rank", "Tool", "Rating", "Implementable", "Docs", "Feasibility", "Auth", "Daytona", "Setup"]]
        for rank, (name, values) in enumerate(ranked, 1):
            data.append([
                str(rank),
                str(name).replace("_", " "),
                f"{int(values.get('rating', 0))}/100",
                "Yes" if values.get("implementable") else "No",
                str(values.get("documentation_quality", 0)),
                str(values.get("integration_feasibility", 0)),
                str(values.get("auth_clarity", 0)),
                "Used" if values.get("daytona_triggered") else "Skipped",
                str(values.get("setup_complexity", "-")),
            ])
    else:
        data = [["Rank", "Candidate", "Exact accuracy", "Field F1", "CER", "Latency", "Failure rate", "Cost / 1k", "Setup"]]
        for rank, (name, values) in enumerate(ranked, 1):
            data.append([
                str(rank),
                str(name).replace("_", " "),
                _percent(values.get("exact_accuracy")),
                _percent(values.get("field_f1")),
                _number(values.get("cer"), 3),
                f"{_number(values.get('mean_latency_s'))}s",
                _percent(values.get("failure_rate")),
                f"${_number(values.get('cost_per_1k_docs'))}",
                str(values.get("setup_complexity", "-")),
            ])
    table = Table(
        data,
        colWidths=[14 * mm, 52 * mm, 29 * mm, 25 * mm, 18 * mm, 22 * mm, 27 * mm, 25 * mm, 16 * mm],
        repeatRows=1,
        hAlign="LEFT",
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HexColor("#edf0f6")),
        ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#526078")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 1), (-1, -1), HexColor("#26344d")),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("BACKGROUND", (0, 1), (-1, 1), HexColor("#f1f4ff")),
        ("GRID", (0, 0), (-1, -1), 0.35, HexColor("#d9dfeb")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(table)

    prose = _prose_from_markdown(markdown)
    if prose:
        story.append(Paragraph("Summary", heading))
        for paragraph in prose:
            story.append(Paragraph(escape(paragraph), body))

    footer = (
        "Ratings combine documentation evidence with sandbox verification where implementation was possible."
        if generic_assessment
        else ("Demo values are representative and clearly labelled." if demo else "Metrics are calculated deterministically from benchmark output and ground truth.")
    )
    story.extend([Spacer(1, 3 * mm), Paragraph(footer, caption)])
    document.build(story)
    return os.path.abspath(out_path)

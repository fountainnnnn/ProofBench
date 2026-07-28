"""PDF export for completed ProofBench reports.

The PDF is the artefact that leaves the tool — it gets attached to a ticket and
read by someone who never saw the console. It therefore has to carry the whole
argument, not a summary of it.

It used to carry almost none. The renderer dropped every heading, every bullet
and every citation, kept the first four paragraphs of prose, and flattened an
evidence list into one run-on sentence — so a four-candidate assessment shipped
with the findings for one candidate, no sources, and no statement of which tool
had actually won. Everything below exists to fix that: the markdown is rendered
rather than sampled, the numbers are typeset from `metrics` so they cannot drift
from the console, and the verdict is stated before the reader has to earn it
from a table.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from html import escape

# Mirrors engine.tool_assessment.ASSESSMENT_BASES. States whether a row rests on
# a real sandbox run or on documentation alone, so the PDF never implies that a
# comparison-only product was executed.
_BASIS_LABELS = {
    "sandbox_execution": "Executed",
    "documentation_evidence": "Documentation",
    "unavailable": "Unavailable",
}

INK = "#182031"
INK_2 = "#3d4861"
INK_3 = "#71798d"
LINE = "#dfe4ee"
LINE_SOFT = "#eef1f7"
ACCENT = "#2f5d8c"
ACCENT_SOFT = "#eef4fa"

# Headings the markdown uses only to introduce the ranked table. The table is
# rendered from `metrics` instead, so printing the heading too would leave it
# floating above a section that is no longer there.
_TABLE_HEADINGS = {"summary", "ranked assessment", "ranked results", "results", "rankings"}
_SOURCE_HEADINGS = {"sources", "citations", "references"}


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


def _score(value: object, suffix: str = "") -> str:
    """Render a score, or a dash when the backend withheld it.

    A withheld score means no assessment was produced. Printing 0 there would
    fabricate a measurement the run never made.
    """
    if value is None:
        return "-"
    try:
        return f"{int(value)}{suffix}"
    except (TypeError, ValueError):
        return "-"


def _display(name: str, values: dict) -> str:
    """The vendor's own name, falling back to the slug metrics are keyed by."""
    return str((values or {}).get("display_name") or name)


def _inline(text: str) -> str:
    """Markdown inline spans as reportlab markup, escaped before it is marked up.

    Escaping first is what makes this safe: a candidate name or a scraped
    heading containing '<' becomes '&lt;' before any tag is introduced, so no
    report content can close a tag the renderer opened.
    """
    out = escape(str(text))
    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
                 lambda m: f'<link href="{m.group(2)}" color="{ACCENT}">{m.group(1)}</link>', out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", out)
    out = re.sub(r"`([^`]+?)`", r'<font face="Courier">\1</font>', out)
    return out


def _blocks(markdown: str) -> list[tuple[str, object]]:
    """Parse the report into ('heading'|'para'|'bullet', value) blocks.

    Only the shapes report_gen and tool_assessment actually emit are handled.
    Markdown tables are dropped on purpose — the ranked table is typeset from
    `metrics`, which is the authoritative copy of those numbers.
    """
    blocks: list[tuple[str, object]] = []
    paragraph: list[str] = []

    def flush():
        if paragraph:
            blocks.append(("para", " ".join(paragraph)))
            paragraph.clear()

    for raw in str(markdown or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("|") or set(line) <= set("-|: "):
            flush()
            continue
        if line.startswith("#"):
            flush()
            level = len(line) - len(line.lstrip("#"))
            blocks.append(("heading", (level, line.lstrip("# ").strip())))
            continue
        if line.startswith(("- ", "* ", "+ ")):
            flush()
            blocks.append(("bullet", line[2:].strip()))
            continue
        if re.match(r"^\d+[.)]\s", line):
            flush()
            blocks.append(("bullet", re.sub(r"^\d+[.)]\s+", "", line)))
            continue
        if line.startswith(">"):
            line = line.lstrip("> ")
        paragraph.append(line)
    flush()
    return blocks


def _ranked(metrics: dict, generic: bool) -> list[tuple[str, dict]]:
    if generic:
        # Products only. A build component is a part of a self-built solution,
        # not a rival product, and ranking them together put a plotting library
        # first on a question about generating math questions. Components are
        # covered by the report's own build-path section.
        metrics = {name: values for name, values in (metrics or {}).items()
                   if (values or {}).get("role") != "build_component"}
        # Requirement fit partitions the field, then score orders within it: a
        # capped 49 for a tool that cannot do the job otherwise outranks a 43 for
        # one that can. A withheld rating sorts last without becoming a zero, and
        # only an explicit implementable=False is a failure.
        return sorted(
            metrics.items(),
            key=lambda item: (
                0 if item[1].get("rating") is None else 1,
                0 if item[1].get("implementable") is False else 1,
                float(item[1].get("rating") if item[1].get("rating") is not None else -1),
            ),
            reverse=True,
        )
    # A candidate that produced no result is not a zero score; it sorts last and
    # is never given a rank number.
    return sorted(metrics.items(),
                  key=lambda item: (-float(item[1]["exact_accuracy"])
                                    if item[1].get("exact_accuracy") is not None else 1.0,
                                    -float(item[1].get("field_f1") or 0)))


def _verdict(ranked: list[tuple[str, dict]], generic: bool) -> tuple[str, str] | None:
    """The answer, stated in words: winner, margin, and how many it beat.

    Returns None when nothing was measured — a run with no result has no winner,
    and inventing one here is the failure mode this whole file guards against.
    """
    key = "rating" if generic else "exact_accuracy"
    scored = [(name, values) for name, values in ranked if values.get(key) is not None]
    if not scored:
        return None
    name, values = scored[0]
    top = float(values[key])
    reason = str(values.get("reason") or "").strip()
    # A ranking is not a recommendation. When the top-rated candidate could not
    # be implemented against the stated objective, announcing it as the winner
    # ("ranked first, 28 points ahead") crowns the least-bad failure — a real
    # run did exactly that with a 49/100 tool whose own reason said it missed a
    # key requirement. The field is still ranked below; the verdict says what
    # the evidence actually supports.
    # A build recommendation is earned by the scores, never asserted by intake.
    # When every marketed product was rated not implementable and an assessed
    # component was, the evidence supports building rather than buying — and
    # saying so is more honest than crowning the least-bad product.
    products = [(n, v) for n, v in scored if v.get("role") != "build_component"]
    components = [(n, v) for n, v in scored if v.get("role") == "build_component"]
    viable = [(n, v) for n, v in components if v.get("implementable") is True]
    if (generic and products and components and viable
            and all(v.get("implementable") is False for _, v in products)):
        listed = ", ".join(f"{_display(n, v)} ({_score(v['rating'], '/100')})"
                           for n, v in viable)
        headline = ("No marketed product met the requirements — "
                    "self-implementation is better supported")
        detail = (f"All {len(products)} marketed products were rated not implementable "
                  f"against the stated objective. The assessed build components — "
                  f"{listed} — are documented well enough to support a self-built "
                  f"integration. This conclusion rests on documentation evidence, "
                  f"not execution.")
        unviable = len(components) - len(viable)
        if unviable:
            detail += (f" {unviable} component{'s' if unviable != 1 else ''} did not "
                       f"clear the bar and are not part of this path.")
        return headline, detail
    # A component topping the table is not a winning product, so the no-winner
    # check reads the products alone; with no components at all this is exactly
    # the previous behaviour, since every scored row is then a product.
    top_product = products[0][1] if products else values
    if generic and top_product.get("implementable") is False:
        failed = sum(1 for _, v in scored if v.get("implementable") is False)
        headline = f"No candidate met the requirements — best score {_score(top, '/100')}"
        detail = (f"{failed} of {len(scored)} scored candidates were rated not "
                  f"implementable against the stated objective. "
                  f"{_display(name, values)} ranked highest on documentation "
                  f"evidence alone; the ranking below is relative, not an "
                  f"endorsement.")
        if reason:
            detail = f"{detail} {reason}"
        return headline, detail
    headline = f"{_display(name, values)} — {_score(top, '/100') if generic else _percent(top)}"
    if len(scored) == 1:
        detail = "The only candidate that produced a result, so nothing was outranked."
    else:
        runner, runner_values = scored[1]
        gap = top - float(runner_values[key])
        unit = "points" if generic else "percentage points"
        gap_text = f"{gap:.0f}" if generic else f"{gap * 100:.1f}"
        detail = (f"Ranked first of {len(scored)} scored candidates, "
                  f"{gap_text} {unit} ahead of {_display(runner, runner_values)}.")
    if reason:
        detail = f"{detail} {reason}"
    return headline, detail


def _footer(canvas, document, generated: str):
    canvas.saveState()
    width, _ = document.pagesize
    y = 12 * document.bottomMargin / 15
    canvas.setStrokeColor(_hex(LINE))
    canvas.setLineWidth(0.4)
    canvas.line(document.leftMargin, y + 5, width - document.rightMargin, y + 5)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(_hex(INK_3))
    canvas.drawString(document.leftMargin, y - 4, f"ProofBench · generated {generated}")
    canvas.drawRightString(width - document.rightMargin, y - 4, f"Page {canvas.getPageNumber()}")
    canvas.restoreState()


def _hex(value: str):
    from reportlab.lib.colors import HexColor

    return HexColor(value)


def write_pdf_report(metrics: dict, markdown: str, out_path: str) -> str:
    """Create an A4 report PDF and return its absolute output path."""
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        CondPageBreak,
        HRFlowable,
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    generated = datetime.now().strftime("%d %b %Y, %H:%M")
    document = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="ProofBench Report",
        author="ProofBench",
    )
    content_width = document.width

    sheet = getSampleStyleSheet()
    title = ParagraphStyle("pbTitle", parent=sheet["Title"], fontName="Helvetica-Bold",
                           fontSize=20, leading=24, textColor=_hex(INK), alignment=TA_LEFT,
                           spaceAfter=1.5 * mm)
    body = ParagraphStyle("pbBody", parent=sheet["BodyText"], fontName="Helvetica",
                          fontSize=9.5, leading=14.5, textColor=_hex(INK_2), spaceAfter=2.5 * mm)
    caption = ParagraphStyle("pbCaption", parent=body, fontSize=8, leading=11,
                             textColor=_hex(INK_3), spaceAfter=0)
    h2 = ParagraphStyle("pbH2", parent=sheet["Heading2"], fontName="Helvetica-Bold",
                        fontSize=12.5, leading=16, textColor=_hex(INK),
                        spaceBefore=6 * mm, spaceAfter=2 * mm)
    h3 = ParagraphStyle("pbH3", parent=h2, fontSize=10.5, leading=14,
                        textColor=_hex(ACCENT), spaceBefore=4.5 * mm, spaceAfter=1.5 * mm)
    bullet = ParagraphStyle("pbBullet", parent=body, leftIndent=5 * mm, bulletIndent=1 * mm,
                            spaceAfter=1.2 * mm, bulletFontSize=7, bulletColor=_hex(INK_3))
    source_url = ParagraphStyle("pbSourceUrl", parent=caption, leftIndent=5 * mm,
                                fontSize=7.5, leading=9.5, spaceAfter=2 * mm)
    cell = ParagraphStyle("pbCell", parent=body, fontSize=8.5, leading=11, spaceAfter=0,
                          textColor=_hex(INK))
    cell_head = ParagraphStyle("pbCellHead", parent=cell, fontName="Helvetica-Bold",
                               fontSize=7.5, leading=9.5, textColor=_hex(INK_3))
    # Cell alignment has to live on the paragraph style: a TableStyle ALIGN rule
    # positions the flowable inside the cell, not the text inside the flowable,
    # so it silently does nothing once cells hold Paragraphs.
    aligned = {
        "l": (cell, cell_head),
        "r": (ParagraphStyle("pbCellR", parent=cell, alignment=TA_RIGHT),
              ParagraphStyle("pbCellHeadR", parent=cell_head, alignment=TA_RIGHT)),
        "c": (ParagraphStyle("pbCellC", parent=cell, alignment=TA_CENTER),
              ParagraphStyle("pbCellHeadC", parent=cell_head, alignment=TA_CENTER)),
    }
    verdict_name = ParagraphStyle("pbVerdictName", parent=body, fontName="Helvetica-Bold",
                                  fontSize=14, leading=18, textColor=_hex(INK), spaceAfter=1 * mm)

    metrics = metrics or {}
    generic = any("rating" in values for values in metrics.values())
    ranked = _ranked(metrics, generic)
    demo = any(values.get("is_demo") for _, values in ranked)
    if generic:
        status, status_color, status_bg = "IMPLEMENTATION ASSESSMENT", ACCENT, ACCENT_SOFT
    elif demo:
        status, status_color, status_bg = "DEMO RESULTS", "#9a6700", "#fff5d8"
    else:
        status, status_color, status_bg = "MEASURED RESULTS", "#147a51", "#e7f7ef"

    blocks = _blocks(markdown)
    document_title = next((text for kind, (level, text) in
                           ((k, v) for k, v in blocks if k == "heading") if level == 1),
                          "ProofBench benchmark report")

    story: list = [
        Paragraph(_inline(document_title), title),
        Spacer(1, 1 * mm),
    ]

    # Status and date on one strip. The chip is measured in the font it is drawn
    # in rather than estimated from character count, because a guessed width is
    # what made "IMPLEMENTATION ASSESSMENT" wrap into two cramped lines.
    from reportlab.pdfbase.pdfmetrics import stringWidth

    chip_width = stringWidth(status, "Helvetica-Bold", 7.5) + 13
    story.append(Table(
        [[Paragraph(f"<b>{status}</b>",
                    ParagraphStyle("pbChip", parent=caption, fontSize=7.5,
                                   textColor=_hex(status_color))),
          Paragraph(f"{len(metrics)} candidate{'s' if len(metrics) != 1 else ''} · "
                    f"generated {generated}", caption)]],
        colWidths=[chip_width, content_width - chip_width],
        hAlign="LEFT",
        style=TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), _hex(status_bg)),
            ("LINEBELOW", (0, 0), (0, 0), 1.2, _hex(status_color)),
            ("LEFTPADDING", (0, 0), (0, 0), 6), ("RIGHTPADDING", (0, 0), (0, 0), 6),
            ("LEFTPADDING", (1, 0), (1, 0), 8), ("RIGHTPADDING", (1, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]),
    ))
    story.append(Spacer(1, 6 * mm))

    # The verdict leads. A reader who stops after the first block should still
    # leave knowing which tool won and by how much.
    verdict = _verdict(ranked, generic)
    if verdict:
        headline, detail = verdict
        story.append(Table(
            [[Paragraph("RECOMMENDATION",
                        ParagraphStyle("pbEyebrow", parent=caption, fontName="Helvetica-Bold",
                                       fontSize=7, textColor=_hex(ACCENT)))],
             [Paragraph(_inline(headline), verdict_name)],
             [Paragraph(_inline(detail), ParagraphStyle("pbVerdictBody", parent=body,
                                                        spaceAfter=0))]],
            colWidths=[content_width],
            hAlign="LEFT",
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), _hex(ACCENT_SOFT)),
                ("LINEBEFORE", (0, 0), (0, -1), 2, _hex(ACCENT)),
                ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (0, 0), 7), ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
                ("TOPPADDING", (0, 1), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -2), 2),
            ]),
        ))
        story.append(Spacer(1, 2 * mm))

    story.append(Paragraph("Ranked comparison", h2))
    if generic:
        # "Pricing" sits beside the other documentation axes and renders "-" when
        # the docs disclosed no pricing at all — a withheld score, not a zero.
        headers = ["#", "Tool", "Suitability", "Basis", "Impl.", "Docs", "Feas.", "Auth",
                   "Pricing", "Setup"]
        weights = [4, 23, 12, 14, 7, 7, 7, 7, 9, 9]
        aligns = "clrlcrrrrr"
        rows = []
        for rank, (name, values) in enumerate(ranked, 1):
            implementable = values.get("implementable")
            rows.append([
                str(rank), _display(name, values),
                _score(values.get("rating"), "/100"),
                _BASIS_LABELS.get(values.get("assessment_basis"), "Documentation"),
                "-" if implementable is None else ("Yes" if implementable else "No"),
                _score(values.get("documentation_quality")),
                _score(values.get("integration_feasibility")),
                _score(values.get("auth_clarity")),
                _score(values.get("pricing_transparency")),
                _score(values.get("setup_complexity")),
            ])
    else:
        headers = ["#", "Candidate", "Exact", "F1", "CER", "Latency", "Failures",
                   "Cost / 1k", "Setup"]
        weights = [4, 24, 10, 9, 9, 11, 11, 12, 10]
        aligns = "clrrrrrrc"
        rows = []
        position = 0
        for name, values in ranked:
            measured = values.get("exact_accuracy") is not None
            if measured:
                position += 1
            rows.append([
                str(position) if measured else "-", _display(name, values),
                _percent(values.get("exact_accuracy")),
                _percent(values.get("field_f1")),
                _number(values.get("cer"), 3),
                f"{_number(values.get('mean_latency_s'))}s",
                _percent(values.get("failure_rate")),
                f"${_number(values.get('cost_per_1k_docs'))}",
                str(values.get("setup_complexity", "-")),
            ])

    total = sum(weights)
    col_widths = [content_width * w / total for w in weights]
    # Names are Paragraphs so a long identifier wraps inside its column instead
    # of overrunning the next one — which is how "Basis" ended up printed on top
    # of "Implementable".
    # Text columns read left, measurements read right, so a column of scores can
    # be scanned on its decimal edge.
    data = [[Paragraph(escape(h), aligned[a][1]) for h, a in zip(headers, aligns)]]
    for row in rows:
        data.append([Paragraph(escape(value), aligned[a][0]) for value, a in zip(row, aligns)])

    table_style = [
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, _hex(INK_3)),
        ("LINEBELOW", (0, 1), (-1, -2), 0.3, _hex(LINE)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    if len(data) > 1:
        table_style += [("BACKGROUND", (0, 1), (-1, 1), _hex(ACCENT_SOFT)),
                        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold")]
    for index in range(3, len(data), 2):
        table_style.append(("BACKGROUND", (0, index), (-1, index), _hex(LINE_SOFT)))
    table = Table(data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle(table_style))
    story.append(table)

    basis_note = (
        "Ratings combine documentation evidence with sandbox verification where implementation "
        "was possible. Comparison-only products were never executed."
        if generic
        else ("Demo values are representative and clearly labelled." if demo
              else "Metrics are calculated deterministically from benchmark output and "
                   "ground truth.")
    )
    story.extend([Spacer(1, 2.5 * mm), Paragraph(basis_note, caption)])

    # The rest of the report, rendered rather than sampled. The H1 became the
    # document title and the ranked table was typeset from metrics, so both are
    # skipped here to avoid printing them twice.
    pending: list = []
    # Content before the first section heading is the document's preamble, which
    # in practice restates the basis note printed under the table. Skipped so the
    # same sentence does not appear twice, three centimetres apart.
    skip_table_section = True
    in_sources = False
    for kind, value in blocks:
        if kind == "heading":
            level, text = value
            if level == 1:
                continue
            key = text.strip().lower().rstrip(":")
            skip_table_section = key in _TABLE_HEADINGS
            in_sources = key in _SOURCE_HEADINGS
            if skip_table_section:
                continue
            style = h2 if level == 2 else h3
            if level == 2:
                pending.append(CondPageBreak(28 * mm))
                pending.append(Paragraph(_inline(text), style))
                pending.append(HRFlowable(width="100%", thickness=0.4, color=_hex(LINE),
                                          spaceBefore=1, spaceAfter=4))
            else:
                pending.append(Paragraph(_inline(text), style))
            continue
        if skip_table_section:
            # Prose that only explained the table we replaced; the table's own
            # note below it says the same thing in one line.
            continue
        if kind == "bullet":
            # "•" and not a prettier glyph: Helvetica's default encoding has no
            # ▪, and reportlab draws a missing glyph as nothing at all, which
            # left every evidence list looking like stray indented sentences.
            pending.append(Paragraph(_inline(value), bullet, bulletText="•"))
            link = re.match(r"^\[([^\]]+)\]\(([^)\s]+)\)\s*$", value) if in_sources else None
            if link:
                # The bare URL under the title, because a citation whose address
                # only exists as a hyperlink is unverifiable the moment someone
                # prints the report.
                pending.append(Paragraph(escape(link.group(2)), source_url))
        else:
            pending.append(Paragraph(_inline(value), body))
    story.extend(pending)

    story.append(Spacer(1, 4 * mm))
    story.append(KeepTogether(Paragraph(
        "Generated by ProofBench. Every figure in this report comes from the run that "
        "produced it; no value is estimated.", caption)))

    stamp = generated
    document.build(story,
                   onFirstPage=lambda canvas, doc: _footer(canvas, doc, stamp),
                   onLaterPages=lambda canvas, doc: _footer(canvas, doc, stamp))
    return os.path.abspath(out_path)

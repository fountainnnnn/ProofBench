"""Generate a deterministic synthetic invoice dataset for ProofBench."""

from __future__ import annotations

import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


VENDORS = (
    "Acme Pte Ltd",
    "Meridian Office Supplies",
    "Northstar Logistics LLP",
    "Juniper Systems Pte Ltd",
    "Harbourfront Services",
    "Orchid Paper Company",
)


def _font(size: int) -> ImageFont.ImageFont:
    """Load Pillow's bundled font, using its scalable variant when available."""
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow versions before load_default(size=...).
        return ImageFont.load_default()


def _draw_right(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: str = "#182230",
) -> None:
    """Draw text with its right edge anchored at xy."""
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((xy[0] - (box[2] - box[0]), xy[1]), text, font=font, fill=fill)


def _template_classic(draw: ImageDraw.ImageDraw, row: dict[str, str]) -> None:
    title = _font(44)
    heading = _font(23)
    body = _font(20)
    small = _font(17)
    draw.text((64, 52), row["vendor"], font=heading, fill="#162133")
    draw.text((64, 91), "18 Market Street, Singapore 048940", font=small, fill="#526070")
    _draw_right(draw, (836, 48), "INVOICE", title, "#253c78")
    draw.line((64, 142, 836, 142), fill="#9aa9bd", width=2)
    draw.text((64, 180), f"Invoice No.  {row['invoice_number']}", font=body, fill="#182230")
    draw.text((64, 220), f"Invoice Date  {row['date']}", font=body, fill="#182230")
    draw.text((64, 293), "BILL TO", font=heading, fill="#253c78")
    draw.text((64, 334), "ProofBench Labs", font=body, fill="#182230")
    draw.rectangle((64, 430, 836, 478), fill="#e8edf6")
    draw.text((80, 442), "Description", font=body, fill="#182230")
    _draw_right(draw, (812, 442), "Amount (SGD)", body)
    draw.text((80, 510), "Professional services", font=body, fill="#182230")
    _draw_right(draw, (812, 510), row["total"], body)
    draw.line((560, 622, 836, 622), fill="#9aa9bd", width=2)
    draw.text((580, 650), "TOTAL SGD", font=heading, fill="#253c78")
    _draw_right(draw, (812, 650), f"${row['total']}", heading)
    draw.text((64, 958), "Thank you for your business.", font=small, fill="#526070")


def _template_modern(draw: ImageDraw.ImageDraw, row: dict[str, str]) -> None:
    title = _font(40)
    heading = _font(24)
    body = _font(20)
    small = _font(17)
    draw.rectangle((0, 0, 900, 194), fill="#263a58")
    draw.text((58, 47), row["vendor"], font=heading, fill="white")
    draw.text((58, 92), "TAX INVOICE", font=title, fill="#cddcff")
    _draw_right(draw, (842, 50), row["invoice_number"], heading, "white")
    _draw_right(draw, (842, 96), row["date"], body, "#dce6f6")
    draw.text((58, 246), "Customer", font=small, fill="#617087")
    draw.text((58, 276), "ProofBench Labs", font=body, fill="#182230")
    draw.text((58, 350), "SERVICES", font=heading, fill="#263a58")
    draw.line((58, 393, 842, 393), fill="#bbc5d3", width=2)
    draw.text((72, 427), "Document processing", font=body, fill="#182230")
    _draw_right(draw, (825, 427), f"SGD {row['total']}", body)
    draw.line((58, 485, 842, 485), fill="#d3dae4", width=1)
    draw.rounded_rectangle((506, 565, 842, 670), radius=12, fill="#e7efff")
    draw.text((535, 598), "AMOUNT DUE", font=heading, fill="#263a58")
    _draw_right(draw, (812, 598), f"${row['total']}", heading, "#263a58")
    draw.text((58, 995), "Payment due within 14 days", font=small, fill="#617087")


def _template_compact(draw: ImageDraw.ImageDraw, row: dict[str, str]) -> None:
    title = _font(38)
    heading = _font(23)
    body = _font(20)
    small = _font(17)
    draw.rectangle((44, 40, 856, 1060), outline="#6f7e91", width=3)
    draw.text((84, 78), row["vendor"], font=heading, fill="#243247")
    draw.text((84, 123), "INVOICE", font=title, fill="#243247")
    draw.rectangle((84, 196, 816, 284), fill="#f0f2f5")
    draw.text((106, 217), f"No: {row['invoice_number']}", font=body, fill="#182230")
    _draw_right(draw, (794, 217), f"Date: {row['date']}", body)
    draw.text((84, 344), "Description", font=small, fill="#5d6878")
    _draw_right(draw, (816, 344), "Line total", small, "#5d6878")
    draw.line((84, 376, 816, 376), fill="#6f7e91", width=2)
    draw.text((84, 411), "Invoice processing package", font=body, fill="#182230")
    _draw_right(draw, (816, 411), row["total"], body)
    draw.line((84, 477, 816, 477), fill="#c3cad3", width=1)
    draw.text((530, 546), "Grand Total", font=heading, fill="#243247")
    _draw_right(draw, (816, 546), f"SGD {row['total']}", heading)
    draw.text((84, 918), "Remittance reference:", font=small, fill="#5d6878")
    draw.text((84, 949), row["invoice_number"], font=body, fill="#182230")
    draw.text((84, 1000), "Generated for benchmarking", font=small, fill="#5d6878")


TEMPLATES = (_template_classic, _template_modern, _template_compact)


def _render_invoice(path: Path, row: dict[str, str], template_index: int) -> None:
    image = Image.new("RGB", (900, 1100), "white")
    draw = ImageDraw.Draw(image)
    TEMPLATES[template_index % len(TEMPLATES)](draw, row)

    for _ in range(180):
        x = random.randrange(image.width)
        y = random.randrange(image.height)
        shade = random.randint(226, 246)
        draw.point((x, y), fill=(shade, shade, shade))

    angle = random.uniform(-0.65, 0.65)
    image = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor="white")
    image.save(path, format="PNG", optimize=True)


def generate_dataset(out_dir: Path, n: int) -> Path:
    """Generate n invoices and return the ground-truth CSV path."""
    if n < 0:
        raise ValueError("n must be non-negative")

    random.seed(42)
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    for stale_path in images_dir.glob("inv_*.png"):
        stale_path.unlink()

    rows: list[dict[str, str]] = []
    first_date = date(2026, 6, 1)
    for index in range(n):
        doc_number = index + 1
        total_cents = 7_500 + ((index * 13_759 + 5_350) % 190_000)
        row = {
            "doc_id": f"inv_{doc_number:03d}",
            "invoice_number": f"INV-{1001 + index}",
            "date": (first_date + timedelta(days=index * 3)).isoformat(),
            "vendor": VENDORS[index % len(VENDORS)],
            "total": f"{total_cents / 100:.2f}",
        }
        rows.append(row)
        _render_invoice(images_dir / f"{row['doc_id']}.png", row, index)

    csv_path = out_dir / "ground_truth.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("doc_id", "invoice_number", "date", "vendor", "total"),
        )
        writer.writeheader()
        writer.writerows(rows)
    return csv_path.resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="Output dataset directory")
    parser.add_argument("--n", required=True, type=int, help="Number of invoices")
    args = parser.parse_args()
    print(generate_dataset(args.out, args.n))


if __name__ == "__main__":
    main()

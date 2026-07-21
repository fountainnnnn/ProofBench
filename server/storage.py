"""Tenant-owned dataset registry and upload validation."""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import shutil
from dataclasses import dataclass
from datetime import date as date_type
from decimal import Decimal, InvalidOperation

from PIL import Image, UnidentifiedImageError

from server import runs

_configured_root = os.environ.get("PROOFBENCH_DATASET_ROOT", "data/uploads")
UPLOADS_DIR = os.path.realpath(_configured_root if os.path.isabs(_configured_root)
                               else os.path.join(runs.ROOT, _configured_root))
MAX_IMAGES = int(os.environ.get("PROOFBENCH_MAX_UPLOAD_IMAGES", "100"))
MAX_IMAGE_BYTES = int(os.environ.get("PROOFBENCH_MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))
MAX_TOTAL_BYTES = int(os.environ.get("PROOFBENCH_MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))
MAX_CSV_BYTES = int(os.environ.get("PROOFBENCH_MAX_CSV_BYTES", str(2 * 1024 * 1024)))
MAX_IMAGE_PIXELS = int(os.environ.get("PROOFBENCH_MAX_IMAGE_PIXELS", "40000000"))
ALLOWED = {".png": {"PNG"}, ".jpg": {"JPEG"}, ".jpeg": {"JPEG"}, ".webp": {"WEBP"}}
ALLOWED_MIME = {".png": {"image/png"}, ".jpg": {"image/jpeg"},
                ".jpeg": {"image/jpeg"}, ".webp": {"image/webp"}}
SAFE_STEM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
GT_FIELDS = ("doc_id", "invoice_number", "date", "vendor", "total")


@dataclass(frozen=True)
class Dataset:
    id: str
    owner: str
    path: str


class DatasetRegistry:
    def __init__(self) -> None:
        os.makedirs(UPLOADS_DIR, exist_ok=True)

    def reload(self) -> None:
        # SQLite is the only registry authority. Files without a durable row are ignored.
        return None

    def create(self, owner: str, path: str, dataset_id: str | None = None,
               allow_synthetic: bool = False) -> Dataset:
        if dataset_id is None:
            raise ValueError("a database-reserved dataset id is required")
        if not re.fullmatch(r"[a-f0-9]{12}", dataset_id):
            raise ValueError("invalid dataset id")
        path = os.path.realpath(path)
        if not _confined(path, UPLOADS_DIR):
            demo_path = os.path.realpath(os.path.join(runs.ROOT, "data", "demo"))
            if not allow_synthetic or path != demo_path:
                raise ValueError("dataset path is outside upload storage")
        dataset = Dataset(dataset_id, owner, path)
        kind = "synthetic" if allow_synthetic else "upload"
        image_count, total_bytes = _dataset_stats(path)
        runs.register_dataset(dataset_id, owner, path, kind=kind,
                              image_count=image_count, total_bytes=total_bytes)
        return dataset

    def allocate_id(self) -> str:
        raise RuntimeError("dataset ids must be reserved transactionally with an owner and size")

    def release_id(self, dataset_id: str) -> None:
        return None

    def get(self, dataset_id: str, owner: str) -> Dataset | None:
        value = runs.get_dataset(dataset_id, owner)
        return Dataset(value["id"], value["owner"], value["path"]) if value else None

    def describe(self, dataset_id: str, owner: str) -> dict | None:
        dataset = self.get(dataset_id, owner)
        if not dataset:
            return None
        value = runs.get_dataset(dataset_id, owner)
        return ({"id": value["id"], "dataset_id": value["id"], "kind": value["kind"],
                 "image_count": value["image_count"], "total_bytes": value["total_bytes"]}
                if value else None)

    def list(self, owner: str) -> list[Dataset]:
        values = []
        for item in runs.list_datasets(owner):
            current = runs.get_dataset(item["id"], owner)
            if current:  # deletion may win between the authoritative list and detail reads
                values.append(Dataset(current["id"], current["owner"], current["path"]))
        return values

    def delete(self, dataset_id: str, owner: str) -> bool:
        value = runs.STORE.get_deleting_dataset(dataset_id, owner)
        if not value:
            return False
        dataset = Dataset(value["id"], value["owner"], value["path"])
        metadata_dir = os.path.realpath(os.path.join(UPLOADS_DIR, dataset_id))
        if not _confined(metadata_dir, UPLOADS_DIR) or os.path.realpath(dataset.path) != metadata_dir:
            raise ValueError("synthetic datasets cannot be deleted")
        if os.path.isdir(metadata_dir):
            shutil.rmtree(metadata_dir)
        return True

    def synthetic(self, owner: str) -> Dataset:
        digest = hashlib.sha256(("proofbench-demo:" + owner).encode()).hexdigest()[:12]
        existing = self.get(digest, owner)
        return existing or self.create(owner, os.path.join(runs.ROOT, "data", "demo"), digest,
                                       allow_synthetic=True)

    def cleanup_unreferenced(self, referenced_ids: set[str], cutoff_timestamp: float) -> list[str]:
        return []


def _dataset_stats(path: str) -> tuple[int, int]:
    image_dir = os.path.join(path, "images")
    files = [os.path.join(image_dir, name) for name in os.listdir(image_dir)] if os.path.isdir(image_dir) else []
    image_files = [item for item in files if os.path.isfile(item)]
    total_bytes = sum(os.path.getsize(item) for item in image_files)
    truth = os.path.join(path, "ground_truth.csv")
    if os.path.isfile(truth):
        total_bytes += os.path.getsize(truth)
    return len(image_files), total_bytes


def _confined(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((os.path.realpath(root), os.path.realpath(path))) == os.path.realpath(root)
    except ValueError:
        return False


def validate_image(filename: str, content_type: str | None, data: bytes) -> str:
    basename = os.path.basename(filename)
    if basename != filename or not basename:
        raise ValueError("image filename must not contain a path")
    stem, extension = os.path.splitext(basename)
    extension = extension.lower()
    if not SAFE_STEM.fullmatch(stem) or extension not in ALLOWED:
        raise ValueError(f"unsupported or unsafe image filename: {filename}")
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"image {filename} is empty or too large")
    if (content_type and content_type.lower() != "application/octet-stream" and
            content_type.lower() not in ALLOWED_MIME[extension]):
        raise ValueError(f"invalid MIME type for image {filename}")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError(f"image {filename} exceeds the pixel limit")
            image.verify()
            detected = image.format
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError(f"image {filename} could not be decoded") from exc
    if detected not in ALLOWED[extension]:
        raise ValueError(f"image {filename} content does not match its extension")
    return stem


def validate_ground_truth(data: bytes, image_ids: set[str]) -> None:
    if not data or len(data) > MAX_CSV_BYTES:
        raise ValueError("ground_truth.csv is empty or too large")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("ground_truth.csv must be UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != GT_FIELDS:
        raise ValueError("ground_truth.csv has an invalid header")
    seen: set[str] = set()
    for line, row in enumerate(reader, 2):
        doc_id = (row.get("doc_id") or "").strip()
        if not SAFE_STEM.fullmatch(doc_id) or doc_id in seen:
            raise ValueError(f"invalid or duplicate doc_id on CSV line {line}")
        if any(len((row.get(field) or "")) > 4096 for field in GT_FIELDS):
            raise ValueError(f"value too long on CSV line {line}")
        values = {field: (row.get(field) or "").strip() for field in GT_FIELDS}
        if any(not values[field] for field in GT_FIELDS):
            raise ValueError(f"ground truth values are required on CSV line {line}")
        date = values["date"]
        try:
            date_type.fromisoformat(date)
        except ValueError as exc:
            raise ValueError(f"date must be YYYY-MM-DD on CSV line {line}") from exc
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise ValueError(f"date must be YYYY-MM-DD on CSV line {line}")
        if not re.fullmatch(r"\d+(?:\.\d{1,2})?", values["total"]):
            raise ValueError(f"invalid total on CSV line {line}")
        try:
            total = Decimal(values["total"])
        except InvalidOperation as exc:
            raise ValueError(f"invalid total on CSV line {line}") from exc
        if not total.is_finite():
            raise ValueError(f"invalid total on CSV line {line}")
        seen.add(doc_id)
    if not seen:
        raise ValueError("ground_truth.csv must contain at least one row")
    if seen != image_ids:
        raise ValueError("ground truth doc_ids must exactly match image filenames")


datasets = DatasetRegistry()

"""Prebuilt sandbox images for first-party candidates.

Every run used to pay each candidate's installation cost inside a fresh
sandbox: `apt-get install tesseract-ocr`, `pip install torch`, `pip install
paddlepaddle`. That is the same work, repeated identically, on every run — and
for the heavier candidates it dominated the benchmark and sometimes exceeded
the command timeout outright.

A snapshot is that installation done once and captured. Sandboxes created from
it start with the dependencies already present (measured: 1.0s to a usable
sandbox with Tesseract installed, against ~12s of apt/pip in-sandbox), so a
candidate's build step becomes a no-op rather than the longest phase of a run.

The snapshot name embeds a hash of the exact build commands it was built from,
so editing a candidate's build commands produces a different name and the old
snapshot is never silently reused for source it does not match. Nothing here is
required: when a snapshot cannot be built or the provider does not offer them,
callers fall back to the previous behaviour of installing in-sandbox.
"""

from __future__ import annotations

import hashlib
import logging

LOGGER = logging.getLogger("proofbench.snapshots")

# Bump when the base image or the snapshot layout changes in a way that should
# invalidate every existing snapshot regardless of build commands.
SNAPSHOT_SCHEMA = "v1"
SNAPSHOT_PREFIX = "proofbench"
BASE_PYTHON = "3.12"


def snapshot_name(candidate_name: str, build_commands, gpu: int = 0) -> str:
    """A stable name identifying this candidate's exact build and shape.

    The accelerator is part of the identity: a CUDA build and a CPU build of the
    same candidate install different wheels, so they must never share a name.
    """
    payload = "\n".join(str(command) for command in (build_commands or []))
    if gpu:
        payload += f"\ngpu={int(gpu)}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]
    safe = "".join(ch if ch.isalnum() else "-" for ch in str(candidate_name).lower())
    return f"{SNAPSHOT_PREFIX}-{safe}-{SNAPSHOT_SCHEMA}-{digest}"


def _declarative_image(build_commands):
    from daytona import Image

    image = Image.debian_slim(BASE_PYTHON)
    for command in build_commands:
        image = image.run_commands(str(command))
    return image


def _listed_snapshots(client):
    """Snapshot records out of the SDK's paginated listing.

    ``snapshot.list()`` returns a PaginatedSnapshots model, and iterating that
    yields ``(field, value)`` pairs rather than snapshots. Reading ``items``
    is what actually enumerates them.
    """
    listing = client.snapshot.list()
    items = getattr(listing, "items", None)
    if items is None and isinstance(listing, (list, tuple)):
        items = listing
    return list(items or [])


def snapshot_exists(client, name: str) -> bool:
    """Whether a usable snapshot of this name is already registered."""
    try:
        for snapshot in _listed_snapshots(client):
            if str(getattr(snapshot, "name", "")) == name:
                return "active" in str(getattr(snapshot, "state", "")).lower()
    except Exception as exc:  # listing is an optimization, never a failure
        LOGGER.info("snapshot listing unavailable: %s", type(exc).__name__)
    return False


def ensure_snapshot(client, candidate_name: str, build_commands,
                    cpu: int = 2, memory_gib: int = 4,
                    gpu: int = 0, gpu_type: str = "") -> str | None:
    """Return a ready snapshot name for this build, creating it if needed.

    Returns None when no snapshot could be produced, which means the caller
    should install in-sandbox exactly as before. Building is slow (~2 minutes)
    but happens once per distinct build, not once per run.
    """
    if not build_commands:
        return None
    name = snapshot_name(candidate_name, build_commands, gpu)
    if snapshot_exists(client, name):
        return name
    try:
        from daytona import CreateSnapshotParams
        from daytona.common.sandbox import Resources

        # Sandboxes created from a snapshot inherit its declared shape and take
        # no Resources override, so the accelerator has to be declared here or
        # every snapshot-backed candidate silently runs without one.
        if gpu:
            from engine.sandbox_pool import resolve_gpu_type

            resources = Resources(cpu=cpu, memory=memory_gib, gpu=gpu,
                                  gpu_type=resolve_gpu_type(gpu_type))
        else:
            resources = Resources(cpu=cpu, memory=memory_gib)
        client.snapshot.create(
            CreateSnapshotParams(
                name=name,
                image=_declarative_image(build_commands),
                resources=resources,
            ),
            on_logs=lambda _message: None,
        )
        return name
    except Exception as exc:
        # An account without snapshot entitlement, a transient build failure, or
        # a name that already exists in a non-active state all land here. The
        # run proceeds by installing in-sandbox.
        detail = str(exc)
        if "already exists" in detail.lower() and snapshot_exists(client, name):
            return name
        if gpu:
            # An accelerator the account cannot give this build is not a reason
            # to lose the prebuilt image entirely; retry the same build without
            # one and let the sandbox decide its device at runtime.
            LOGGER.info("gpu snapshot unavailable for %s, retrying on cpu", candidate_name)
            return ensure_snapshot(client, candidate_name, build_commands,
                                   cpu=cpu, memory_gib=memory_gib)
        LOGGER.info("snapshot build skipped for %s: %s", candidate_name, type(exc).__name__)
        return None


# The environment every candidate starts from, whatever it is.
#
# ProofBench benchmarks tools, not one category of tool: a candidate may be an
# OCR engine, a document parser, a classifier, a scraper, an embedding model, or
# an API client that needs nothing but `requests`. Building a snapshot per
# candidate only helps the candidates ProofBench ships, because a generated
# adapter's build commands are authored per run and a snapshot of them would be
# used exactly once.
#
# So there is also a base snapshot: a domain-neutral substrate that any
# candidate can install on top of. It carries what a Linux tool integration
# usually needs before it needs anything specific — a toolchain, the common
# native libraries that binary wheels link against, and the CUDA build of Torch,
# which is both the largest single download in this space and the one most
# widely shared. Nothing here is OCR: Tesseract, PaddleOCR, and EasyOCR install
# their own runtimes on top of this, exactly as a candidate ProofBench has never
# seen would.
BASE_SNAPSHOT_COMMANDS = (
    "apt-get update && apt-get install -y --no-install-recommends "
    "build-essential curl git ca-certificates "
    # Native libraries that image, video, and document wheels link against.
    "libgl1 libglib2.0-0 libgomp1 libsm6 libxext6 "
    "poppler-utils ffmpeg "
    "&& rm -rf /var/lib/apt/lists/*",
    "python -m pip install --upgrade pip setuptools wheel",
    # The CUDA build, because the sandbox carries a GPU. On a sandbox without
    # one these wheels still run on CPU, so the base stays valid either way.
    ("python -m pip install --index-url "
     "https://download.pytorch.org/whl/cu124 torch torchvision"),
    # The substrate almost every integration reaches for, independent of domain.
    "python -m pip install numpy pillow requests httpx pandas opencv-python-headless",
)

BASE_SNAPSHOT_NAME = "base"


def ensure_base_snapshot(client, cpu: int = 4, memory_gib: int = 8,
                         gpu: int = 0, gpu_type: str = "") -> str | None:
    """The general-purpose snapshot any candidate can start from."""
    return ensure_snapshot(client, BASE_SNAPSHOT_NAME, BASE_SNAPSHOT_COMMANDS,
                           cpu=cpu, memory_gib=memory_gib, gpu=gpu, gpu_type=gpu_type)

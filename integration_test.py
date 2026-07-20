"""Offline integration test — no Daytona, no LLM keys needed.

Validates the full pipeline mechanics with a local subprocess "sandbox":
dataset generation → candidate build/validate/run → collation → deterministic
evaluation → report fallback. Uses two fake candidates:
  - "oracle": reads ground_truth.csv and returns the correct row (should score ~1.0)
  - "noisy":  returns perturbed fields (should score strictly worse)

Run: .venv/Scripts/python integration_test.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from engine.agent import Orchestrator  # noqa: E402
from engine.candidates.base import Candidate, RESULT_JSON_WRAPPER  # noqa: E402
from engine.sandbox_pool import SandboxHandle, SandboxPool  # noqa: E402

VENV_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PY = VENV_PY if os.path.exists(VENV_PY) else sys.executable


class LocalSandbox:
    """Fakes a Daytona sandbox: private workdir, runs code locally with it as CWD."""

    def __init__(self):
        self.workdir = tempfile.mkdtemp(prefix="pb-sb-")

    class _Proc:  # noqa: D401 - minimal duck type
        def __init__(self, outer):
            self._outer = outer

        def exec(self, cmd, timeout=120):
            r = subprocess.run(cmd, shell=True, cwd=self._outer.workdir,
                               capture_output=True, text=True, timeout=timeout)
            return r.stdout + r.stderr

        def code_run(self, code, timeout=180):
            with tempfile.NamedTemporaryFile(
                "w", suffix=".py", dir=self._outer.workdir, delete=False
            ) as f:
                f.write(code)
                tmp = f.name
            try:
                r = subprocess.run([PY, tmp], cwd=self._outer.workdir,
                                   capture_output=True, text=True, timeout=timeout)
                return r.stdout + r.stderr
            finally:
                os.unlink(tmp)

    @property
    def process(self):
        return LocalSandbox._Proc(self)

    class _FS:
        def __init__(self, outer):
            self._outer = outer

        def upload(self, local, remote):
            import shutil

            dst = os.path.join(self._outer.workdir, remote)
            os.makedirs(os.path.dirname(dst) or self._outer.workdir, exist_ok=True)
            if not os.path.abspath(local) == os.path.abspath(dst):
                shutil.copy(local, dst)

    @property
    def fs(self):
        return LocalSandbox._FS(self)

    def delete(self):
        pass


class LocalPool(SandboxPool):
    def __init__(self):
        super().__init__(size=2)

    def start(self):
        pass

    def acquire(self, label: str) -> SandboxHandle:
        return SandboxHandle(id=f"{label}-local", label=label,
                             sandbox=LocalSandbox())

    def release(self, h):
        pass

    def destroy_all(self):
        pass


ORACLE_ADAPTER = '''
import csv
def extract(image_path):
    import os
    doc_id = os.path.splitext(os.path.basename(image_path))[0]
    with open("ground_truth.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["doc_id"] == doc_id:
                return {"invoice_number": row["invoice_number"], "date": row["date"],
                        "vendor": row["vendor"], "total": row["total"]}
    return {"invoice_number": "", "date": "", "vendor": "", "total": ""}
''' + "\n" + RESULT_JSON_WRAPPER

NOISY_ADAPTER = '''
import csv
def extract(image_path):
    import os
    doc_id = os.path.splitext(os.path.basename(image_path))[0]
    with open("ground_truth.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["doc_id"] == doc_id:
                return {"invoice_number": "X" + row["invoice_number"],
                        "date": row["date"], "vendor": row["vendor"].lower(),
                        "total": ""}
    return {"invoice_number": "", "date": "", "vendor": "", "total": ""}
''' + "\n" + RESULT_JSON_WRAPPER


def main() -> int:
    events = []

    def emit(ev, data):
        events.append((ev, data))
        if ev == "state":
            print("  [state]", data.get("phase"), data.get("candidates"))

    # 1. dataset
    print("== make_dataset ==")
    r = subprocess.run([PY, "make_dataset.py", "--out", "data/itest", "--n", "8"],
                       cwd=ROOT, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip()[-500:])
    assert r.returncode == 0, r.stderr
    dataset = os.path.join(ROOT, "data", "itest")
    assert os.path.exists(os.path.join(dataset, "ground_truth.csv"))

    # 2. orchestrator with local pool
    print("== scripted run (oracle + noisy) ==")
    run_dir = os.path.join(ROOT, "runs", "itest")
    orch = Orchestrator("itest", run_dir, emit)
    orch.pool = LocalPool()
    orch.ctx.pool = orch.pool

    spec = {
        "category": "invoice extraction (offline test)",
        "fields": ["invoice_number", "date", "vendor", "total"],
        "candidates": [
            {"name": "oracle", "display_name": "Oracle (test)", "docs_url": "",
             "pricing_url": "", "kind": "local_tool", "use_fallback": False},
            {"name": "noisy", "display_name": "Noisy (test)", "docs_url": "",
             "pricing_url": "", "kind": "local_tool", "use_fallback": False},
        ],
        "dataset": {"path": dataset},
    }
    # inject test adapters directly (bypasses fallback registry + codegen)
    orch.ctx.candidates["oracle"] = Candidate(
        name="oracle", display_name="Oracle (test)", docs_url="", kind="local_tool",
        build_commands=[], adapter_code=ORACLE_ADAPTER, setup_complexity=1)
    orch.ctx.candidates["noisy"] = Candidate(
        name="noisy", display_name="Noisy (test)", docs_url="", kind="local_tool",
        build_commands=[], adapter_code=NOISY_ADAPTER, setup_complexity=1)

    metrics = orch.run_benchmark_scripted(spec)

    # 3. assertions
    print("== assertions ==")
    assert set(metrics) == {"oracle", "noisy"}, f"unexpected metrics keys: {set(metrics)}"
    o, n = metrics["oracle"], metrics["noisy"]
    print("  oracle:", json.dumps(o))
    print("  noisy: ", json.dumps(n))
    assert o["exact_accuracy"] > 0.99, f"oracle should be ~perfect: {o['exact_accuracy']}"
    assert o["failure_rate"] == 0.0
    assert n["exact_accuracy"] < o["exact_accuracy"], "noisy should score worse"
    kinds = {k for k, _ in events if k == "artifact"}
    print("  artifact kinds emitted:", sorted({d.get('kind') for _, d in events if _ == 'artifact'}))
    report_path = os.path.join(run_dir, "report.md")
    assert os.path.exists(report_path), "report.md missing"
    with open(report_path, encoding="utf-8") as f:
        head = f.read(300)
    print("  report.md head:", head.replace("\n", " | ")[:200])
    print("ALL INTEGRATION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

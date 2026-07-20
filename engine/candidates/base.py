"""Candidate contract for ProofBench (CONTRACTS §1).

Every benchmark candidate — local OCR tool, hosted VLM, or anything the agent
discovers — is described by the same dataclass. The runner is fully generic.
"""

from dataclasses import dataclass


@dataclass
class Candidate:
    name: str                    # unique slug, e.g. "tesseract"
    display_name: str            # "Tesseract OCR 5.x"
    docs_url: str                # documentation the integration was built from
    kind: str                    # "local_tool" | "hosted_api"
    build_commands: list[str]    # shell cmds to install/configure INSIDE a Daytona sandbox
    adapter_code: str            # python source executed INSIDE the sandbox (see below)
    setup_complexity: int = 1    # 1 (trivial) .. 5 (painful); agent may worsen it on repairs
    pricing_url: str = ""        # where pricing was scraped from ("" if free/local)


# adapter_code contract (executed inside the sandbox, CWD contains the dataset):
#   - defines extract(image_path: str) -> dict with keys
#     {"invoice_number", "date", "vendor", "total"} (empty string for missing fields)
#   - MUST end with exactly this wrapper (no other prints):
RESULT_JSON_WRAPPER = '''
import json, sys, time
_t0 = time.time()
try:
    _out = extract(sys.argv[1])
    print("RESULT_JSON:" + json.dumps({"ok": True, "fields": _out, "latency_s": round(time.time() - _t0, 3)}))
except Exception as e:
    print("RESULT_JSON:" + json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
'''.strip()

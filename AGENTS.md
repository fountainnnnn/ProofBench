# ProofBench repository guide

Read `CONTRACTS.md`, `DESIGN.md`, and `PRODUCT.md` before changing behavior.
`CONTRACTS.md` is the current hardened local technical interface, not a frozen
hackathon artifact and not a service commitment to anyone. Update it in the same
change whenever an external contract changes.

`ARCHITECTURE.md` maps what the system does today, and
`docs/adr/0001-local-product-boundary.md` records the product boundary: a
proprietary, solo-operated local pre-release on one single-host Compose unit.
Keep documentation and UI copy inside that boundary.

## Supported stack

- Python 3.12 backend: FastAPI in `server/`, benchmark engine in `engine/`.
- React 18 and Vite 8 frontend in `web/`; Node.js 22.12 or newer.
- SQLite is the durable single-host state store. The supported Compose shape is
  one API replica and one Nginx web replica.
- Runtime data lives under `runs/` and `data/`; neither belongs in Git.

## Local commands

```powershell
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest_local_tmp
.venv\Scripts\python.exe integration_test.py
Set-Location web
npm ci
npm run lint:a11y
npm test
npm run build
```

Run the API with `.venv\Scripts\python.exe -m uvicorn server.main:app
--port 8000`. Run the frontend with `npm run dev` from `web/`.

## Engineering rules

1. Authentication mode is fail closed and mutually exclusive. The supported
   loopback-only local profile explicitly sets `PROOFBENCH_INSECURE_DEV=1` and
   resolves tokenless requests to `local-dev`. Any exposed deployment disables
   that flag and requires `PROOFBENCH_API_KEYS`. Liveness and auth-mode status
   are public; authenticated-mode session bootstrap accepts a valid token.
2. Browser code must never persist bearer tokens. In authenticated mode,
   HttpOnly cookies are for read-only browser transports and state-changing
   requests require a bearer or API-key header. Local mode sends neither.
3. Every resource lookup is tenant scoped. Clients use server-issued dataset,
   session, and immutable run IDs, never host paths.
4. The deterministic evaluator is the only source of extraction correctness.
   LLMs may explain measured results but must never invent or judge scores.
5. Generated code runs in disposable Daytona sandboxes. Capabilities and
   credentials are granted from trusted server identity, never user-controlled
   labels or generated source. Orchestration credentials are never injected.
6. Validate and redact before persistence, logging, SSE emission, rendering,
   or PDF generation. SSE lines are single-line, bounded, and secret free.
7. Use durable transactions for admission, quotas, retention, ownership, and
   active-run state. Do not use process-local dictionaries as authority.
8. Frontend work follows `DESIGN.md`, uses the light theme, remains keyboard
   accessible and responsive near 390 px, and represents missing/synthetic
   data honestly.
9. Keep dependencies locked, hashed where supported, and audited. Add new
   direct dependencies to the corresponding `.in` or `package.json` file and
   regenerate the lock. `pip-compile` resolves only for the platform it runs
   on, so recompiling on macOS or Linux silently drops the Windows-only wheels
   (`colorama`, `win32-setctime`) and the Windows CI job then fails at install
   under `--require-hashes`. Regenerate on Windows, or re-add those entries by
   hand and verify with `pip install --dry-run --require-hashes` against both
   locks. Keep the flags the file header documents; dropping `--allow-unsafe`
   from the dev lock unpins `pip` itself and fails the same way.
10. Behavior changes require focused regression tests plus the relevant full
    suite. Never weaken a failing security or correctness test to make it pass.
11. Land work as you finish it. A change that is verified is committed and
    pushed in the same sitting, not left dirty in a working tree: this repo is
    edited from more than one machine, and unpushed work is work the next
    machine cannot see. Commit when the relevant suites pass, push the branch,
    and say what landed. Do not batch a day of unrelated changes into one
    commit because pushing was deferred.
12. Documentation is part of the change, not a follow-up. When a change alters
    what the product does, what it costs, or how it is operated, update
    `README.md` in the same commit. Record measurements rather than adjectives
    (`399s -> 84s`, not "much faster"), and write down negative results too:
    a documented dead end is what stops the next person paying for it twice.

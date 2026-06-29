# The curriculum: build your own AI Flight Recorder

These steps build the gateway up one idea at a time. Each step is a snapshot of
the journey; the **destination** is the full, tested implementation in
[`../src/blackbox_ai`](../src/blackbox_ai).

## How the steps work

- **Steps 01-03 are self-contained, runnable mini-apps.** Each `app.py` is a
  complete program *as of that step* — copy-forward snapshots, no MongoDB or API
  keys required (they use stand-ins so you can run them immediately). This is the
  "from scratch" build of the core architecture.
- **Steps 04-07 are guided lessons anchored on the canonical package.** Vector
  search, Queryable Encryption, the TTL cache, and production hardening rely on
  real infrastructure (MongoDB Atlas Local, `crypt_shared`, Voyage AI), so rather
  than ship fragile partial copies, these steps walk the exact modules in
  `src/blackbox_ai` and show you how to *run them* via the project's `make`
  targets. The canonical package is the final step.

> Nothing in `steps/` is wired into the main test/CI gate — the canonical
> `src/blackbox_ai` + `tests/` stay green on their own. Steps are additive
> teaching material.

## Run a self-contained step

Steps 01-03 need only FastAPI, Uvicorn, and httpx:

```bash
cd steps/01-pass-through-relay
uv run --with fastapi --with "uvicorn[standard]" --with httpx uvicorn app:app --port 8000
# (or: pip install fastapi "uvicorn[standard]" httpx && uvicorn app:app --port 8000)
```

## The path

1. [01-pass-through-relay](01-pass-through-relay) — the invisible dashcam: a fail-open streaming relay. *(runnable)*
2. [02-two-plane-capture](02-two-plane-capture) — tee + queue + workers; the recorder that can't crash the plane. *(runnable)*
3. [03-parsers-and-intent](03-parsers-and-intent) — per-provider parsing, the polymorphic Intent Document, and *why "intent" is biased*. *(runnable)*
4. [04-vector-time-travel](04-vector-time-travel) — embeddings, `$vectorSearch`, and hybrid `$rankFusion`. *(lesson on the canonical package)*
5. [05-queryable-encryption](05-queryable-encryption) — the safe with a search slot, fail-closed. *(lesson)*
6. [06-cache-and-replay](06-cache-and-replay) — a self-cleaning TTL cache and portable replay artifacts. *(lesson)*
7. [07-production-hardening](07-production-hardening) — secure-by-default, and the honest gap to real production. *(lesson)*

Each step's `README.md` lists the canonical modules it corresponds to, so you can
always diff the snapshot against the real thing.

# Step 01 — The invisible dashcam (a fail-open relay)

**Goal:** sit between an SDK and a model provider, and stream every byte through
unchanged. No recording yet — just prove the recorder can be *invisible*.

## The idea

Nobody adopts a debugging tool that makes their life harder *today* to maybe help
at 2:47am *someday*. So the recorder has to be a thing you drop in front of your
existing code without your existing code noticing. That fits on one line — you
change *where* your SDK points:

```python
client = OpenAI(base_url="http://localhost:8000/openai/v1", api_key="sk-...")
```

The key design move is the **data plane**: one generic relay serves *every*
provider. There's no per-provider logic on the hot path — just a small table of
base URLs ([`app.py`](app.py) `PROVIDERS`) and a generic handler that forwards the
request and streams the response straight back. Adding a provider is one line.
Being dumb is the feature: a relay that does almost nothing has almost nothing
that can break.

## Run it

```bash
cd steps/01-pass-through-relay
uv run --with fastapi --with "uvicorn[standard]" --with httpx uvicorn app:app --port 8000
```

Then drive it. The simplest no-key check (needs a local Ollama):

```bash
curl -N http://localhost:8000/ollama/api/chat \
  -H 'content-type: application/json' \
  -d '{"model":"llama3.2","messages":[{"role":"user","content":"hi"}],"stream":true}'
```

Or point an OpenAI SDK at `http://localhost:8000/openai/v1` with your real key —
the client carries its own `Authorization` header straight through.

## What to notice

- **Streaming is preserved.** We use `httpx` with `stream=True` and FastAPI's
  `StreamingResponse`, so tokens reach the client as they arrive — the relay adds
  negligible overhead and never buffers the whole response.
- **It's already fail-open**, trivially: there's no database, no parsing, nothing
  to fall over. Steps 02+ add recording *without* giving up this property.

## Canonical counterparts

- [`src/blackbox_ai/proxy/relay.py`](../../src/blackbox_ai/proxy/relay.py) — the real generic relay (credential injection, backpressure, error handling).
- [`src/blackbox_ai/providers/catalog.py`](../../src/blackbox_ai/providers/catalog.py) — the real provider table.
- [`src/blackbox_ai/api/relay_routes.py`](../../src/blackbox_ai/api/relay_routes.py) — the catch-all route.

## Next

[Step 02](../02-two-plane-capture) adds the recorder — and the rule that it can
never crash the plane.

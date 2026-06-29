# Step 02 — The recorder that can't crash the plane

**Goal:** record every interaction, while making it *impossible* for the recorder
to slow down or break the response.

## The idea

The day you add logging is the day your logging becomes a way to fail: the
database hiccups, and suddenly user-facing requests are hanging on a telemetry
write. A flight recorder that can crash the plane isn't a safety device.

So we split the system into two planes (see [`app.py`](app.py)):

- **Data plane** — the relay from Step 01, now *teeing* a copy of the byte stream
  to a capture buffer as it flows to the client. The tee
  ([`tee_stream`](app.py)) is the trick: there's one consumer (the client) and a
  cheap, synchronous, non-raising `observe` callback. Because the generator only
  advances when the client pulls the next chunk, **capture inherits the client's
  backpressure for free** — no second queue, no way for recording to outrun the
  response.
- **Telemetry plane** — a background worker drains a bounded `asyncio.Queue` and
  does the slow, fallible work. The hand-off is a single non-blocking
  `put_nowait`, and the entire philosophy is three lines:

```python
def submit(capture):
    try:
        QUEUE.put_nowait(capture)
    except asyncio.QueueFull:
        METRICS["dropped"] += 1   # shed load; never block the relay
```

The worst thing that happens under load is we lose a *recording*, never a
*response*. Keep that distinction sacred and everything else gets easy.

This snapshot uses an in-memory list instead of MongoDB, so it runs with no
external dependencies.

## Run it

```bash
cd steps/02-two-plane-capture
uv run --with fastapi --with "uvicorn[standard]" --with httpx uvicorn app:app --port 8000
```

Make a call (local Ollama shown; any provider works with its key):

```bash
curl -N http://localhost:8000/ollama/api/chat \
  -H 'content-type: application/json' \
  -d '{"model":"llama3.2","messages":[{"role":"user","content":"hi"}],"stream":true}'
```

Then look at what the telemetry plane recorded — note this endpoint is *not* part
of the relay:

```bash
curl -s http://localhost:8000/_captures | python -m json.tool
```

You'll see latency, time-to-first-token, the streamed flag, and a preview — plus
`metrics.captured` / `metrics.dropped` and the live `queue_depth`.

## What to notice

- The capture is finalized in a `finally`, so we record even when the **client
  disconnects mid-stream** ("the user bailed after 12 seconds" is a data point you
  want).
- Nothing in the telemetry plane can raise into the relay. Kill the worker, fill
  the queue — the response still streams.

## Canonical counterparts

- [`src/blackbox_ai/proxy/tee.py`](../../src/blackbox_ai/proxy/tee.py)
- [`src/blackbox_ai/telemetry/capture.py`](../../src/blackbox_ai/telemetry/capture.py)
- [`src/blackbox_ai/telemetry/pipeline.py`](../../src/blackbox_ai/telemetry/pipeline.py) — see also [ADR 0004: drop-on-full backpressure](../../docs/adr/0004-drop-on-full-backpressure.md).
- [`src/blackbox_ai/telemetry/sink_mongo.py`](../../src/blackbox_ai/telemetry/sink_mongo.py)

## Next

[Step 03](../03-parsers-and-intent) turns the raw bytes into a structured,
polymorphic **Intent Document** — and confronts the uncomfortable question of what
"intent" actually is.

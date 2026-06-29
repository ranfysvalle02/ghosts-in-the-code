# Step 06 — A self-cleaning cache and portable replay

**Goal:** stop paying for identical calls, and make every captured interaction
something you *own* and can re-run.

## Part 1: a cache that takes out its own trash

Some calls are byte-for-byte identical and expensive. The obvious fix is a cache;
the obvious mistake is a cache you never clean. Instead of bolting on a second
server with its own eviction story, let MongoDB hold the cache and tell it one
thing — *these entries expire after an hour* — via a **TTL index**. The server
sweeps them for you. No cron, no second system.

The lookup stays on-brand with the fail-open philosophy: it's time-bounded
(`asyncio.wait_for`), so a slow cache read can never stall the relay — past the
budget we just forward upstream. The cache is an optimization, never a dependency.

One subtlety: the entry's identity is `(cache_key, streamed)`, so an SSE response
is never replayed to a client expecting a single JSON document, or vice versa. See
[ADR 0003](../../docs/adr/0003-cache-identity-includes-streamed.md).

## Part 2: replay — own it, then re-run it

Capturing is only half the value; you also want to own and replay. The gateway
**never auto-replays** — a captured agent request can carry destructive tool calls
(`edit_file`, `delete`), so re-issuing one is always a deliberate human action. It
hands you the verbatim, decrypted inputs as a self-contained artifact instead.

Keep the two tiers straight:

- **Deterministic replay (the cache):** an exact repeat replays the *stored
  response bytes* — byte-for-byte identical, for free. Narrow, but exact.
- **Migration replay (the artifact):** feed the verbatim payload to a cheaper /
  newer / open-source model. You reproduce the *inputs* exactly; the *output* is a
  fresh generation. The honest escape hatch from lock-in: you own every question
  ever asked.

## Run it

```bash
# caching
echo 'GATEWAY_CACHE_ENABLED=true' >> .env
make up && make init
# opt a request in with the header X-Intent-Cache: on; a HIT returns instantly
# and is still recorded with served_from_cache: true.

# replay / export
docker compose exec gateway blackbox-ai export <request_id>
docker compose exec gateway blackbox-ai export <request_id> --as curl --token demo-gateway-token
```

Section 5 of [`../../OLLAMA_DEMO.md`](../../OLLAMA_DEMO.md) walks a full export.

## Canonical modules

- [`src/blackbox_ai/cache/`](../../src/blackbox_ai/cache/) — canonical request keys, the fail-open lookup gate, and the TTL-indexed store.
- [`src/blackbox_ai/replay.py`](../../src/blackbox_ai/replay.py) — export an interaction as a portable, re-runnable artifact.

## Next

[Step 07](../07-production-hardening) makes the secure choice the default — and is
honest about everything still missing for real production.

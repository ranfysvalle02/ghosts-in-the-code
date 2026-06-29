"""Step 01 - the invisible dashcam: a fail-open pass-through relay.

A single generic handler relays *every* provider. There is no per-provider code
on the hot path - just a tiny table of base URLs and a generic relay that streams
bytes straight back. This is the whole "data plane" idea in one file: be dumb, be
fast, get out of the way.

Run it:

    uv run --with fastapi --with "uvicorn[standard]" --with httpx uvicorn app:app --port 8000

Then point any SDK at it, e.g. OpenAI:

    from openai import OpenAI
    client = OpenAI(base_url="http://localhost:8000/openai/v1", api_key="sk-...")

The client thinks it's talking to OpenAI. It's talking to this relay, which
forwards every byte faithfully and streams the response back unchanged.

Canonical counterparts in the full project:
    src/blackbox_ai/proxy/relay.py        (the real generic relay)
    src/blackbox_ai/providers/catalog.py  (the real provider table)
    src/blackbox_ai/api/relay_routes.py   (the catch-all route)
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

# prefix -> upstream base URL. Adding a provider is one line (additive).
PROVIDERS: dict[str, str] = {
    "openai": "https://api.openai.com",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "ollama": "http://localhost:11434",
}

# Hop-by-hop / length headers we must not echo back verbatim, since we re-stream.
_DROP_RESPONSE_HEADERS = {"content-length", "content-encoding", "transfer-encoding", "connection"}
_DROP_REQUEST_HEADERS = {"host", "content-length"}

app = FastAPI(title="Step 01 - pass-through relay")
_client = httpx.AsyncClient(timeout=httpx.Timeout(None))


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.api_route(
    "/{provider}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def relay(provider: str, path: str, request: Request) -> StreamingResponse:
    base = PROVIDERS.get(provider)
    if base is None:
        return StreamingResponse(
            iter([b'{"error":"unknown provider"}']),
            status_code=404,
            media_type="application/json",
        )

    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQUEST_HEADERS}

    upstream = _client.build_request(
        request.method,
        f"{base}/{path}",
        params=request.query_params,
        content=body,
        headers=headers,
    )
    response = await _client.send(upstream, stream=True)

    async def passthrough() -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_raw():
                yield chunk  # -> client, immediately
        finally:
            await response.aclose()

    return StreamingResponse(
        passthrough(),
        status_code=response.status_code,
        headers={
            k: v for k, v in response.headers.items() if k.lower() not in _DROP_RESPONSE_HEADERS
        },
    )

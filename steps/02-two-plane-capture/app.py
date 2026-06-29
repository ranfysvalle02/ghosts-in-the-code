"""Step 02 - the recorder that can't crash the plane.

We add recording to the Step 01 relay, but split the system into two planes that
physically cannot hurt each other:

* **Data plane** - the relay. Streams bytes to the client and *tees* a copy into a
  bounded queue. It never parses, never waits on storage.
* **Telemetry plane** - a background worker drains the queue and does the slow,
  fallible work (here: building a record and stashing it in memory). If it falls
  behind, it *drops* work and counts it. It never reaches back and touches the
  request.

The whole philosophy is one line: ``except asyncio.QueueFull -> drop``. The worst
thing that happens under load is we lose a *recording*, never a *response*.

This snapshot uses an in-memory list as the "database" so it runs with no MongoDB
and no API keys. Inspect what was captured at GET /_captures.

Run it:

    uv run --with fastapi --with "uvicorn[standard]" --with httpx uvicorn app:app --port 8000

Canonical counterparts:
    src/blackbox_ai/proxy/tee.py            (the streaming tee)
    src/blackbox_ai/telemetry/capture.py    (the bounded capture buffer)
    src/blackbox_ai/telemetry/pipeline.py   (the queue + workers + drop-on-full)
    src/blackbox_ai/telemetry/sink_mongo.py (the real sink; here it's a list)
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

PROVIDERS: dict[str, str] = {
    "openai": "https://api.openai.com",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "ollama": "http://localhost:11434",
}

_DROP_RESPONSE_HEADERS = {"content-length", "content-encoding", "transfer-encoding", "connection"}
_DROP_REQUEST_HEADERS = {"host", "content-length"}

MAX_CAPTURE_BYTES = 64_000
QUEUE_MAXSIZE = 1000


@dataclass
class RawCapture:
    request_id: str
    provider: str
    path: str
    status: int
    streamed: bool
    body: bytes
    ttft_ms: float | None
    latency_ms: float
    truncated: bool


class CaptureBuffer:
    """Bounded, synchronous, non-raising mirror of the response stream.

    Because ``observe`` is cheap and synchronous it never yields the event loop,
    and because the tee only advances when the client pulls the next chunk, the
    capture inherits the client's backpressure for free.
    """

    def __init__(self) -> None:
        self._chunks = bytearray()
        self._start = time.monotonic()
        self.ttft_ms: float | None = None
        self.truncated = False

    def observe(self, chunk: bytes) -> None:
        if self.ttft_ms is None:
            self.ttft_ms = (time.monotonic() - self._start) * 1000.0
        if len(self._chunks) < MAX_CAPTURE_BYTES:
            self._chunks += chunk
        else:
            self.truncated = True  # past the cap we flag instead of growing

    def finalize(
        self, *, request_id: str, provider: str, path: str, status: int, streamed: bool
    ) -> RawCapture:
        return RawCapture(
            request_id=request_id,
            provider=provider,
            path=path,
            status=status,
            streamed=streamed,
            body=bytes(self._chunks),
            ttft_ms=self.ttft_ms,
            latency_ms=(time.monotonic() - self._start) * 1000.0,
            truncated=self.truncated,
        )


async def tee_stream(
    source: AsyncIterator[bytes], observe: Callable[[bytes], None]
) -> AsyncIterator[bytes]:
    async for chunk in source:
        observe(chunk)  # mirror to telemetry
        yield chunk  # -> client


# The "telemetry plane" state: an in-memory stand-in for MongoDB.
INTENTS: list[dict[str, Any]] = []
QUEUE: asyncio.Queue[RawCapture] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
METRICS = {"captured": 0, "dropped": 0}


def submit(capture: RawCapture) -> None:
    """Hand a capture to the telemetry plane - never blocking the relay."""
    try:
        QUEUE.put_nowait(capture)
    except asyncio.QueueFull:
        METRICS["dropped"] += 1  # shed load; the response already went out


async def telemetry_worker() -> None:
    while True:
        capture = await QUEUE.get()
        try:
            INTENTS.append(
                {
                    "request_id": capture.request_id,
                    "provider": capture.provider,
                    "path": capture.path,
                    "http_status": capture.status,
                    "streamed": capture.streamed,
                    "performance": {"ttft_ms": capture.ttft_ms, "latency_ms": capture.latency_ms},
                    "response_truncated": capture.truncated,
                    # A real sink parses this; here we keep a short preview.
                    "raw_response_preview": capture.body[:240].decode("utf-8", "replace"),
                }
            )
            METRICS["captured"] += 1
        finally:
            QUEUE.task_done()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    worker = asyncio.create_task(telemetry_worker())
    try:
        yield
    finally:
        worker.cancel()


app = FastAPI(title="Step 02 - two-plane capture", lifespan=lifespan)
_client = httpx.AsyncClient(timeout=httpx.Timeout(None))


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/_captures")
async def captures() -> JSONResponse:
    """Peek at the telemetry plane (not part of the relay)."""
    return JSONResponse({"metrics": METRICS, "queue_depth": QUEUE.qsize(), "intents": INTENTS})


@app.api_route("/{provider}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def relay(provider: str, path: str, request: Request) -> StreamingResponse:
    base = PROVIDERS.get(provider)
    if base is None:
        return StreamingResponse(
            iter([b'{"error":"unknown provider"}']), status_code=404, media_type="application/json"
        )

    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQUEST_HEADERS}

    upstream = _client.build_request(
        request.method, f"{base}/{path}", params=request.query_params, content=body, headers=headers
    )
    response = await _client.send(upstream, stream=True)
    content_type = response.headers.get("content-type", "")
    streamed = "event-stream" in content_type or "x-ndjson" in content_type
    buffer = CaptureBuffer()

    async def passthrough() -> AsyncIterator[bytes]:
        try:
            async for chunk in tee_stream(response.aiter_raw(), buffer.observe):
                yield chunk
        finally:
            await response.aclose()
            # The data plane's only telemetry touch: a non-blocking hand-off.
            submit(
                buffer.finalize(
                    request_id=request_id,
                    provider=provider,
                    path=path,
                    status=response.status_code,
                    streamed=streamed,
                )
            )

    return StreamingResponse(
        passthrough(),
        status_code=response.status_code,
        headers={
            k: v for k, v in response.headers.items() if k.lower() not in _DROP_RESPONSE_HEADERS
        }
        | {"x-request-id": request_id},
    )

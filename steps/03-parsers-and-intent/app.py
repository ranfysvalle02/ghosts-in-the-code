"""Step 03 - the polymorphic Intent Document (and why "intent" is biased).

Two planes (Step 02) are in place. Now the telemetry worker *parses* the captured
bytes into a structured record. Every provider speaks a different dialect, so we
dispatch a tiny per-provider parser and lift the results into one **Intent
Document** - a single dict whose shape follows the data instead of a rigid table.

The honest part: the parser pulls out ``chain_of_thought`` where a provider
exposes it (Anthropic ``thinking`` deltas, OpenAI-style ``reasoning_content``).
That field is the model's **stated** rationale - self-reported, not a trace of the
real computation, and exposed unevenly across providers. So an Intent Document is
a *decision record* (inputs + actions + stated reasoning), not a confession. The
trustworthy fields are the objective ones (``tools_called``, and the diff/params a
real build would add). See ../../docs/intent-is-biased.md.

Runs in memory, no MongoDB/keys. Inspect at GET /_captures.

    uv run --with fastapi --with "uvicorn[standard]" --with httpx uvicorn app:app --port 8000

Canonical counterparts:
    src/blackbox_ai/telemetry/parsers/      (the real per-provider parsers)
    src/blackbox_ai/telemetry/models.py     (the real IntentDocument / IntentTelemetry)
"""

from __future__ import annotations

import asyncio
import json
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
MAX_CAPTURE_BYTES = 256_000
QUEUE_MAXSIZE = 1000


# --------------------------------------------------------------------------- #
# Tiny, illustrative per-provider parsers. The real ones are far more complete;
# these show the *shape* of the polymorphism.
# --------------------------------------------------------------------------- #
def _sse_data_lines(text: str) -> list[str]:
    return [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]


def parse_openai(body: bytes) -> dict[str, Any]:
    content, reasoning, tools, finish = [], [], [], None
    for data in _sse_data_lines(body.decode("utf-8", "replace")):
        if data == "[DONE]":
            continue
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        for choice in obj.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content.append(delta["content"])
            if delta.get("reasoning_content"):
                reasoning.append(delta["reasoning_content"])
            for call in delta.get("tool_calls") or []:
                fn = call.get("function") or {}
                tools.append({"name": fn.get("name"), "arguments": fn.get("arguments")})
            finish = choice.get("finish_reason") or finish
    return _telemetry(content, reasoning, tools, finish, parsed=bool(content or reasoning or tools))


def parse_anthropic(body: bytes) -> dict[str, Any]:
    content, thinking, finish = [], [], None
    for data in _sse_data_lines(body.decode("utf-8", "replace")):
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        delta = obj.get("delta") or {}
        if delta.get("type") == "text_delta":
            content.append(delta.get("text", ""))
        elif delta.get("type") == "thinking_delta":
            thinking.append(delta.get("thinking", ""))
        if delta.get("stop_reason"):
            finish = delta["stop_reason"]
    return _telemetry(content, thinking, [], finish, parsed=bool(content or thinking))


def parse_ollama(body: bytes) -> dict[str, Any]:
    content, finish = [], None
    for line in body.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        content.append((obj.get("message") or {}).get("content", ""))
        if obj.get("done"):
            finish = obj.get("done_reason") or "stop"
    return _telemetry(content, [], [], finish, parsed=bool(content))


def _telemetry(content, reasoning, tools, finish, *, parsed: bool) -> dict[str, Any]:
    return {
        "content": "".join(content) or None,
        # Self-reported reasoning. A lead, not ground truth (docs/intent-is-biased.md).
        "chain_of_thought": "".join(reasoning) or None,
        "tools_called": tools,
        "finish_reason": finish,
        "parse_status": "ok" if parsed else "unparsed",
    }


_PARSERS: dict[str, Callable[[bytes], dict[str, Any]]] = {
    "openai": parse_openai,
    "azure": parse_openai,
    "anthropic": parse_anthropic,
    "ollama": parse_ollama,
}


def parse(provider: str, body: bytes) -> dict[str, Any]:
    parser = _PARSERS.get(provider)
    if parser is None:
        return _telemetry([], [], [], None, parsed=False)
    return parser(body)


# --------------------------------------------------------------------------- #
# Two-plane machinery (unchanged from Step 02), now feeding the parser.
# --------------------------------------------------------------------------- #
@dataclass
class RawCapture:
    request_id: str
    provider: str
    path: str
    status: int
    streamed: bool
    request_payload: dict[str, Any] | None
    body: bytes
    ttft_ms: float | None
    latency_ms: float
    truncated: bool


class CaptureBuffer:
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
            self.truncated = True

    def finalize(self, **kw: Any) -> RawCapture:
        return RawCapture(
            body=bytes(self._chunks),
            ttft_ms=self.ttft_ms,
            latency_ms=(time.monotonic() - self._start) * 1000.0,
            truncated=self.truncated,
            **kw,
        )


async def tee_stream(
    source: AsyncIterator[bytes], observe: Callable[[bytes], None]
) -> AsyncIterator[bytes]:
    async for chunk in source:
        observe(chunk)
        yield chunk


INTENTS: list[dict[str, Any]] = []
QUEUE: asyncio.Queue[RawCapture] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
METRICS = {"captured": 0, "dropped": 0}


def submit(capture: RawCapture) -> None:
    try:
        QUEUE.put_nowait(capture)
    except asyncio.QueueFull:
        METRICS["dropped"] += 1


async def telemetry_worker() -> None:
    while True:
        capture = await QUEUE.get()
        try:
            telemetry = parse(capture.provider, capture.body)
            # The polymorphic Intent Document: normalized fields + raw payload.
            INTENTS.append(
                {
                    "request_id": capture.request_id,
                    "provider": capture.provider,
                    "endpoint": capture.path,
                    "http_status": capture.status,
                    "streamed": capture.streamed,
                    "performance": {"ttft_ms": capture.ttft_ms, "latency_ms": capture.latency_ms},
                    "response_truncated": capture.truncated,
                    "intent_telemetry": telemetry,
                    "raw_payload": capture.request_payload,  # verbatim request, for replay
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


app = FastAPI(title="Step 03 - parsers and intent", lifespan=lifespan)
_client = httpx.AsyncClient(timeout=httpx.Timeout(None))


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/_captures")
async def captures() -> JSONResponse:
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
    try:
        request_payload = json.loads(body) if body else None
    except json.JSONDecodeError:
        request_payload = None
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
            submit(
                buffer.finalize(
                    request_id=request_id,
                    provider=provider,
                    path=path,
                    status=response.status_code,
                    streamed=streamed,
                    request_payload=request_payload,
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

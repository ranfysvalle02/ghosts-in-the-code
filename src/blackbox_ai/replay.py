"""Export a captured interaction as a portable, re-runnable artifact.

Replay is deliberately *not* something the gateway does. The data plane is a
witness that touches nothing - it never originates a request. Instead, this
module hands back the verbatim, decrypted *inputs* of a past interaction (the
provider, method, upstream path, and exact request body) as a self-contained
artifact you can re-issue however you like: against a new or cheaper model for a
migration, into an eval suite, or as a regression fixture. You own the inputs;
what you do with them is your call.

Two deliberate boundaries:

* **Export, not auto-replay.** A captured agent request can carry destructive
  tool calls, so re-issuing one must be a deliberate human act, never a gateway
  feature. We emit the artifact; you decide whether and where to run it.
* **Reproducible inputs, not outputs.** The request body is reproduced exactly.
  The *response* of any re-issue is a fresh generation, not a deterministic
  reproduction (that guarantee belongs only to the exact-match response cache).

When Queryable Encryption is on, the collection is read through the encrypting
client, so ``raw_payload`` comes back decrypted.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import orjson
from pymongo.asynchronous.collection import AsyncCollection

from blackbox_ai.errors import IntentNotFoundError
from blackbox_ai.logging import get_logger

__all__ = ["ReplayArtifact", "ReplayService"]

_log = get_logger("blackbox_ai.replay")

# Just the fields needed to reconstruct the original request plus the grouping
# metadata. raw_payload is the only encrypted field here; it is decrypted on read
# by the encrypting client.
_PROJECTION: dict[str, Any] = {
    "_id": 0,
    "request_id": 1,
    "provider": 1,
    "method": 1,
    "endpoint": 1,
    "model_requested": 1,
    "timestamp": 1,
    "project_id": 1,
    "session_id": 1,
    "developer_id": 1,
    "streamed": 1,
    "raw_payload": 1,
}

# (artifact field -> sovereign header) so a re-issue is grouped like the original.
_METADATA_HEADERS: tuple[tuple[str, str], ...] = (
    ("project_id", "x-project-id"),
    ("session_id", "x-agent-session"),
    ("developer_id", "x-developer-id"),
)


@dataclass(frozen=True, slots=True)
class ReplayArtifact:
    """A self-contained, re-runnable record of one interaction's inputs."""

    request_id: str
    provider: str
    method: str
    endpoint: str
    raw_payload: dict[str, Any] | None
    model_requested: str | None = None
    timestamp: datetime | None = None
    project_id: str | None = None
    session_id: str | None = None
    developer_id: str | None = None
    streamed: bool = False

    def gateway_path(self) -> str:
        """The gateway route that re-issues this request: ``/{provider}/{path}``."""
        return f"/{self.provider}/{self.endpoint.lstrip('/')}"

    def metadata_headers(self) -> dict[str, str]:
        """Sovereign grouping headers reconstructed from the captured context."""
        headers: dict[str, str] = {}
        for field_name, header in _METADATA_HEADERS:
            value = getattr(self, field_name)
            if value:
                headers[header] = value
        return headers

    def as_dict(self) -> dict[str, Any]:
        """A JSON-friendly descriptor of the full artifact."""
        return {
            "request_id": self.request_id,
            "provider": self.provider,
            "method": self.method,
            "endpoint": self.endpoint,
            "gateway_path": self.gateway_path(),
            "streamed": self.streamed,
            "model_requested": self.model_requested,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "metadata": self.metadata_headers(),
            "raw_payload": self.raw_payload,
        }

    def as_curl(self, *, base_url: str, token: str | None = None) -> str:
        """Render a ready-to-run ``curl`` that re-issues this request via the gateway.

        Every dynamic part is shell-quoted, so prompts containing quotes, spaces,
        or newlines round-trip safely. The original query string is not captured,
        so providers that carry parameters in the URL (e.g. Gemini's ``alt=sse``)
        may need it re-added by hand.
        """
        url = f"{base_url.rstrip('/')}{self.gateway_path()}"
        lines = [f"curl -X {self.method} {shlex.quote(url)}"]
        headers = {"content-type": "application/json", **self.metadata_headers()}
        if token:
            headers["x-gateway-token"] = token
        lines.extend(f"-H {shlex.quote(f'{name}: {value}')}" for name, value in headers.items())
        if self.raw_payload is not None:
            body = orjson.dumps(self.raw_payload).decode("utf-8")
            lines.append(f"--data {shlex.quote(body)}")
        return " \\\n  ".join(lines)


class ReplayService:
    """Fetches a past interaction's inputs as a :class:`ReplayArtifact`.

    Read-only by design: it never re-issues anything. It simply projects the
    request-reconstruction fields out of the intents collection (decrypting
    ``raw_payload`` when QE is enabled) and wraps them in a portable artifact.
    """

    def __init__(self, collection: AsyncCollection[dict[str, Any]]) -> None:
        self._collection = collection

    async def fetch(self, request_id: str) -> ReplayArtifact:
        """Return the replay artifact for ``request_id`` (raises if unknown)."""
        if not request_id.strip():
            raise IntentNotFoundError("A request_id is required.")
        doc = await self._collection.find_one({"request_id": request_id}, projection=_PROJECTION)
        if doc is None:
            raise IntentNotFoundError(f"No captured interaction for request_id {request_id!r}.")
        _log.info("replay_export", request_id=request_id, provider=doc.get("provider"))
        return ReplayArtifact(
            request_id=doc["request_id"],
            provider=doc["provider"],
            method=doc.get("method", "POST"),
            endpoint=doc.get("endpoint", ""),
            raw_payload=doc.get("raw_payload"),
            model_requested=doc.get("model_requested"),
            timestamp=doc.get("timestamp"),
            project_id=doc.get("project_id"),
            session_id=doc.get("session_id"),
            developer_id=doc.get("developer_id"),
            streamed=bool(doc.get("streamed", False)),
        )

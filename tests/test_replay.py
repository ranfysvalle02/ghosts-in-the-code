"""Replay export: fetching the portable artifact and rendering it safely."""

from __future__ import annotations

import shlex
from datetime import UTC, datetime
from typing import Any

import orjson
import pytest

from blackbox_ai.errors import IntentNotFoundError
from blackbox_ai.replay import ReplayArtifact, ReplayService
from tests.conftest import build_harness, default_settings

_DOC: dict[str, Any] = {
    "request_id": "req-123",
    "provider": "ollama",
    "method": "POST",
    "endpoint": "api/chat",
    "model_requested": "qwen3:14b",
    "timestamp": datetime(2026, 6, 27, 1, 14, tzinfo=UTC),
    "project_id": "blackbox-ai-demo",
    "session_id": "demo-session-001",
    "developer_id": "demo-engineer",
    "streamed": True,
    # An apostrophe in the prompt is the classic shell-injection landmine.
    "raw_payload": {
        "model": "qwen3:14b",
        "messages": [{"role": "user", "content": "why's owning intent data matter?"}],
    },
}


class _FakeCollection:
    """Minimal stand-in that matches on ``request_id`` like the real collection."""

    def __init__(self, doc: dict[str, Any] | None) -> None:
        self._doc = doc
        self.last_filter: dict[str, Any] | None = None
        self.last_projection: dict[str, Any] | None = None

    async def find_one(
        self, filter: dict[str, Any], *, projection: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        self.last_filter = filter
        self.last_projection = projection
        if self._doc is None or filter.get("request_id") != self._doc.get("request_id"):
            return None
        return self._doc


def _artifact() -> ReplayArtifact:
    return ReplayArtifact(
        request_id="req-123",
        provider="ollama",
        method="POST",
        endpoint="api/chat",
        raw_payload=_DOC["raw_payload"],
        project_id="proj",
        session_id="sess",
        developer_id="dev",
        streamed=True,
    )


async def test_fetch_returns_artifact() -> None:
    coll = _FakeCollection(_DOC)
    artifact = await ReplayService(coll).fetch("req-123")

    assert artifact.provider == "ollama"
    assert artifact.gateway_path() == "/ollama/api/chat"
    assert artifact.raw_payload is not None
    assert artifact.raw_payload["model"] == "qwen3:14b"
    assert artifact.metadata_headers() == {
        "x-project-id": "blackbox-ai-demo",
        "x-agent-session": "demo-session-001",
        "x-developer-id": "demo-engineer",
    }
    assert coll.last_filter == {"request_id": "req-123"}
    # Only the reconstruction fields are projected (raw_payload included).
    assert coll.last_projection is not None
    assert coll.last_projection["raw_payload"] == 1


async def test_fetch_unknown_request_id_raises() -> None:
    with pytest.raises(IntentNotFoundError):
        await ReplayService(_FakeCollection(None)).fetch("missing")


async def test_fetch_blank_request_id_raises() -> None:
    with pytest.raises(IntentNotFoundError):
        await ReplayService(_FakeCollection(_DOC)).fetch("   ")


def test_as_curl_round_trips_through_a_shell() -> None:
    artifact = _artifact()
    curl = artifact.as_curl(base_url="http://localhost:8000/", token="tok")

    # Undo the cosmetic line continuations the way a shell would, then tokenise.
    tokens = shlex.split(curl.replace("\\\n", ""))

    assert tokens[0] == "curl"
    assert "http://localhost:8000/ollama/api/chat" in tokens
    assert "x-gateway-token: tok" in tokens
    assert "x-project-id: proj" in tokens
    # The body survives quoting intact despite the apostrophe.
    body = tokens[tokens.index("--data") + 1]
    assert orjson.loads(body) == artifact.raw_payload


def test_as_curl_omits_data_when_no_payload() -> None:
    artifact = ReplayArtifact(
        request_id="r", provider="openai", method="GET", endpoint="v1/models", raw_payload=None
    )
    curl = artifact.as_curl(base_url="http://localhost:8000")
    assert "--data" not in curl


async def test_admin_replay_endpoint_returns_artifact() -> None:
    settings = default_settings()
    async with build_harness(
        settings, replay_service=ReplayService(_FakeCollection(_DOC))
    ) as harness:
        # No admin token configured -> the endpoint is disabled (503), never open.
        disabled = await harness.client.get("/admin/intents/req-123/replay")
        assert disabled.status_code == 503

    settings = default_settings(admin_token="admin-secret")
    async with build_harness(
        settings, replay_service=ReplayService(_FakeCollection(_DOC))
    ) as harness:
        unauth = await harness.client.get("/admin/intents/req-123/replay")
        assert unauth.status_code == 401

        ok = await harness.client.get(
            "/admin/intents/req-123/replay", headers={"x-admin-token": "admin-secret"}
        )
        assert ok.status_code == 200
        body = ok.json()
        assert body["provider"] == "ollama"
        assert body["gateway_path"] == "/ollama/api/chat"
        assert body["raw_payload"]["model"] == "qwen3:14b"
        assert "curl" in body and body["curl"].startswith("curl -X POST")

        missing = await harness.client.get(
            "/admin/intents/nope/replay", headers={"x-admin-token": "admin-secret"}
        )
        assert missing.status_code == 404
        assert missing.json()["error"]["type"] == "intent_not_found"

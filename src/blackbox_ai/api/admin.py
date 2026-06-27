"""Admin API: vector "time-travel" search and portable replay export.

Both endpoints are guarded by ``GATEWAY_ADMIN_TOKEN``. When the token is unset
they are disabled (503) so a misconfiguration never exposes telemetry
unauthenticated.
"""

from __future__ import annotations

import orjson
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from starlette.responses import Response

from blackbox_ai.api._auth import require_admin_token
from blackbox_ai.errors import SearchUnavailableError
from blackbox_ai.search import SearchMode
from blackbox_ai.state import AppState

__all__ = ["router"]

router = APIRouter(prefix="/admin", tags=["admin"])


class SearchRequest(BaseModel):
    """Body for ``POST /admin/search``."""

    query: str = Field(min_length=1)
    mode: SearchMode = SearchMode.HYBRID
    project_id: str | None = None
    session_id: str | None = None
    provider: str | None = None
    developer_id: str | None = None
    k: int = Field(default=5, ge=1, le=50)


def _state(request: Request) -> AppState:
    state: AppState = request.app.state.gateway
    return state


@router.post("/search")
async def admin_search(request: Request, body: SearchRequest) -> Response:
    """Run a vector search and return the most similar Intent Documents."""
    state = _state(request)
    require_admin_token(request, state.settings)
    if state.search_service is None:
        raise SearchUnavailableError(
            "Vector search is not configured (set GATEWAY_EMBEDDINGS_PROVIDER=voyage "
            "and VOYAGE_API_KEY, then run `blackbox-ai init`)."
        )
    results = await state.search_service.search(
        body.query,
        mode=body.mode,
        project_id=body.project_id,
        session_id=body.session_id,
        provider=body.provider,
        developer_id=body.developer_id,
        k=body.k,
    )
    payload = {
        "query": body.query,
        "mode": results.mode.value,
        "count": len(results.hits),
        "results": [{"score": hit.score, **hit.document} for hit in results.hits],
    }
    return Response(content=orjson.dumps(payload), media_type="application/json")


@router.get("/intents/{request_id}/replay")
async def export_replay_artifact(request: Request, request_id: str) -> Response:
    """Return the portable replay artifact (verbatim, decrypted inputs) for one interaction.

    This is an *export*, not an action: the gateway never re-issues the request.
    The artifact carries everything needed to replay it yourself - including a
    ready-to-run ``curl`` against this gateway.
    """
    state = _state(request)
    require_admin_token(request, state.settings)
    if state.replay_service is None:
        raise SearchUnavailableError("Replay export is unavailable (no MongoDB connection).")
    artifact = await state.replay_service.fetch(request_id)
    payload = {
        **artifact.as_dict(),
        "curl": artifact.as_curl(base_url=str(request.base_url)),
    }
    return Response(content=orjson.dumps(payload), media_type="application/json")

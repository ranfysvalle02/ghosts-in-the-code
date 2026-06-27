"""``blackbox-ai`` command-line interface.

Subcommands
-----------
* ``serve``    - run the gateway with uvicorn (the default when no subcommand).
* ``gen-key``  - print a fresh base64 local KMS master key.
* ``init``     - create encrypted collections, indexes, TTL, and the vector index.
* ``search``   - run a vector "time-travel" search from the terminal.
* ``export``   - export a captured interaction as a portable, replayable artifact.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import NoReturn

import orjson
import uvicorn

from blackbox_ai.bootstrap import (
    build_embedder,
    build_encryption_manager,
    ensure_storage,
)
from blackbox_ai.config import Settings, get_settings
from blackbox_ai.db.mongo import create_client, ping
from blackbox_ai.errors import GatewayError
from blackbox_ai.logging import configure_logging, get_logger
from blackbox_ai.replay import ReplayService
from blackbox_ai.search import SearchMode, SearchService
from blackbox_ai.security.encryption import generate_local_key

__all__ = ["main"]

_log = get_logger("blackbox_ai.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blackbox-ai", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Run the gateway (default).")
    serve.set_defaults(func=_cmd_serve)

    gen_key = sub.add_parser("gen-key", help="Print a fresh base64 96-byte master key.")
    gen_key.set_defaults(func=_cmd_gen_key)

    init = sub.add_parser("init", help="Bootstrap collections, indexes, and vector index.")
    init.set_defaults(func=_cmd_init)

    search = sub.add_parser("search", help="Vector search over captured intent.")
    search.add_argument("query", help="Natural-language question.")
    search.add_argument(
        "--mode",
        choices=[m.value for m in SearchMode],
        default=SearchMode.HYBRID.value,
        help="Retrieval mode (default hybrid; falls back to vector if unsupported).",
    )
    search.add_argument("--project", default=None, help="Filter by X-Project-ID.")
    search.add_argument("--session", default=None, help="Filter by X-Agent-Session.")
    search.add_argument("--provider", default=None, help="Filter by provider name.")
    search.add_argument("--k", type=int, default=5, help="Number of results (default 5).")
    search.set_defaults(func=_cmd_search)

    export = sub.add_parser(
        "export", help="Export a captured interaction as a portable, replayable artifact."
    )
    export.add_argument("request_id", help="The X-Request-ID of the interaction to export.")
    export.add_argument(
        "--as",
        dest="fmt",
        choices=["payload", "request", "curl"],
        default="payload",
        help=(
            "Output form: the verbatim request body (default), a full request "
            "descriptor, or a ready-to-run curl against the gateway."
        ),
    )
    export.add_argument(
        "--gateway-url",
        default="http://localhost:8000",
        help="Gateway base URL used to render `--as curl` (default http://localhost:8000).",
    )
    export.add_argument(
        "--token", default=None, help="x-gateway-token to embed in the rendered `--as curl`."
    )
    export.set_defaults(func=_cmd_export)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", _cmd_serve)
    func(args)


def _cmd_serve(_args: argparse.Namespace) -> None:
    settings = get_settings()
    uvicorn.run(
        "blackbox_ai.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


def _cmd_gen_key(_args: argparse.Namespace) -> None:
    # Bare key on stdout so it composes with shell tooling, e.g.
    #   echo "GATEWAY_ENCRYPTION_KEY=$(blackbox-ai gen-key)" >> .env
    print(generate_local_key())


def _cmd_init(_args: argparse.Namespace) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)
    asyncio.run(_run_init(settings))


def _cmd_search(args: argparse.Namespace) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)
    asyncio.run(_run_search(settings, args))


def _cmd_export(args: argparse.Namespace) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)
    asyncio.run(_run_export(settings, args))


async def _run_init(settings: Settings) -> None:
    try:
        encryption = build_encryption_manager(settings)
    except ValueError as exc:
        _fail(f"Encryption is enabled but misconfigured: {exc}")
    embedder = build_embedder(settings)
    client = create_client(settings)
    try:
        await ping(client)
        await ensure_storage(
            settings,
            admin_client=client,
            encryption=encryption,
            embedder=embedder,
            wait_vector=True,
        )
    finally:
        await client.close()
    print(
        f"Initialized '{settings.mongo_db}': encryption="
        f"{'on' if encryption else 'off'}, embeddings={embedder.model_name}, "
        f"cache={'on' if settings.cache_enabled else 'off'}."
    )


async def _run_search(settings: Settings, args: argparse.Namespace) -> None:
    embedder = build_embedder(settings)
    if embedder.dims == 0:
        _fail(
            "Embeddings are not configured. Set GATEWAY_EMBEDDINGS_PROVIDER=voyage "
            "and VOYAGE_API_KEY."
        )
    try:
        encryption = build_encryption_manager(settings)
    except ValueError as exc:
        _fail(f"Encryption is enabled but misconfigured: {exc}")
    # Read through the encrypting client so encrypted intent text is decrypted.
    client = encryption.build_encrypting_client() if encryption else create_client(settings)
    try:
        service = SearchService(
            client[settings.mongo_db][settings.mongo_collection],
            embedder,
            vector_index_name=settings.vector_index_name,
            search_index_name=settings.search_index_name,
        )
        try:
            results = await service.search(
                args.query,
                mode=SearchMode(args.mode),
                project_id=args.project,
                session_id=args.session,
                provider=args.provider,
                k=args.k,
            )
        except GatewayError as exc:
            _fail(exc.message)
        _dump_json(
            {
                "query": args.query,
                "mode": results.mode.value,
                "count": len(results.hits),
                "results": [{"score": hit.score, **hit.document} for hit in results.hits],
            }
        )
    finally:
        await client.close()


async def _run_export(settings: Settings, args: argparse.Namespace) -> None:
    try:
        encryption = build_encryption_manager(settings)
    except ValueError as exc:
        _fail(f"Encryption is enabled but misconfigured: {exc}")
    # Read through the encrypting client so the captured raw_payload is decrypted.
    client = encryption.build_encrypting_client() if encryption else create_client(settings)
    try:
        service = ReplayService(client[settings.mongo_db][settings.mongo_collection])
        try:
            artifact = await service.fetch(args.request_id)
        except GatewayError as exc:
            _fail(exc.message)
        if args.fmt == "curl":
            print(artifact.as_curl(base_url=args.gateway_url, token=args.token))
        elif args.fmt == "request":
            _dump_json(artifact.as_dict())
        else:  # payload: the verbatim request body, ready to replay as you wish.
            _dump_json(artifact.raw_payload)
    finally:
        await client.close()


def _dump_json(value: object) -> None:
    sys.stdout.buffer.write(orjson.dumps(value, option=orjson.OPT_INDENT_2))
    sys.stdout.buffer.write(b"\n")


def _fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Smoke tooling for LeanKit external-channel pilot flows."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "http://localhost:8181"


def _load_repo_env_defaults() -> None:
    """Load repo-local .env values when the caller shell has not exported them."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _resolve_base_url() -> str:
    """Resolve the Archon control-plane base URL."""
    return (
        os.getenv("LEANKIT_CONTROL_PLANE_URL")
        or os.getenv("ARCHON_SERVER_URL")
        or os.getenv("ARCHON_URL")
        or DEFAULT_BASE_URL
    ).rstrip("/")


def _request(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Issue one JSON HTTP request and return parsed JSON or a structured error."""
    body = None
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            text = response.read().decode(charset)
            return True, json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(text)
        except json.JSONDecodeError:
            detail = {"raw": text}
        return False, {"status": exc.code, "url": url, "detail": detail}
    except urllib.error.URLError as exc:
        return False, {"status": "network-error", "url": url, "detail": str(exc)}


def _print_result(label: str, ok: bool, payload: dict[str, Any]) -> None:
    """Print one smoke result in a readable form."""
    banner = "PASS" if ok else "FAIL"
    print(f"\n[{banner}] {label}")
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))


def _build_telegram_update(message_text: str) -> dict[str, Any]:
    """Create one Telegram message update payload for webhook smoke tests."""
    return {
        "update_id": 990001,
        "message": {
            "message_id": 880001,
            "date": 1774060800,
            "text": message_text,
            "chat": {
                "id": os.getenv("LEANKIT_TELEGRAM_CHAT_ID") or "pilot-chat",
                "type": "private",
            },
            "from": {
                "id": 550001,
                "username": "pilot-owner",
                "first_name": "Pilot",
            },
        },
    }


def _build_callback_update(approval_id: str, decision: str) -> dict[str, Any]:
    """Create one Telegram callback update payload."""
    return {
        "update_id": 990002,
        "callback_query": {
            "id": "pilot-callback-001",
            "data": f"approval:{decision}:{approval_id}",
            "from": {
                "id": 550001,
                "username": "pilot-owner",
                "first_name": "Pilot",
            },
        },
    }


def _run_health(base_url: str) -> bool:
    """Check external-channel health surfaces."""
    all_ok = True
    for label, path in (
        ("Platform service health", "/api/services/health"),
        ("Telegram health", "/api/channels/telegram/health"),
        ("OpenClaw health", "/api/channels/openclaw/health"),
    ):
        ok, result = _request("GET", f"{base_url}{path}")
        _print_result(label, ok, result)
        all_ok &= ok
    return all_ok


def _run_telegram_webhook(
    base_url: str,
    *,
    message_text: str,
    approval_id: str | None,
    decision: str,
) -> bool:
    """Run Telegram webhook smoke flow for message and optional callback."""
    secret = os.getenv("LEANKIT_TELEGRAM_WEBHOOK_SECRET", "")
    ok, result = _request(
        "POST",
        f"{base_url}/api/channels/telegram/webhook",
        payload=_build_telegram_update(message_text),
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
    )
    _print_result("Telegram webhook message", ok, result)
    all_ok = ok

    if approval_id:
        ok, result = _request(
            "POST",
            f"{base_url}/api/channels/telegram/webhook",
            payload=_build_callback_update(approval_id, decision),
            headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        )
        _print_result(f"Telegram webhook callback ({decision})", ok, result)
        all_ok &= ok

    return all_ok


def _run_openclaw_ingest(
    base_url: str,
    *,
    title: str,
    summary: str,
    project_id: str | None,
    modality: str,
) -> bool:
    """Run one OpenClaw ingress smoke request."""
    secret = os.getenv("LEANKIT_OPENCLAW_INGEST_SECRET", "")
    ok, result = _request(
        "POST",
        f"{base_url}/api/channels/openclaw/ingest",
        payload={
            "request_type": "message",
            "title": title,
            "summary": summary,
            "project_id": project_id,
            "input_modality": modality,
            "input_text": summary,
            "request_id": "pilot-openclaw-message-001",
            "payload": {
                **({"voice_transcript": summary} if modality == "voice" else {}),
                "pilot_mode": True,
            },
        },
        headers={"X-OpenClaw-Secret": secret},
    )
    _print_result("OpenClaw ingest", ok, result)
    return ok


def _run_openclaw_architect(
    base_url: str,
    *,
    title: str,
    summary: str,
    project_id: str | None,
    modality: str,
    provider: str,
    model: str | None,
    command_sequence_id: str | None,
    step_key: str | None,
    clarification_answers: list[str],
    clarification_response_to_request_id: str | None,
) -> bool:
    """Run one OpenClaw architect-plan smoke request."""
    secret = os.getenv("LEANKIT_OPENCLAW_INGEST_SECRET", "")
    ok, result = _request(
        "POST",
        f"{base_url}/api/channels/openclaw/architect-plan",
        payload={
            "title": title,
            "summary": summary,
            "project_id": project_id,
            "input_modality": modality,
            "input_text": summary,
            "command_sequence_id": command_sequence_id,
            "step_key": step_key,
            "clarification_answers": clarification_answers,
            "clarification_response_to_request_id": clarification_response_to_request_id,
            "architect_provider": provider,
            "architect_model": model,
            "payload": {
                "pilot_mode": True,
                "request_source": "channel-smoke",
                **({"voice_transcript": summary} if modality == "voice" else {}),
            },
        },
        headers={"X-OpenClaw-Secret": secret},
    )
    _print_result("OpenClaw architect plan", ok, result)
    return ok


def _run_due_digest_cycle(base_url: str, *, force: bool, limit: int) -> bool:
    """Run one scheduler-backed due-digest cycle."""
    ok, result = _request(
        "POST",
        f"{base_url}/api/channels/telegram/due-digest-cycle",
        payload={
            "force": force,
            "limit": limit,
        },
    )
    _print_result("Telegram due-digest cycle", ok, result)
    return ok


def _build_parser() -> argparse.ArgumentParser:
    """Build CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=_resolve_base_url(), help="Archon control-plane base URL")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health", help="Check Telegram and OpenClaw health surfaces")

    telegram = subparsers.add_parser("telegram-webhook", help="Run Telegram webhook smoke flow")
    telegram.add_argument("--message-text", default="Pilot smoke: record a Telegram external request")
    telegram.add_argument("--approval-id")
    telegram.add_argument("--decision", choices=("approve", "reject"), default="approve")

    ingest = subparsers.add_parser("openclaw-ingest", help="Run one OpenClaw ingest smoke request")
    ingest.add_argument("--title", default="Pilot smoke OpenClaw request")
    ingest.add_argument("--summary", default="Create a pilot external request through OpenClaw ingress.")
    ingest.add_argument("--project-id")
    ingest.add_argument("--modality", choices=("voice", "text"), default="voice")

    architect = subparsers.add_parser("openclaw-architect", help="Run one OpenClaw architect-plan smoke request")
    architect.add_argument("--title", default="Pilot smoke architect request")
    architect.add_argument("--summary", default="Create an implementation plan for a pilot feature request.")
    architect.add_argument("--project-id")
    architect.add_argument("--modality", choices=("voice", "text"), default="voice")
    architect.add_argument("--provider", default="chatgpt-codex")
    architect.add_argument("--model")
    architect.add_argument("--sequence-id")
    architect.add_argument("--step-key", default="architect-plan")
    architect.add_argument("--clarification-answer", action="append", default=[])
    architect.add_argument("--response-to-request-id")

    digest = subparsers.add_parser("due-digest-cycle", help="Run one Telegram due-digest scheduler cycle")
    digest.add_argument("--force", action="store_true")
    digest.add_argument("--limit", type=int, default=10)

    pilot = subparsers.add_parser("pilot", help="Run the full live-pilot smoke sequence")
    pilot.add_argument("--project-id")
    pilot.add_argument("--approval-id")
    pilot.add_argument("--decision", choices=("approve", "reject"), default="approve")
    pilot.add_argument("--modality", choices=("voice", "text"), default="voice")
    pilot.add_argument("--provider", default="chatgpt-codex")
    pilot.add_argument("--model")
    pilot.add_argument("--sequence-id")
    pilot.add_argument("--force-digest", action="store_true")
    pilot.add_argument("--digest-limit", type=int, default=10)

    return parser


def main() -> int:
    """CLI entrypoint."""
    _load_repo_env_defaults()
    parser = _build_parser()
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    if args.command == "health":
        return 0 if _run_health(base_url) else 1
    if args.command == "telegram-webhook":
        ok = _run_telegram_webhook(
            base_url,
            message_text=args.message_text,
            approval_id=args.approval_id,
            decision=args.decision,
        )
        return 0 if ok else 1
    if args.command == "openclaw-ingest":
        ok = _run_openclaw_ingest(
            base_url,
            title=args.title,
            summary=args.summary,
            project_id=args.project_id,
            modality=args.modality,
        )
        return 0 if ok else 1
    if args.command == "openclaw-architect":
        ok = _run_openclaw_architect(
            base_url,
            title=args.title,
            summary=args.summary,
            project_id=args.project_id,
            modality=args.modality,
            provider=args.provider,
            model=args.model,
            command_sequence_id=args.sequence_id,
            step_key=args.step_key,
            clarification_answers=args.clarification_answer,
            clarification_response_to_request_id=args.response_to_request_id,
        )
        return 0 if ok else 1
    if args.command == "due-digest-cycle":
        ok = _run_due_digest_cycle(base_url, force=args.force, limit=args.limit)
        return 0 if ok else 1
    if args.command == "pilot":
        results = [
            _run_health(base_url),
            _run_openclaw_ingest(
                base_url,
                title="Pilot smoke OpenClaw request",
                summary="Create a pilot external request through OpenClaw ingress.",
                project_id=args.project_id,
                modality=args.modality,
            ),
            _run_openclaw_architect(
                base_url,
                title="Pilot smoke architect request",
                summary="Create an implementation plan for a pilot feature request.",
                project_id=args.project_id,
                modality=args.modality,
                provider=args.provider,
                model=args.model,
                command_sequence_id=args.sequence_id,
                step_key="architect-plan",
                clarification_answers=[],
                clarification_response_to_request_id=None,
            ),
            _run_telegram_webhook(
                base_url,
                message_text="Pilot smoke: record a Telegram external request",
                approval_id=args.approval_id,
                decision=args.decision,
            ),
            _run_due_digest_cycle(base_url, force=args.force_digest, limit=args.digest_limit),
        ]
        return 0 if all(results) else 1

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())

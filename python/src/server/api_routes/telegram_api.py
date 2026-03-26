"""Telegram webhook endpoints."""

from fastapi import APIRouter, Header, HTTPException, Request

from ..models.api_contracts import (
    TelegramChannelHealthResponse,
    TelegramDigestCycleRequest,
    TelegramDigestRequest,
    TelegramDigestResponse,
    TelegramDueDigestRequest,
    validate_response,
)
from ..services.channels.notification_scheduler_service import NotificationSchedulerService
from ..services.channels.telegram_service import TelegramChannelService

router = APIRouter(prefix="/api/channels/telegram", tags=["telegram"])


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    """Receive Telegram webhook updates and route them into control-plane services."""
    body = await request.json()
    service = TelegramChannelService()
    success, result = await service.handle_webhook(
        body,
        secret_token=x_telegram_bot_api_secret_token,
    )
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return result


@router.post("/digest", response_model=TelegramDigestResponse)
async def telegram_digest(body: TelegramDigestRequest):
    """Build or send a Telegram digest from caller-supplied events."""
    service = TelegramChannelService()
    if body.send:
        success, result = await service.send_digest([item.model_dump() for item in body.events])
        if not success:
            raise HTTPException(status_code=400, detail=result)
        return validate_response(result, TelegramDigestResponse)

    preview = service.build_digest([item.model_dump() for item in body.events])
    return validate_response(preview, TelegramDigestResponse)


@router.post("/digest-preview", response_model=TelegramDigestResponse)
async def telegram_digest_preview(body: TelegramDigestRequest):
    """Build a Telegram digest preview without sending it."""
    service = TelegramChannelService()
    preview = service.build_digest([item.model_dump() for item in body.events])
    return validate_response(preview, TelegramDigestResponse)


@router.post("/digest-send", response_model=TelegramDigestResponse)
async def telegram_digest_send(body: TelegramDigestRequest):
    """Send a Telegram digest built from caller-supplied events."""
    service = TelegramChannelService()
    success, result = await service.send_digest([item.model_dump() for item in body.events])
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return validate_response(result, TelegramDigestResponse)


@router.post("/due-digest", response_model=TelegramDigestResponse)
async def telegram_due_digest(body: TelegramDueDigestRequest):
    """Send a schedule-aware digest if the current configured window is due."""
    scheduler = NotificationSchedulerService()
    success, result = await scheduler.run_telegram_due_digest(
        events=[item.model_dump() for item in body.events],
        force=body.force,
        now_override=body.now_override,
        scheduler_origin=body.scheduler_origin,
        delivery_required=body.delivery_required,
    )
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return validate_response(result, TelegramDigestResponse)


@router.post("/due-digest-cycle", response_model=TelegramDigestResponse)
async def telegram_due_digest_cycle(body: TelegramDigestCycleRequest):
    """Collect replay events and run one scheduler-backed due-digest cycle."""
    scheduler = NotificationSchedulerService()
    success, result = await scheduler.run_telegram_due_digest(
        collect_mode="observability-replay",
        force=body.force,
        now_override=body.now_override,
        limit=body.limit,
        scheduler_origin=body.scheduler_origin,
        delivery_required=body.delivery_required,
    )
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return validate_response(result, TelegramDigestResponse)


@router.get("/health", response_model=TelegramChannelHealthResponse)
async def telegram_health():
    """Return Telegram channel readiness and routing configuration."""
    service = TelegramChannelService()
    return validate_response(service.get_health_status(), TelegramChannelHealthResponse)


@router.get("/heartbeat", response_model=TelegramChannelHealthResponse)
async def telegram_heartbeat():
    """Return Telegram heartbeat-style readiness for automation checks."""
    service = TelegramChannelService()
    return validate_response(service.get_health_status(), TelegramChannelHealthResponse)

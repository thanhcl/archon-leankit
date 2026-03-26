"""Aggregated health endpoints for external channels."""

from fastapi import APIRouter

from ..models.api_contracts import ExternalChannelHeartbeatResponse, validate_response
from ..services.channels.channel_health_service import ExternalChannelHealthService

router = APIRouter(prefix="/api/channels", tags=["channels"])


@router.get("/health", response_model=ExternalChannelHeartbeatResponse)
async def channels_health():
    """Return one aggregated readiness snapshot across external channels."""
    service = ExternalChannelHealthService()
    return validate_response(service.get_heartbeat(), ExternalChannelHeartbeatResponse)


@router.get("/heartbeat", response_model=ExternalChannelHeartbeatResponse)
async def channels_heartbeat():
    """Return one automation-friendly aggregated heartbeat across external channels."""
    service = ExternalChannelHealthService()
    return validate_response(service.get_heartbeat(), ExternalChannelHeartbeatResponse)

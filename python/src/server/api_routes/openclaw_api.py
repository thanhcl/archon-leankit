"""OpenClaw ingress endpoints."""

from fastapi import APIRouter, Header, HTTPException

from ..models.api_contracts import (
    ExternalRequestResponse,
    OpenClawChannelHealthResponse,
    OpenClawArchitectRequest,
    OpenClawArchitectResponse,
    OpenClawIngestRequest,
    validate_response,
)
from ..services.channels.openclaw_service import OpenClawChannelService

router = APIRouter(prefix="/api/channels/openclaw", tags=["openclaw"])


@router.post("/ingest", response_model=ExternalRequestResponse)
async def openclaw_ingest(
    body: OpenClawIngestRequest,
    x_openclaw_secret: str | None = Header(default=None),
):
    """Receive OpenClaw ingress and route it into the external-request control plane."""
    service = OpenClawChannelService()
    success, result = await service.handle_ingest(body.model_dump(), secret_token=x_openclaw_secret)
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return validate_response(result["request"], ExternalRequestResponse)


@router.post("/architect-plan", response_model=OpenClawArchitectResponse)
async def openclaw_architect_plan(
    body: OpenClawArchitectRequest,
    x_openclaw_secret: str | None = Header(default=None),
):
    """Receive an OpenClaw architect request and return a structured architect plan."""
    service = OpenClawChannelService()
    success, result = await service.handle_architect_request(body.model_dump(), secret_token=x_openclaw_secret)
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return validate_response(
        {
            "request": result["request"],
            "architect_plan": result["architect_plan"],
        },
        OpenClawArchitectResponse,
    )


@router.get("/health", response_model=OpenClawChannelHealthResponse)
async def openclaw_health():
    """Return OpenClaw ingress readiness and replay-protection flags."""
    service = OpenClawChannelService()
    return validate_response(service.get_health_status(), OpenClawChannelHealthResponse)


@router.get("/heartbeat", response_model=OpenClawChannelHealthResponse)
async def openclaw_heartbeat():
    """Return OpenClaw heartbeat-style readiness for automation checks."""
    service = OpenClawChannelService()
    return validate_response(service.get_health_status(), OpenClawChannelHealthResponse)

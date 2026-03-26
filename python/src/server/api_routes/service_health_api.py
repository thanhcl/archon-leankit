"""Aggregated pilot-oriented service health endpoints."""

from fastapi import APIRouter

from ..models.api_contracts import PlatformServiceHealthResponse, validate_response
from ..services.channels.platform_health_service import PlatformHealthService

router = APIRouter(prefix="/api/services", tags=["services"])


@router.get("/health", response_model=PlatformServiceHealthResponse)
async def services_health():
    """Return aggregated pilot-critical service health across platform dependencies."""
    service = PlatformHealthService()
    return validate_response(await service.get_service_health(), PlatformServiceHealthResponse)


@router.get("/heartbeat", response_model=PlatformServiceHealthResponse)
async def services_heartbeat():
    """Automation-friendly alias for the aggregated service-health surface."""
    service = PlatformHealthService()
    return validate_response(await service.get_service_health(), PlatformServiceHealthResponse)

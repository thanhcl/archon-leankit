"""External ingress request API endpoints."""

from fastapi import APIRouter, HTTPException

from ..models.api_contracts import (
    CreateExternalRequestRequest,
    ExternalRequestListResponse,
    ExternalRequestResponse,
    validate_response,
)
from ..services.projects.external_request_service import ExternalRequestService

router = APIRouter(prefix="/api/external-requests", tags=["external-requests"])


@router.get("", response_model=ExternalRequestListResponse)
async def list_external_requests(
    project_id: str | None = None,
    source_channel: str | None = None,
    request_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
):
    """List external ingress requests with optional filtering."""
    service = ExternalRequestService()
    success, result = service.list_requests(
        project_id=project_id,
        source_channel=source_channel,
        request_type=request_type,
        status=status,
        limit=limit,
    )
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return validate_response(result, ExternalRequestListResponse)


@router.get("/{request_id}", response_model=ExternalRequestResponse)
async def get_external_request(request_id: str):
    """Get a single external request by ID."""
    service = ExternalRequestService()
    success, result = service.get_request(request_id)
    if not success:
        raise HTTPException(status_code=404, detail=result)
    return validate_response(result["request"], ExternalRequestResponse)


@router.post("", response_model=ExternalRequestResponse)
async def create_external_request(body: CreateExternalRequestRequest):
    """Create an external ingress request and optionally materialize it."""
    service = ExternalRequestService()
    success, result = await service.create_request(**body.model_dump())
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return validate_response(result["request"], ExternalRequestResponse)

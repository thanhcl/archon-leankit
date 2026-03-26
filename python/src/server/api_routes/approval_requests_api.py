"""Approval workflow API endpoints."""

from fastapi import APIRouter, HTTPException

from ..models.api_contracts import (
    ApprovalBatchDecisionRequest,
    ApprovalBatchDecisionResponse,
    ApprovalDecisionRequest,
    ApprovalRequestListResponse,
    ApprovalRequestResponse,
    CreateApprovalRequestRequest,
    validate_response,
)
from ..services.projects.approval_request_service import ApprovalRequestService

router = APIRouter(prefix="/api/approval-requests", tags=["approval-requests"])


@router.get("", response_model=ApprovalRequestListResponse)
async def list_approval_requests(
    project_id: str | None = None,
    status: str | None = None,
    external_request_id: str | None = None,
    limit: int = 50,
):
    """List approval requests with optional filters."""
    service = ApprovalRequestService()
    success, result = service.list_requests(
        project_id=project_id,
        status=status,
        external_request_id=external_request_id,
        limit=limit,
    )
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return validate_response(result, ApprovalRequestListResponse)


@router.get("/{approval_id}", response_model=ApprovalRequestResponse)
async def get_approval_request(approval_id: str):
    """Fetch one approval request."""
    service = ApprovalRequestService()
    success, result = service.get_request(approval_id)
    if not success:
        raise HTTPException(status_code=404, detail=result)
    return validate_response(result["approval"], ApprovalRequestResponse)


@router.post("/batch-decision", response_model=ApprovalBatchDecisionResponse)
async def batch_decide_approval_requests(body: ApprovalBatchDecisionRequest):
    """Record the same decision across multiple approval requests."""
    service = ApprovalRequestService()
    success, result = await service.decide_requests(
        body.approval_ids,
        decision=body.decision,
        decided_by=body.decided_by,
        decision_comment=body.decision_comment,
        bundle_label=body.bundle_label,
        minimum_required=body.minimum_required,
        stop_on_error=body.stop_on_error,
    )
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return validate_response(result, ApprovalBatchDecisionResponse)


@router.post("", response_model=ApprovalRequestResponse)
async def create_approval_request(body: CreateApprovalRequestRequest):
    """Create a pending approval request."""
    service = ApprovalRequestService()
    success, result = await service.create_request(**body.model_dump())
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return validate_response(result["approval"], ApprovalRequestResponse)


@router.post("/{approval_id}/decision", response_model=ApprovalRequestResponse)
async def decide_approval_request(approval_id: str, body: ApprovalDecisionRequest):
    """Record an approval decision."""
    service = ApprovalRequestService()
    success, result = await service.decide_request(
        approval_id,
        decision=body.decision,
        decided_by=body.decided_by,
        decision_comment=body.decision_comment,
    )
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return validate_response(result["approval"], ApprovalRequestResponse)

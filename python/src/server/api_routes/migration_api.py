"""API routes for database migration tracking and management."""

import logfire
from fastapi import APIRouter, Header, HTTPException, Response

from ..config.version import ARCHON_VERSION
from ..models.api_contracts import (
    MigrationHistoryResponse,
    MigrationRecord,
    MigrationStatusResponse,
    PendingMigration,
)
from ..services.migration_service import migration_service
from ..utils.etag_utils import check_etag, generate_etag


# Create router
router = APIRouter(prefix="/api/migrations", tags=["migrations"])


@router.get("/status", response_model=MigrationStatusResponse)
async def get_migration_status(
    response: Response, if_none_match: str | None = Header(None)
):
    """
    Get current migration status including pending and applied migrations.

    Returns comprehensive migration status with:
    - List of pending migrations with SQL content
    - List of applied migrations
    - Bootstrap flag if migrations table doesn't exist
    - Current version information
    """
    try:
        # Get migration status from service
        status = await migration_service.get_migration_status()

        # Generate ETag for response
        etag = generate_etag(status)

        # Check if client has current data
        if check_etag(if_none_match, etag):
            # Client has current data, return 304
            response.status_code = 304
            response.headers["ETag"] = f'"{etag}"'
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return Response(status_code=304)
        else:
            # Client needs new data
            response.headers["ETag"] = f'"{etag}"'
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return MigrationStatusResponse(**status)

    except Exception as e:
        logfire.error(f"Error getting migration status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get migration status: {str(e)}") from e


@router.get("/history", response_model=MigrationHistoryResponse)
async def get_migration_history(response: Response, if_none_match: str | None = Header(None)):
    """
    Get history of applied migrations.

    Returns list of all applied migrations sorted by date.
    """
    try:
        # Get applied migrations from service
        applied = await migration_service.get_applied_migrations()

        # Format response
        history = {
            "migrations": [
                MigrationRecord(
                    version=m.version,
                    migration_name=m.migration_name,
                    applied_at=m.applied_at,
                    checksum=m.checksum,
                )
                for m in applied
            ],
            "total_count": len(applied),
            "current_version": ARCHON_VERSION,
        }

        # Generate ETag for response
        etag = generate_etag(history)

        # Check if client has current data
        if check_etag(if_none_match, etag):
            # Client has current data, return 304
            response.status_code = 304
            response.headers["ETag"] = f'"{etag}"'
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return Response(status_code=304)
        else:
            # Client needs new data
            response.headers["ETag"] = f'"{etag}"'
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return MigrationHistoryResponse(**history)

    except Exception as e:
        logfire.error(f"Error getting migration history: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get migration history: {str(e)}") from e


@router.get("/pending", response_model=list[PendingMigration])
async def get_pending_migrations():
    """
    Get list of pending migrations only.

    Returns simplified list of migrations that need to be applied.
    """
    try:
        # Get pending migrations from service
        pending = await migration_service.get_pending_migrations()

        # Format response
        return [
            PendingMigration(
                version=m.version,
                name=m.name,
                sql_content=m.sql_content,
                file_path=m.file_path,
                checksum=m.checksum,
            )
            for m in pending
        ]

    except Exception as e:
        logfire.error(f"Error getting pending migrations: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get pending migrations: {str(e)}") from e

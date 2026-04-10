"""
Wiki API endpoints — CRUD for wiki pages, links, graph, lint, viewer, and export.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from ..config.logfire_config import get_logger
from ..services.wiki.wiki_community_service import WikiCommunityService
from ..services.wiki.wiki_export_service import WikiExportService
from ..services.wiki.wiki_service import WikiService
from ..services.wiki.wiki_lint_service import WikiLintService

logger = get_logger(__name__)

router = APIRouter(prefix="/api/wiki", tags=["wiki"])


# ── Request Models ────────────────────────────────────────────

class CreatePageRequest(BaseModel):
    project_id: str
    title: str
    content: str
    page_type: str
    category: str | None = None
    tags: list[str] | None = None
    source_ids: list[str] | None = None
    summary: str | None = None
    status: str = "draft"


class UpdatePageRequest(BaseModel):
    content: str | None = None
    title: str | None = None
    summary: str | None = None
    tags: list[str] | None = None
    status: str | None = None
    category: str | None = None


class CreateLinkRequest(BaseModel):
    from_page_id: str
    to_page_id: str
    link_type: str = "related"
    context: str | None = None
    strength: float = 0.5
    confidence: str = "inferred"
    created_by: str = "system"


class CreateNoteRequest(BaseModel):
    project_id: str
    text: str
    link_to_slugs: list[str] | None = None
    tags: list[str] | None = None


# ── Page Endpoints ────────────────────────────────────────────

@router.post("/pages")
async def create_page(req: CreatePageRequest):
    svc = WikiService()
    ok, result = svc.create_page(
        project_id=req.project_id,
        title=req.title,
        content=req.content,
        page_type=req.page_type,
        category=req.category,
        tags=req.tags,
        source_ids=req.source_ids,
        summary=req.summary,
        status=req.status,
    )
    if not ok:
        raise HTTPException(status_code=500, detail=result) from None
    return result


@router.get("/pages/{page_id}")
async def get_page(page_id: str, include_links: bool = True):
    svc = WikiService()
    ok, result = svc.get_page(page_id=page_id, include_links=include_links)
    if not ok:
        raise HTTPException(status_code=404, detail=result) from None
    return result


@router.get("/pages/by-slug/{slug}")
async def get_page_by_slug(slug: str, project_id: str, include_links: bool = True):
    svc = WikiService()
    ok, result = svc.get_page(slug=slug, project_id=project_id, include_links=include_links)
    if not ok:
        raise HTTPException(status_code=404, detail=result) from None
    return result


@router.put("/pages/{page_id}")
async def update_page(page_id: str, req: UpdatePageRequest):
    svc = WikiService()
    ok, result = svc.update_page(
        page_id=page_id,
        content=req.content,
        title=req.title,
        summary=req.summary,
        tags=req.tags,
        status=req.status,
        category=req.category,
    )
    if not ok:
        raise HTTPException(status_code=500, detail=result) from None
    return result


@router.delete("/pages/{page_id}")
async def delete_page(page_id: str):
    svc = WikiService()
    ok, msg = svc.delete_page(page_id)
    if not ok:
        raise HTTPException(status_code=500, detail={"error": msg}) from None
    return {"message": msg}


@router.get("/pages")
async def list_pages(
    project_id: str,
    page_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    svc = WikiService()
    ok, result = svc.list_pages(
        project_id=project_id,
        page_type=page_type,
        status=status,
        limit=limit,
        offset=offset,
    )
    if not ok:
        raise HTTPException(status_code=500, detail={"error": "Failed to list pages"}) from None
    return {"pages": result, "count": len(result)}


@router.get("/search")
async def search_pages(
    query: str,
    project_id: str | None = None,
    page_type: str | None = None,
    category: str | None = None,
    status: str | None = None,
    limit: int = 10,
):
    svc = WikiService()
    ok, result = svc.search_pages(
        query=query,
        project_id=project_id,
        page_type=page_type,
        category=category,
        status=status,
        limit=limit,
    )
    if not ok:
        raise HTTPException(status_code=500, detail={"error": "Search failed"}) from None
    return {"results": result, "count": len(result)}


# ── Link Endpoints ────────────────────────────────────────────

@router.post("/links")
async def create_link(req: CreateLinkRequest):
    svc = WikiService()
    ok, result = svc.create_link(
        from_page_id=req.from_page_id,
        to_page_id=req.to_page_id,
        link_type=req.link_type,
        context=req.context,
        strength=req.strength,
        confidence=req.confidence,
        created_by=req.created_by,
    )
    if not ok:
        raise HTTPException(status_code=500, detail=result) from None
    return result


# ── Graph Endpoints ───────────────────────────────────────────

@router.get("/graph")
async def get_graph(
    project_id: str | None = None,
    page_id: str | None = None,
    depth: int = 2,
):
    svc = WikiService()
    ok, result = svc.get_graph(
        project_id=project_id,
        page_id=page_id,
        depth=depth,
    )
    if not ok:
        raise HTTPException(status_code=500, detail=result) from None
    return result


# ── Lint Endpoints ────────────────────────────────────────────

@router.post("/lint/{project_id}")
async def lint_wiki(project_id: str):
    svc = WikiLintService()
    ok, result = svc.lint(project_id)
    if not ok:
        raise HTTPException(status_code=500, detail=result) from None
    return result


# ── Note Endpoints (E3: Write-Back Loop) ─────────────────────

@router.post("/notes")
async def create_note(req: CreateNoteRequest):
    svc = WikiService()
    ok, result = svc.create_quick_note(
        project_id=req.project_id,
        text=req.text,
        link_to_slugs=req.link_to_slugs,
        tags=req.tags,
    )
    if not ok:
        raise HTTPException(status_code=500, detail=result) from None
    return result


# ── Community Endpoints (E4) ─────────────────────────────────

@router.post("/communities/{project_id}")
async def detect_communities(project_id: str):
    svc = WikiCommunityService()
    ok, result = svc.detect_communities(project_id)
    if not ok:
        raise HTTPException(status_code=500, detail=result) from None
    return result


@router.get("/communities/{project_id}")
async def get_communities(project_id: str):
    svc = WikiCommunityService()
    ok, result = svc.get_communities(project_id)
    if not ok:
        raise HTTPException(status_code=500, detail=result) from None
    return result


# ── Viewer Endpoint (C1) ─────────────────────────────────────

@router.get("/viewer", response_class=HTMLResponse)
async def wiki_viewer():
    """Serve the standalone Wiki KB HTML viewer."""
    # Look for the HTML file in multiple locations (Docker + local)
    candidates = [
        Path("/app/src/server/static/wiki-viewer.html"),
        Path(__file__).parent.parent / "static" / "wiki-viewer.html",
    ]
    for path in candidates:
        if path.exists():
            return HTMLResponse(content=path.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail="wiki-viewer.html not found") from None


# ── Export Endpoint (C3) ──────────────────────────────────────

@router.get("/export/obsidian/{project_id}")
async def export_obsidian(project_id: str):
    """Export wiki pages as Obsidian vault (zip download)."""
    svc = WikiExportService()
    ok, result = svc.export_obsidian_vault(project_id)
    if not ok:
        raise HTTPException(status_code=500, detail=result) from None
    return Response(
        content=result,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=wiki-vault-{project_id[:8]}.zip"},
    )

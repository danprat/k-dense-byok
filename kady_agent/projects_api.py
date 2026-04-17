"""HTTP endpoints for project CRUD and sandbox (re)initialisation.

Mounted under the root path of the main FastAPI app in ``server.py``. The
middleware in ``server.py`` sets the active project for each request; the
endpoints here explicitly read and mutate the project registry, so they
bypass the usual ``active_paths()`` resolution and operate on whichever
project id is in the URL or request body.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .projects import (
    DEFAULT_PROJECT_ID,
    create_project,
    delete_project,
    ensure_project_exists,
    get_project,
    init_project_sandbox,
    list_projects,
    update_project,
)


projects_router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreateBody(BaseModel):
    name: str
    description: Optional[str] = ""
    tags: Optional[list[str]] = Field(default_factory=list)
    id: Optional[str] = None  # let callers pin a slug, otherwise we mint one


class ProjectPatchBody(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    archived: Optional[bool] = None


class SandboxInitBody(BaseModel):
    sync_venv: bool = True
    download_skills: bool = True


@projects_router.get("")
def get_projects():
    """Return every known project (archived projects sorted last)."""
    return [m.to_dict() for m in list_projects()]


@projects_router.post("", status_code=201)
def post_project(body: ProjectCreateBody):
    """Create a new project and seed an empty sandbox directory."""
    try:
        meta = create_project(
            name=body.name,
            description=body.description or "",
            tags=body.tags or [],
            project_id=body.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Touch the on-disk skeleton so subsequent GET /sandbox/tree etc. work
    # without requiring an explicit init call.
    ensure_project_exists(meta.id)
    return meta.to_dict()


@projects_router.get("/{project_id}")
def get_one_project(project_id: str):
    meta = get_project(project_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return meta.to_dict()


@projects_router.patch("/{project_id}")
def patch_project(project_id: str, body: ProjectPatchBody):
    try:
        meta = update_project(
            project_id,
            name=body.name,
            description=body.description,
            tags=body.tags,
            archived=body.archived,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Project not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return meta.to_dict()


@projects_router.delete("/{project_id}", status_code=204)
def delete_one_project(project_id: str):
    if project_id == DEFAULT_PROJECT_ID:
        raise HTTPException(
            status_code=400, detail="The default project cannot be deleted"
        )
    meta = get_project(project_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        delete_project(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return None


@projects_router.post("/{project_id}/sandbox/init")
def post_init_sandbox(project_id: str, body: SandboxInitBody | None = None):
    """Run (or re-run) the heavy sandbox bootstrap for a project.

    Creates GEMINI.md, merged ``.gemini/settings.json``, pyproject.toml,
    runs ``uv sync``, and downloads the scientific skills catalogue.
    Idempotent.
    """
    if get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    body = body or SandboxInitBody()
    init_project_sandbox(
        project_id,
        sync_venv=body.sync_venv,
        download_skills=body.download_skills,
    )
    return {"ok": True}

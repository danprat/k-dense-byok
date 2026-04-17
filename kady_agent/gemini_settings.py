"""Shared helpers for building and persisting Gemini CLI settings.

The default MCP servers (docling, parallel-search) are defined here.
User-added MCP servers live in each project's ``custom_mcps.json``
(outside ``sandbox/`` so they survive sandbox deletion). The active
project is resolved via the request-scoped ``ACTIVE_PROJECT`` ContextVar
through :func:`kady_agent.projects.active_paths`.
"""

from __future__ import annotations

import json
from pathlib import Path

from .projects import active_paths


def custom_mcps_path() -> Path:
    """Return the custom-MCP JSON path for the active project."""
    return active_paths().custom_mcps_path


def build_default_settings() -> dict:
    """Return the base Gemini CLI settings dict with built-in MCP servers."""
    settings: dict = {
        "security": {"auth": {"selectedType": "gemini-api-key"}},
        "mcpServers": {
            "docling": {
                "command": "uvx",
                "args": ["--from=docling-mcp", "docling-mcp-server"],
            },
            # Lets the expert drop highlights / sticky notes into the
            # <pdf>.annotations.json sidecar so the user-facing PDF
            # viewer renders them with the expert's label + color.
            # Invoked in-process via `python -m` so it always matches
            # the bundled server code without needing console_scripts
            # to be installed for the expert's environment.
            "pdf-annotations": {
                "command": "uv",
                "args": [
                    "run",
                    "--directory",
                    _repo_root_str(),
                    "python",
                    "-m",
                    "kady_agent.mcp_servers.pdf_annotations",
                ],
            },
        },
    }
    return settings


def _repo_root_str() -> str:
    """Absolute path to the repo root (parent of ``kady_agent``)."""
    from pathlib import Path

    return str(Path(__file__).resolve().parent.parent)


def load_custom_mcps() -> dict:
    """Read user-defined MCP servers for the active project.

    Returns an empty dict when the file is missing or unparseable.
    """
    path = custom_mcps_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {}


def save_custom_mcps(data: dict) -> None:
    """Persist user-defined MCP servers for the active project."""
    path = custom_mcps_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_merged_settings(target_dir: str | Path) -> None:
    """Build merged settings and write to ``<target_dir>/settings.json``.

    *target_dir* is typically ``<project>/sandbox/.gemini``.
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    settings = build_default_settings()
    custom = load_custom_mcps()
    settings["mcpServers"].update(custom)

    out = target_dir / "settings.json"
    out.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

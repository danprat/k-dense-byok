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


def browser_use_config_path() -> Path:
    """Return the browser-use config JSON path for the active project."""
    return active_paths().browser_use_config_path


DEFAULT_BROWSER_USE_CONFIG: dict = {
    "enabled": True,
    "headed": False,
    "profile": None,
    "session": None,
}


def load_browser_use_config() -> dict:
    """Read the browser-use config for the active project.

    Returns a dict with defaults filled in; missing/unparseable files
    fall back to ``DEFAULT_BROWSER_USE_CONFIG``.
    """
    path = browser_use_config_path()
    data: dict | None = None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            data = parsed
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = None

    cfg = dict(DEFAULT_BROWSER_USE_CONFIG)
    if data:
        cfg.update({k: data[k] for k in DEFAULT_BROWSER_USE_CONFIG if k in data})
    return cfg


def save_browser_use_config(data: dict) -> None:
    """Persist the browser-use config for the active project."""
    cfg = dict(DEFAULT_BROWSER_USE_CONFIG)
    cfg.update({k: data[k] for k in DEFAULT_BROWSER_USE_CONFIG if k in data})
    path = browser_use_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def build_browser_use_mcp_spec() -> dict | None:
    """Return a Gemini-CLI-style MCP server spec for browser-use.

    Returns ``None`` when the feature is disabled in the project's
    ``browser_use.json`` so callers can skip registration.
    """
    cfg = load_browser_use_config()
    if not cfg.get("enabled", True):
        return None

    args: list[str] = ["browser-use"]
    if cfg.get("headed"):
        args.append("--headed")
    profile = cfg.get("profile")
    if profile:
        args += ["--profile", str(profile)]
    session = cfg.get("session")
    if session:
        args += ["--session", str(session)]
    args.append("--mcp")
    return {"command": "uvx", "args": args}


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

    bu = build_browser_use_mcp_spec()
    if bu is not None:
        settings["mcpServers"]["browser-use"] = bu

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

"""Detect locally-installed Chrome profiles for browser-use.

Reads Chrome's ``Local State`` JSON to map profile directory names
(``Default``, ``Profile 1``, ``Profile 2`` ...) to the user-visible
display names / emails stored in ``profile.info_cache``.

browser-use's ``--profile`` flag expects the *directory* name, so we
expose both so the UI can render friendly labels.
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ChromeProfile:
    """A single Chrome profile on disk."""

    id: str
    """Directory name under the Chrome user-data dir (e.g. ``Default``,
    ``Profile 1``). This is what browser-use's ``--profile`` expects."""

    name: str
    """Display name (falls back to ``id`` when the Local-State entry has
    no ``name``/``gaia_given_name``/``user_name``)."""

    email: str | None
    """The associated Google account email when present."""

    path: str
    """Absolute path to the profile directory."""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "path": self.path,
        }


def _chrome_user_data_dir() -> Path | None:
    """Return the Chrome user-data directory for the current user, if any.

    Returns the first existing candidate; callers should check the
    ``Local State`` file existence before using the result.
    """
    home = Path(os.path.expanduser("~"))
    system = platform.system()
    candidates: list[Path]
    if system == "Darwin":
        candidates = [home / "Library" / "Application Support" / "Google" / "Chrome"]
    elif system == "Windows":
        local_appdata = os.environ.get("LOCALAPPDATA")
        candidates = [Path(local_appdata) / "Google" / "Chrome" / "User Data"] if local_appdata else []
    else:  # Linux / BSD
        candidates = [
            home / ".config" / "google-chrome",
            home / ".config" / "chromium",
        ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def detect_chrome_profiles() -> list[ChromeProfile]:
    """Return the list of Chrome profiles detected on this machine.

    Safe to call when Chrome isn't installed — returns ``[]`` instead of
    raising. Sorted so ``Default`` (when present) comes first, followed
    by profiles sorted alphabetically by display name.
    """
    root = _chrome_user_data_dir()
    if root is None:
        return []

    local_state_path = root / "Local State"
    info_cache: dict = {}
    try:
        state = json.loads(local_state_path.read_text(encoding="utf-8"))
        info_cache = (state.get("profile") or {}).get("info_cache") or {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        info_cache = {}

    profiles: list[ChromeProfile] = []
    for profile_id, meta in info_cache.items():
        if not isinstance(meta, dict):
            continue
        path = root / profile_id
        if not path.is_dir():
            # Skip stale entries that no longer exist on disk.
            continue
        name = (
            meta.get("name")
            or meta.get("gaia_given_name")
            or meta.get("user_name")
            or profile_id
        )
        email = meta.get("user_name") or None
        profiles.append(
            ChromeProfile(
                id=profile_id,
                name=str(name),
                email=str(email) if email else None,
                path=str(path),
            )
        )

    def sort_key(p: ChromeProfile) -> tuple[int, str]:
        # Pin "Default" to the top, then sort by display name.
        return (0 if p.id == "Default" else 1, p.name.lower())

    profiles.sort(key=sort_key)
    return profiles

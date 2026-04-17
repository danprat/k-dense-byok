"""Numerical-claims tracking: schema + validation + per-turn merge.

Every expert delegation that executed code is required (per
``instructions/gemini_cli.md`` PROTOCOL:REPRODUCIBILITY) to write a
``claims.json`` file at ``.kady/expert/<delegationId>/claims.json`` listing
every user-facing number in its reply with a pointer back to a concrete
source: a file line, a notebook cell, or a specific tool-output event in the
expert's ``stdout.jsonl``.

The frontend reads the merged turn-level file via
``GET /turns/{s}/{t}/claims`` and renders each claim with a subtle
underline (backed) or a red dotted underline (unbacked), plus click-through
into the referenced file/cell/tool output.

Schema
------

Each claim is a dict::

    {
      "text": "p = 0.03",
      "context": "shortest surrounding sentence",
      "status": "verified" | "approximate" | "unbacked" | "ambiguous",
      "source": {
        "kind": "file" | "notebook" | "tool_output" | "none",
        "file": "report/analysis.py",        # for file, notebook
        "line": 23,                          # for file, notebook (line in cell source)
        "cell": 7,                           # for notebook
        "value": "0.0284",                   # optional literal from source
        "note": "optional human note",
        "delegationId": "003",               # for tool_output
        "eventIndex": 42                     # for tool_output
      }
    }

A claim with ``status == "unbacked"`` should carry ``source.kind == "none"``
(or omit ``source``). The frontend renders these with a red underline.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

VALID_STATUSES = {"verified", "approximate", "unbacked", "ambiguous"}
VALID_KINDS = {"file", "notebook", "tool_output", "none"}


def _coerce_source(raw: Any) -> dict | None:
    """Normalize a ``source`` field. Return None on unrecoverable shapes.

    Accepts the legacy flat shape (``{file, line, cell, value}``) and
    infers ``kind`` when missing so old auditor output keeps working.
    """
    if raw is None:
        return {"kind": "none"}
    if not isinstance(raw, dict):
        return None

    out: dict[str, Any] = {}
    kind = raw.get("kind")
    file = raw.get("file")
    cell = raw.get("cell")
    line = raw.get("line")
    value = raw.get("value")
    note = raw.get("note")
    delegation_id = raw.get("delegationId") or raw.get("delegation_id")
    event_index = raw.get("eventIndex")
    if event_index is None:
        event_index = raw.get("event_index")

    if kind not in VALID_KINDS:
        if isinstance(delegation_id, str) and event_index is not None:
            kind = "tool_output"
        elif isinstance(cell, int):
            kind = "notebook"
        elif isinstance(file, str):
            kind = "file"
        else:
            kind = "none"
    out["kind"] = kind

    if kind in ("file", "notebook") and isinstance(file, str) and file.strip():
        out["file"] = file.strip()
    if kind == "notebook":
        if isinstance(cell, int):
            out["cell"] = cell
        elif isinstance(cell, str) and cell.isdigit():
            out["cell"] = int(cell)
    if isinstance(line, int):
        out["line"] = line
    elif isinstance(line, str) and line.isdigit():
        out["line"] = int(line)
    if isinstance(value, (str, int, float)):
        out["value"] = str(value)
    if isinstance(note, str) and note.strip():
        out["note"] = note.strip()
    if kind == "tool_output":
        if isinstance(delegation_id, str) and delegation_id.strip():
            out["delegationId"] = delegation_id.strip()
        if isinstance(event_index, int):
            out["eventIndex"] = event_index
        elif isinstance(event_index, str) and event_index.isdigit():
            out["eventIndex"] = int(event_index)
        if "delegationId" not in out or "eventIndex" not in out:
            out["kind"] = "none"
            for k in ("delegationId", "eventIndex"):
                out.pop(k, None)

    if out["kind"] == "file" and "file" not in out:
        out["kind"] = "none"
    if out["kind"] == "notebook" and ("file" not in out or "cell" not in out):
        out["kind"] = "none"

    return out


def _coerce_claim(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    status = raw.get("status")
    if status not in VALID_STATUSES:
        status = "unbacked"
    entry: dict[str, Any] = {"text": text, "status": status}
    context = raw.get("context")
    if isinstance(context, str) and context.strip():
        entry["context"] = context.strip()
    src = _coerce_source(raw.get("source"))
    if src is None:
        src = {"kind": "none"}
    entry["source"] = src
    if status == "unbacked" and src.get("kind") != "none":
        entry["source"] = {"kind": "none"}
    return entry


def load_expert_claims(
    path: Path, delegation_id: str | None = None
) -> list[dict]:
    """Parse an expert-written ``claims.json``, dropping malformed entries.

    ``delegation_id`` is stamped on any tool_output source that omitted it,
    so merged turn-level claims remain self-describing.
    """
    try:
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if isinstance(data, list):
        raw_claims: Iterable[Any] = data
    elif isinstance(data, dict):
        raw_claims = data.get("claims") or []
    else:
        return []

    out: list[dict] = []
    for raw in raw_claims:
        entry = _coerce_claim(raw)
        if entry is None:
            continue
        src = entry.get("source") or {}
        if src.get("kind") == "tool_output" and "delegationId" not in src:
            if delegation_id:
                src["delegationId"] = delegation_id
            else:
                entry["source"] = {"kind": "none"}
        out.append(entry)
    return out


def merge_turn_claims(
    existing: list[dict] | None, new_entries: list[dict]
) -> list[dict]:
    """Append ``new_entries`` to ``existing`` preserving delegation order."""
    merged: list[dict] = list(existing or [])
    for entry in new_entries:
        merged.append(entry)
    return merged


def summarize_claims(entries: list[dict]) -> dict:
    """Return status counts + total for the manifest summary slot."""
    counts = {"verified": 0, "approximate": 0, "unbacked": 0, "ambiguous": 0}
    for e in entries:
        status = e.get("status")
        if status in counts:
            counts[status] += 1
    return {"total": len(entries), **counts}


def build_turn_claims_doc(
    *,
    turn_id: str,
    entries: list[dict],
    scanned_at: str | None = None,
) -> dict:
    return {
        "turnId": turn_id,
        "scannedAt": scanned_at or datetime.now(timezone.utc).isoformat(),
        "claims": entries,
    }


CODE_PRODUCING_TOOLS = frozenset(
    {
        "run_shell_command",
        "shell",
        "write_file",
        "edit_file",
        "replace",
        "save_file",
        "create_file",
        "apply_patch",
    }
)


def produced_code(result: dict, env_lock: str | None) -> bool:
    """Heuristic: did this delegation run / write code?

    We consider any file-writing or shell-running tool use a "produced code"
    signal, and also treat the presence of an ``env.lock`` artifact as
    authoritative — the reproducibility protocol only asks the expert to
    write it after executing Python/R/shell code.
    """
    if env_lock:
        return True
    tools = (result or {}).get("tools_used") or {}
    if not isinstance(tools, dict):
        return False
    for name in tools:
        if not isinstance(name, str):
            continue
        if name in CODE_PRODUCING_TOOLS:
            return True
        lower = name.lower()
        if "shell" in lower or "write" in lower or "edit" in lower:
            return True
    return False

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Optional

import litellm
from dotenv import load_dotenv
from google.adk.tools.tool_context import ToolContext

from ..cost_ledger import check_project_budget, record_cost
from ..manifest import (
    attach_delegation,
    session_seed,
)
from ..projects import active_paths, get_project

REPO_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(REPO_ROOT / "kady_agent" / ".env")

_DEFAULT_EXPERT_MODEL = os.getenv("DEFAULT_EXPERT_MODEL", "custom/gpt-5.4-proxy").strip()
_EXPERT_HEADERS = {
    "X-Title": "Kady-Expert",
    "HTTP-Referer": "https://www.k-dense.ai",
}


def _strip_custom_model_prefix(model: str) -> str:
    if isinstance(model, str) and model.startswith("custom/"):
        return model[len("custom/") :]
    return model


def _custom_model_litellm_kwargs(model: str) -> dict[str, str]:
    if not (isinstance(model, str) and model.startswith("custom/")):
        return {}

    api_base = os.environ.get("CUSTOM_OPENAI_BASE_URL", "").strip().rstrip("/")
    api_key = os.environ.get("CUSTOM_OPENAI_API_KEY", "").strip()
    if not api_base or not api_key:
        return {}

    return {
        "api_base": api_base,
        "api_key": api_key,
        "custom_llm_provider": "openai",
    }


def _extract_response_text(response: object) -> str:
    try:
        return response.choices[0].message.content or ""
    except (AttributeError, IndexError, KeyError, TypeError):
        return ""


def _extract_response_cost(response: object) -> float | None:
    hidden = getattr(response, "_hidden_params", None)
    if isinstance(hidden, dict):
        value = hidden.get("response_cost")
        if isinstance(value, (int, float)):
            return float(value)

    usage = getattr(response, "usage", None)
    for attr in ("cost", "total_cost"):
        value = getattr(usage, attr, None)
        if isinstance(value, (int, float)):
            return float(value)
    if isinstance(usage, dict):
        for key in ("cost", "total_cost"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                return float(value)
    return None


def _build_tracking_headers(
    *,
    paths_id: str,
    session_id: Optional[str],
    turn_id: Optional[str],
    delegation_id: Optional[str],
) -> dict[str, str]:
    headers = dict(_EXPERT_HEADERS)
    headers["X-Kady-Role"] = "expert"
    headers["X-Kady-Project"] = paths_id
    if session_id:
        headers["X-Kady-Session-Id"] = session_id
    if turn_id:
        headers["X-Kady-Turn-Id"] = turn_id
    if delegation_id:
        headers["X-Kady-Delegation-Id"] = delegation_id
    return headers


def _collect_expert_artifacts(kady_dir: Path, delegation_id: str) -> tuple[str | None, list[str] | None]:
    """Read expert-side env.lock and deliverables.json written by the Gemini CLI.

    The expert is instructed (see instructions/gemini_cli.md PROTOCOL:REPRODUCIBILITY)
    to write `.kady/expert/<delegationId>/env.lock` and `deliverables.json`. We
    read and return them so they can be persisted into the manifest.
    """
    expert_dir = kady_dir / "expert" / delegation_id
    env_lock: str | None = None
    deliverables: list[str] | None = None
    try:
        env_path = expert_dir / "env.lock"
        if env_path.is_file():
            env_lock = env_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    try:
        deliv_path = expert_dir / "deliverables.json"
        if deliv_path.is_file():
            data = json.loads(deliv_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, list):
                deliverables = [str(item) for item in data if isinstance(item, (str, int))]
    except (OSError, json.JSONDecodeError):
        pass
    return env_lock, deliverables


def _summarize_cli_error(stderr: str) -> str:
    """Return a compact, user-facing error message for expert model failures."""
    message = stderr.strip()
    if not message:
        return "expert model call failed"

    lowered = message.lower()
    if (
        "status 500" in lowered
        or '"code":500' in lowered
        or "internal server error" in lowered
    ):
        return "Expert model failed with upstream 500 Internal Server Error."

    return message


async def delegate_task(
    prompt: str,
    working_directory: Optional[str] = None,
    tool_context: Optional[ToolContext] = None,
) -> dict:
    """Delegate a task to an expert.

    Args:
        prompt: The prompt to delegate to the expert.
        working_directory: The sandbox directory to execute the task in.
            Defaults to the active project's sandbox.

    Returns:
        A dict with ``result`` (response text), ``skills_used`` (list of
        activated Gemini CLI skill names), and ``tools_used`` (tool call
        counts).
    """
    paths = active_paths()

    # Hard spend cap: if the project has a spendLimitUsd set and the cumulative
    # cost across every session already equals or exceeds it, refuse to launch
    # the expert. Returning a tool result (rather than raising) lets the
    # orchestrator surface a clean, user-facing explanation and still close
    # the turn normally.
    project_meta = get_project(paths.id)
    if project_meta is not None and project_meta.spendLimitUsd is not None:
        budget = check_project_budget(paths.id, project_meta.spendLimitUsd)
        if budget["state"] == "exceeded":
            limit = float(budget["limitUsd"] or 0.0)
            spent = float(budget["totalUsd"] or 0.0)
            return {
                "result": (
                    f"Delegation blocked: project '{project_meta.name}' has "
                    f"reached its spend limit (${spent:.2f} / ${limit:.2f}). "
                    f"Raise the limit in the project settings and retry."
                ),
                "skills_used": [],
                "tools_used": {},
                "budgetBlocked": True,
                "projectId": paths.id,
                "totalUsd": spent,
                "limitUsd": limit,
            }

    if working_directory is None or not working_directory.strip():
        cwd = paths.sandbox
    else:
        wd = Path(working_directory)
        if not wd.is_absolute():
            cwd = (paths.sandbox / wd).resolve()
        else:
            cwd = wd.resolve()
        if not cwd.is_relative_to(paths.sandbox):
            cwd = paths.sandbox

    cwd.mkdir(parents=True, exist_ok=True)

    state = tool_context.state if tool_context is not None else None
    turn_id: Optional[str] = None
    session_id: Optional[str] = None
    delegation_id: Optional[str] = None
    selected_model = _DEFAULT_EXPERT_MODEL
    if state is not None:
        turn_id = state.get("_turnId")
        session_id = state.get("_sessionId")
        raw_model = state.get("_expertModel") or _DEFAULT_EXPERT_MODEL
        if isinstance(raw_model, str) and raw_model.strip():
            selected_model = raw_model.strip()
    if session_id and turn_id:
        counter_key = f"_delegation_counter_{turn_id}"
        prev = state.get(counter_key) or 0
        delegation_id = f"{int(prev) + 1:03d}"
        state[counter_key] = int(prev) + 1

    headers = _build_tracking_headers(
        paths_id=paths.id,
        session_id=session_id,
        turn_id=turn_id,
        delegation_id=delegation_id,
    )

    request_model = _strip_custom_model_prefix(selected_model)

    completion_kwargs = {
        "model": request_model,
        "messages": [{"role": "user", "content": prompt}],
        "extra_headers": headers,
        "timeout": 600,
        "metadata": {
            "kady_role": "expert",
            "kady_project": paths.id,
            **({"kady_session_id": session_id} if session_id else {}),
            **({"kady_turn_id": turn_id} if turn_id else {}),
            **({"kady_delegation_id": delegation_id} if delegation_id else {}),
        },
        "cwd": str(cwd),
        **_custom_model_litellm_kwargs(selected_model),
    }

    started_at = time.time()
    try:
        response = await litellm.acompletion(**completion_kwargs)
    except Exception as exc:
        raise RuntimeError(_summarize_cli_error(str(exc))) from exc
    duration_ms = int((time.time() - started_at) * 1000)

    result = {
        "result": _extract_response_text(response),
        "skills_used": [],
        "tools_used": {},
    }

    if session_id and turn_id and delegation_id:
        try:
            record_cost(
                session_id=session_id,
                turn_id=turn_id,
                role="expert",
                model=selected_model,
                usage_dict=getattr(response, "usage", None),
                cost_usd=_extract_response_cost(response),
                delegation_id=delegation_id,
                project_id=paths.id,
            )
        except Exception:
            pass

        env_lock: str | None = None
        deliverables: list[str] | None = None
        kady_dir = cwd / ".kady"
        if kady_dir.is_dir():
            env_lock, deliverables = _collect_expert_artifacts(kady_dir, delegation_id)
        try:
            target_expert_dir = paths.runs_dir / session_id / turn_id / "expert" / delegation_id
            target_expert_dir.mkdir(parents=True, exist_ok=True)
            await attach_delegation(
                session_id=session_id,
                turn_id=turn_id,
                delegation_id=delegation_id,
                prompt=prompt,
                cwd=str(cwd.relative_to(REPO_ROOT)) if cwd.is_relative_to(REPO_ROOT) else str(cwd),
                result=result,
                duration_ms=duration_ms,
                stdout=result["result"],
                env_lock=env_lock,
                deliverables=deliverables,
            )
        except Exception:
            pass

    return result


if __name__ == "__main__":
    result = asyncio.run(delegate_task("What is the capital of France?"))
    print(result)

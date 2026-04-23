"""Unit tests for ``kady_agent/tools/gemini_cli.py``.

The heavy lifting (``delegate_task``) now calls ``litellm.acompletion``
directly; we stub that call and verify delegation metadata and expert
routing are preserved without forking a Gemini CLI subprocess.
"""

from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path

import pytest

from kady_agent import cost_ledger
from kady_agent.tools import gemini_cli


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_extract_response_text_reads_first_choice_message():
    response = types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="hello"))]
    )

    assert gemini_cli._extract_response_text(response) == "hello"


def test_extract_response_text_returns_empty_for_unexpected_shape():
    assert gemini_cli._extract_response_text(types.SimpleNamespace()) == ""


def test_extract_response_cost_prefers_hidden_response_cost():
    response = types.SimpleNamespace(
        _hidden_params={"response_cost": 0.75},
        usage=types.SimpleNamespace(total_cost=0.25),
    )

    assert gemini_cli._extract_response_cost(response) == pytest.approx(0.75)


def test_extract_response_cost_falls_back_to_usage_total_cost():
    response = types.SimpleNamespace(usage={"total_cost": 0.5})

    assert gemini_cli._extract_response_cost(response) == pytest.approx(0.5)


def test_strip_custom_model_prefix_drops_provider_prefix():
    assert gemini_cli._strip_custom_model_prefix("gpt-5.4-proxy") == "gpt-5.4-proxy"
    assert gemini_cli._strip_custom_model_prefix("gpt-5.4-proxy") == "gpt-5.4-proxy"


def test_collect_expert_artifacts(tmp_path):
    deleg_dir = tmp_path / "expert" / "d1"
    deleg_dir.mkdir(parents=True)
    (deleg_dir / "env.lock").write_text("numpy==2.0\n", encoding="utf-8")
    (deleg_dir / "deliverables.json").write_text(
        json.dumps(["out.csv", 42, {"ignored": True}]),
        encoding="utf-8",
    )
    env_lock, deliverables = gemini_cli._collect_expert_artifacts(tmp_path, "d1")
    assert env_lock == "numpy==2.0\n"
    # Integers are coerced via str(); dicts are filtered out.
    assert deliverables == ["out.csv", "42"]


def test_collect_expert_artifacts_missing_files_return_none(tmp_path):
    env_lock, deliverables = gemini_cli._collect_expert_artifacts(tmp_path, "d1")
    assert env_lock is None
    assert deliverables is None


def test_collect_expert_artifacts_ignores_non_list_json(tmp_path):
    deleg_dir = tmp_path / "expert" / "d1"
    deleg_dir.mkdir(parents=True)
    (deleg_dir / "deliverables.json").write_text('{"not": "list"}', encoding="utf-8")
    env_lock, deliverables = gemini_cli._collect_expert_artifacts(tmp_path, "d1")
    assert env_lock is None
    assert deliverables is None


def test_summarize_cli_error_compacts_noisy_upstream_500():
    stderr = "\n".join([
        "YOLO mode is enabled. All tool calls will be automatically approved.",
        "YOLO mode is enabled. All tool calls will be automatically approved.",
        "Attempt 1 failed with status 500. Retrying with backoff...",
        '_ApiError: {"error":{"message":"Internal Server Error","code":500,"status":"Internal Server Error"}}',
        "at async Models.generateContentStream",
    ])

    assert gemini_cli._summarize_cli_error(stderr) == (
        "Expert model failed with upstream 500 Internal Server Error."
    )


def test_summarize_cli_error_preserves_non_500_errors():
    assert gemini_cli._summarize_cli_error("boom") == "boom"


# ---------------------------------------------------------------------------
# delegate_task (subprocess-mocked)
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


async def test_delegate_task_without_tool_context(active_project, monkeypatch):
    called = {}

    async def fake_acompletion(**kwargs):
        called["kwargs"] = kwargs
        return types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(content="ok")
                )
            ],
            usage=types.SimpleNamespace(prompt_tokens=5, completion_tokens=3, total_tokens=8),
            _hidden_params={"response_cost": 0.25},
        )

    monkeypatch.setattr(gemini_cli.litellm, "acompletion", fake_acompletion)

    result = await gemini_cli.delegate_task("please help")
    assert result["result"] == "ok"
    assert result["skills_used"] == []
    assert result["tools_used"] == {}
    assert called["kwargs"]["model"] == "gpt-5.4-proxy"
    assert called["kwargs"]["messages"] == [{"role": "user", "content": "please help"}]
    assert called["kwargs"]["timeout"] == 600
    assert called["kwargs"]["extra_headers"]["X-Kady-Project"] == active_project.id


async def test_delegate_task_routes_relative_working_dir_into_sandbox(active_project, monkeypatch):
    seen_cwd = {}

    async def fake_acompletion(**kwargs):
        seen_cwd["cwd"] = kwargs.get("cwd")
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))]
        )

    monkeypatch.setattr(gemini_cli.litellm, "acompletion", fake_acompletion)

    await gemini_cli.delegate_task("hi", working_directory="sub/dir")
    assert seen_cwd["cwd"] == str(active_project.sandbox / "sub" / "dir")
    assert (active_project.sandbox / "sub" / "dir").is_dir()


async def test_delegate_task_falls_back_to_sandbox_for_escape(active_project, monkeypatch, tmp_path):
    seen_cwd = {}

    async def fake_acompletion(**kwargs):
        seen_cwd["cwd"] = kwargs.get("cwd")
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))]
        )

    monkeypatch.setattr(gemini_cli.litellm, "acompletion", fake_acompletion)

    outside = tmp_path / "outside"
    outside.mkdir()
    await gemini_cli.delegate_task("hi", working_directory=str(outside))
    assert seen_cwd["cwd"] == str(active_project.sandbox)


async def test_delegate_task_subprocess_failure_raises(active_project, monkeypatch):
    async def fake_acompletion(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(gemini_cli.litellm, "acompletion", fake_acompletion)

    with pytest.raises(RuntimeError, match="boom"):
        await gemini_cli.delegate_task("hi")


async def test_delegate_task_subprocess_failure_raises_clean_500(active_project, monkeypatch):
    async def fake_acompletion(**kwargs):
        raise RuntimeError(
            '{"error":{"message":"Internal Server Error","code":500,"status":"Internal Server Error"}}'
        )

    monkeypatch.setattr(gemini_cli.litellm, "acompletion", fake_acompletion)

    with pytest.raises(
        RuntimeError,
        match="Expert model failed with upstream 500 Internal Server Error.",
    ):
        await gemini_cli.delegate_task("hi")


async def test_delegate_task_records_delegation_when_state_present(
    active_project, monkeypatch
):
    from kady_agent import manifest as manifest_module

    turn_id, _ = await manifest_module.open_turn(
        session_id="s1", user_text="p", model="gpt-5.4-proxy", expert_model="gpt-5.4-proxy"
    )

    state = {
        "_sessionId": "s1",
        "_turnId": turn_id,
        "_expertModel": "gpt-5.4-proxy",
    }
    ctx = types.SimpleNamespace(state=state)

    async def fake_acompletion(**kwargs):
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="done"))],
            usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30),
            _hidden_params={"response_cost": 0.125},
        )

    monkeypatch.setattr(gemini_cli.litellm, "acompletion", fake_acompletion)

    result = await gemini_cli.delegate_task("analyze", tool_context=ctx)
    assert result["result"] == "done"

    manifest = manifest_module.read_manifest("s1", turn_id)
    assert manifest is not None
    assert len(manifest["delegations"]) == 1
    assert manifest["delegations"][0]["id"] == "001"

    summary = cost_ledger.read_costs("s1", project_id=active_project.id)
    assert summary["expertUsd"] == pytest.approx(0.125)
    assert summary["entries"][0]["model"] == "gpt-5.4-proxy"


async def test_delegate_task_passes_custom_gemini_proxy_without_provider_prefix(active_project, monkeypatch):
    called = {}
    state = {
        "_expertModel": "gpt-5.4-proxy",
    }
    ctx = types.SimpleNamespace(state=state)

    async def fake_acompletion(**kwargs):
        called["kwargs"] = kwargs
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))]
        )

    monkeypatch.setattr(gemini_cli.litellm, "acompletion", fake_acompletion)

    await gemini_cli.delegate_task("analyze", tool_context=ctx)
    assert called["kwargs"]["model"] == "gpt-5.4-proxy"
    assert called["kwargs"]["api_base"] == "https://litellm.bisakerja.id"
    assert called["kwargs"]["custom_llm_provider"] == "openai"


async def test_delegate_task_passes_custom_openai_proxy_without_provider_prefix(active_project, monkeypatch):
    called = {}
    state = {
        "_expertModel": "gpt-5.4-proxy",
    }
    ctx = types.SimpleNamespace(state=state)

    async def fake_acompletion(**kwargs):
        called["kwargs"] = kwargs
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))]
        )

    monkeypatch.setattr(gemini_cli.litellm, "acompletion", fake_acompletion)

    await gemini_cli.delegate_task("analyze", tool_context=ctx)
    assert called["kwargs"]["model"] == "gpt-5.4-proxy"
    assert called["kwargs"]["api_base"] == "https://litellm.bisakerja.id"
    assert called["kwargs"]["custom_llm_provider"] == "openai"


async def test_delegate_task_does_not_fallback_to_orchestrator_model_for_expert(active_project, monkeypatch):
    called = {}
    state = {
        "_model": "gpt-5.4-proxy",
    }
    ctx = types.SimpleNamespace(state=state)

    async def fake_acompletion(**kwargs):
        called["kwargs"] = kwargs
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))]
        )

    monkeypatch.setattr(gemini_cli.litellm, "acompletion", fake_acompletion)
    monkeypatch.setenv("DEFAULT_EXPERT_MODEL", "gpt-5.4-proxy")

    await gemini_cli.delegate_task("analyze", tool_context=ctx)
    assert called["kwargs"]["model"] == "gpt-5.4-proxy"
    assert called["kwargs"]["model"] != "gpt-5.4-proxy"

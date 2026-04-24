# OpenAI-Compatible Default Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a config-driven OpenAI-compatible endpoint the default model provider, expose only the configured proxy models in the UI, and reject non-allowlisted model IDs without any provider fallback.

**Architecture:** The backend becomes the source of truth for model configuration by parsing required OpenAI-compatible env/config values, validating them at startup, and exposing a normalized model catalog endpoint for the frontend. LiteLLM routing is aligned to the same allowlist, while the orchestrator, expert subprocess, and revision endpoint all validate model overrides against that shared config.

**Tech Stack:** FastAPI, LiteLLM proxy, Python 3.13, pytest, httpx ASGI integration tests, Next.js/React, Vitest

---

## File Structure

- Modify: `kady_agent/env.example` — document the new required OpenAI-compatible env vars and set example default model values to proxy IDs.
- Modify: `server.py` — add config parsing, startup validation, model catalog endpoint, and allowlist enforcement helpers used by API handlers.
- Modify: `kady_agent/agent.py` — switch default model fallbacks to config-driven proxy IDs and validate incoming overrides.
- Modify: `kady_agent/tools/gemini_cli.py` — replace prefix-based expert routing checks with allowlist-based validation so expert model selection matches backend config.
- Modify: `litellm_config.yaml` — define explicit proxy model deployments against the OpenAI-compatible endpoint and remove the OpenRouter-first default path for primary models.
- Modify: `web/src/lib/use-models.ts` — fetch the backend-provided model catalog instead of static OpenRouter JSON.
- Modify: `web/src/components/model-selector.tsx` — stop deriving defaults from the static JSON file and consume backend-driven defaults.
- Modify: `web/src/app/page.tsx` — initialize selected models from the loaded catalog rather than compile-time constants.
- Modify: `web/src/components/workflows-panel.tsx` — initialize workflow model selection from the live catalog rather than static defaults.
- Create: `web/src/lib/model-catalog.ts` — centralize frontend model catalog fetch + default resolution helpers shared by hooks/components.
- Test: `tests/test_server_integration.py` — cover startup/config parsing, model catalog endpoint, and allowlist enforcement.
- Test: `tests/test_gemini_cli_tool.py` — cover allowlisted expert routing behavior.
- Create: `web/src/lib/use-models.test.ts` — verify backend model loading and fallback-free failure handling.
- Create: `web/src/lib/model-catalog.test.ts` — verify default model resolution logic from backend payloads.

### Task 1: Add backend config parsing and catalog shape

**Files:**
- Modify: `server.py:151-239`
- Modify: `kady_agent/env.example:1-29`
- Test: `tests/test_server_integration.py`

- [ ] **Step 1: Write the failing backend tests for provider config parsing and catalog exposure**

```python
async def test_config_endpoint_reports_proxy_provider_status(asgi_client, monkeypatch):
    monkeypatch.setenv("OPENAI_COMPAT_API_BASE", "https://litellm.bisakerja.id")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "sk-test")
    monkeypatch.setenv(
        "OPENAI_COMPAT_MODELS",
        json.dumps([
            {"id": "gpt-4.1-proxy", "label": "GPT-4.1 Proxy", "provider": "OpenAI Compatible"},
            {"id": "gpt-5.4-proxy", "label": "GPT-5.4 Proxy", "provider": "OpenAI Compatible", "default": True},
        ])
    )

    resp = await asgi_client.get("/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["modelProvider"] == {
        "configured": True,
        "apiBase": "https://litellm.bisakerja.id",
        "defaultModel": "gpt-5.4-proxy",
        "defaultExpertModel": "gpt-5.4-proxy",
    }


async def test_model_catalog_endpoint_returns_allowlisted_models(asgi_client, monkeypatch):
    monkeypatch.setenv("OPENAI_COMPAT_API_BASE", "https://litellm.bisakerja.id")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "sk-test")
    monkeypatch.setenv(
        "OPENAI_COMPAT_MODELS",
        json.dumps([
            {
                "id": "gemini-3-pro-proxy",
                "label": "Gemini 3 Pro Proxy",
                "provider": "OpenAI Compatible",
                "tier": "flagship",
                "context_length": 1048576,
                "pricing": {"prompt": 0.0, "completion": 0.0},
                "modality": "text->text",
                "description": "Proxy-routed Gemini model",
                "expertDefault": True,
            }
        ])
    )

    resp = await asgi_client.get("/models")
    assert resp.status_code == 200
    assert resp.json() == {
        "models": [
            {
                "id": "gemini-3-pro-proxy",
                "label": "Gemini 3 Pro Proxy",
                "provider": "OpenAI Compatible",
                "tier": "flagship",
                "context_length": 1048576,
                "pricing": {"prompt": 0.0, "completion": 0.0},
                "modality": "text->text",
                "description": "Proxy-routed Gemini model",
                "expertDefault": True,
            }
        ],
        "defaultModel": "gemini-3-pro-proxy",
        "defaultExpertModel": "gemini-3-pro-proxy",
    }
```

- [ ] **Step 2: Run the targeted backend tests to verify they fail first**

Run: `uv run pytest tests/test_server_integration.py -k "model_catalog or proxy_provider_status" -v`
Expected: FAIL with missing `/models` endpoint and missing `modelProvider` fields in `/config`.

- [ ] **Step 3: Implement provider config parsing, normalization, and `/models` endpoint**

```python
from functools import lru_cache
from typing import Literal

ModelTier = Literal["budget", "mid", "high", "flagship"]


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _parse_model_catalog(raw: str) -> list[dict]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OPENAI_COMPAT_MODELS must be valid JSON") from exc
    if not isinstance(parsed, list) or not parsed:
        raise RuntimeError("OPENAI_COMPAT_MODELS must be a non-empty JSON array")

    models: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise RuntimeError("Each OPENAI_COMPAT_MODELS entry must be an object")
        model_id = str(item.get("id") or "").strip()
        label = str(item.get("label") or model_id).strip()
        if not model_id:
            raise RuntimeError("Each OPENAI_COMPAT_MODELS entry requires a non-empty id")
        models.append(
            {
                "id": model_id,
                "label": label,
                "provider": str(item.get("provider") or "OpenAI Compatible"),
                "tier": str(item.get("tier") or "flagship"),
                "context_length": int(item.get("context_length") or 0),
                "pricing": {
                    "prompt": float((item.get("pricing") or {}).get("prompt") or 0.0),
                    "completion": float((item.get("pricing") or {}).get("completion") or 0.0),
                },
                "modality": item.get("modality") or "text->text",
                "description": str(item.get("description") or "Proxy-routed model"),
                "default": bool(item.get("default")),
                "expertDefault": bool(item.get("expertDefault")),
            }
        )
    return models


@lru_cache(maxsize=1)
def load_model_provider_config() -> dict:
    api_base = _required_env("OPENAI_COMPAT_API_BASE").rstrip("/")
    api_key = _required_env("OPENAI_COMPAT_API_KEY")
    models = _parse_model_catalog(_required_env("OPENAI_COMPAT_MODELS"))
    ids = {m["id"] for m in models}
    default_model = (os.environ.get("DEFAULT_AGENT_MODEL") or models[0]["id"]).strip()
    default_expert_model = (os.environ.get("DEFAULT_EXPERT_MODEL") or default_model).strip()
    if default_model not in ids:
        raise RuntimeError(f"DEFAULT_AGENT_MODEL must be one of {sorted(ids)}")
    if default_expert_model not in ids:
        raise RuntimeError(f"DEFAULT_EXPERT_MODEL must be one of {sorted(ids)}")
    return {
        "api_base": api_base,
        "api_key": api_key,
        "models": models,
        "allowed_model_ids": ids,
        "default_model": default_model,
        "default_expert_model": default_expert_model,
    }


@app.on_event("startup")
async def validate_model_provider_config() -> None:
    load_model_provider_config()


@app.get("/models")
async def get_model_catalog():
    cfg = load_model_provider_config()
    return {
        "models": cfg["models"],
        "defaultModel": cfg["default_model"],
        "defaultExpertModel": cfg["default_expert_model"],
    }
```

- [ ] **Step 4: Extend `/config` and `env.example` for the new provider configuration**

```python
@app.get("/config")
async def config():
    modal_id = os.environ.get("MODAL_TOKEN_ID", "").strip()
    modal_secret = os.environ.get("MODAL_TOKEN_SECRET", "").strip()
    provider = load_model_provider_config()
    return {
        "modal_configured": bool(modal_id and modal_secret),
        "modelProvider": {
            "configured": True,
            "apiBase": provider["api_base"],
            "defaultModel": provider["default_model"],
            "defaultExpertModel": provider["default_expert_model"],
        },
    }
```

```dotenv
# Default OpenAI-compatible provider routed through LiteLLM
OPENAI_COMPAT_API_BASE=https://litellm.bisakerja.id
OPENAI_COMPAT_API_KEY=
OPENAI_COMPAT_MODELS=[
  {"id":"gemini-3-flash-proxy","label":"Gemini 3 Flash Proxy","provider":"OpenAI Compatible","tier":"high","context_length":1048576,"pricing":{"prompt":0.0,"completion":0.0},"modality":"text->text","description":"Proxy-routed Gemini Flash model"},
  {"id":"gemini-3-pro-proxy","label":"Gemini 3 Pro Proxy","provider":"OpenAI Compatible","tier":"flagship","context_length":1048576,"pricing":{"prompt":0.0,"completion":0.0},"modality":"text->text","description":"Proxy-routed Gemini Pro model","expertDefault":true},
  {"id":"gemini-2.5-pro-proxy","label":"Gemini 2.5 Pro Proxy","provider":"OpenAI Compatible","tier":"flagship","context_length":1048576,"pricing":{"prompt":0.0,"completion":0.0},"modality":"text->text","description":"Proxy-routed Gemini 2.5 Pro model"},
  {"id":"gemini-3.1-flash-lite-preview-proxy","label":"Gemini 3.1 Flash Lite Preview Proxy","provider":"OpenAI Compatible","tier":"mid","context_length":1048576,"pricing":{"prompt":0.0,"completion":0.0},"modality":"text->text","description":"Proxy-routed Gemini Flash Lite model"},
  {"id":"gpt-4.1-proxy","label":"GPT-4.1 Proxy","provider":"OpenAI Compatible","tier":"high","context_length":1048576,"pricing":{"prompt":0.0,"completion":0.0},"modality":"text->text","description":"Proxy-routed GPT-4.1 model","default":true},
  {"id":"gpt-5.4-proxy","label":"GPT-5.4 Proxy","provider":"OpenAI Compatible","tier":"flagship","context_length":1048576,"pricing":{"prompt":0.0,"completion":0.0},"modality":"text->text","description":"Proxy-routed GPT-5.4 model"},
  {"id":"minimax-m2.5-proxy","label":"MiniMax M2.5 Proxy","provider":"OpenAI Compatible","tier":"high","context_length":1048576,"pricing":{"prompt":0.0,"completion":0.0},"modality":"text->text","description":"Proxy-routed MiniMax model"}
]
DEFAULT_AGENT_MODEL="gpt-4.1-proxy"
DEFAULT_EXPERT_MODEL="gemini-3-pro-proxy"
```

- [ ] **Step 5: Re-run the targeted backend tests**

Run: `uv run pytest tests/test_server_integration.py -k "model_catalog or proxy_provider_status" -v`
Expected: PASS with the new `/models` endpoint and `/config` payload.

- [ ] **Step 6: Commit the backend config foundation**

```bash
git add kady_agent/env.example server.py tests/test_server_integration.py
git commit -m "feat: add config-driven proxy model catalog"
```

### Task 2: Enforce the allowlist in backend runtime and expert routing

**Files:**
- Modify: `server.py:1035-1099`
- Modify: `kady_agent/agent.py:26-39,161-166,181-210`
- Modify: `kady_agent/tools/gemini_cli.py:30-45,197-267`
- Test: `tests/test_server_integration.py`
- Test: `tests/test_gemini_cli_tool.py`

- [ ] **Step 1: Write failing tests for invalid model rejection and expert allowlist routing**

```python
async def test_revise_markdown_rejects_non_allowlisted_model(asgi_client, monkeypatch):
    monkeypatch.setenv("OPENAI_COMPAT_API_BASE", "https://litellm.bisakerja.id")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "sk-test")
    monkeypatch.setenv(
        "OPENAI_COMPAT_MODELS",
        json.dumps([
            {"id": "gpt-4.1-proxy", "label": "GPT-4.1 Proxy", "provider": "OpenAI Compatible"}
        ])
    )
    monkeypatch.setenv("DEFAULT_AGENT_MODEL", "gpt-4.1-proxy")

    resp = await asgi_client.post(
        "/revise-markdown",
        json={
            "selection": "Hello",
            "instruction": "Rewrite it",
            "model": "openrouter/anthropic/claude-opus-4.7",
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unsupported model: openrouter/anthropic/claude-opus-4.7"
```

```python
def test_cli_can_route_allowlisted_models(monkeypatch):
    monkeypatch.setenv(
        "OPENAI_COMPAT_MODELS",
        json.dumps([
            {"id": "gpt-4.1-proxy", "label": "GPT-4.1 Proxy", "provider": "OpenAI Compatible"},
            {"id": "gemini-3-pro-proxy", "label": "Gemini 3 Pro Proxy", "provider": "OpenAI Compatible"},
        ])
    )
    assert gemini_cli._cli_can_route("gpt-4.1-proxy") is True
    assert gemini_cli._cli_can_route("gemini-3-pro-proxy") is True
    assert gemini_cli._cli_can_route("openrouter/anthropic/claude-opus-4.7") is False
```

- [ ] **Step 2: Run the backend and gemini CLI tests to confirm they fail first**

Run: `uv run pytest tests/test_server_integration.py -k "non_allowlisted_model" tests/test_gemini_cli_tool.py -k "cli_can_route_allowlisted" -v`
Expected: FAIL because the runtime currently accepts arbitrary overrides and `_cli_can_route()` only checks prefixes.

- [ ] **Step 3: Implement shared allowlist validation in `server.py` and `agent.py`**

```python
def ensure_allowed_model(model_id: str) -> str:
    model = model_id.strip()
    if not model:
        raise HTTPException(status_code=400, detail="model is required")
    cfg = load_model_provider_config()
    if model not in cfg["allowed_model_ids"]:
        raise HTTPException(status_code=400, detail=f"Unsupported model: {model}")
    return model
```

```python
DEFAULT_MODEL = os.getenv("DEFAULT_AGENT_MODEL") or "gpt-4.1-proxy"
DEFAULT_EXPERT_MODEL = os.getenv("DEFAULT_EXPERT_MODEL") or DEFAULT_MODEL


def _override_model(callback_context, llm_request):
    override = callback_context.state.get("_model")
    if isinstance(override, str) and override.strip():
        from server import ensure_allowed_model
        llm_request.model = ensure_allowed_model(override)
    _inject_tracking_headers(callback_context)
    return None
```

```python
model = (
    ensure_allowed_model(str(model_override))
    if isinstance(model_override, str) and model_override.strip()
    else DEFAULT_MODEL
)
```

- [ ] **Step 4: Replace prefix checks in `gemini_cli.py` with allowlist-backed routing**

```python
@functools.lru_cache(maxsize=1)
def _allowed_model_ids() -> set[str]:
    raw = os.environ.get("OPENAI_COMPAT_MODELS", "")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return set()
    return {
        str(item.get("id") or "").strip()
        for item in parsed
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }


def _cli_can_route(model: str) -> bool:
    return model.strip() in _allowed_model_ids()
```

```python
if selected_model and _cli_can_route(selected_model):
    cli_args.extend(["-m", selected_model])
```

- [ ] **Step 5: Re-run the targeted allowlist tests**

Run: `uv run pytest tests/test_server_integration.py -k "non_allowlisted_model" tests/test_gemini_cli_tool.py -k "cli_can_route_allowlisted" -v`
Expected: PASS with unsupported models rejected and allowlisted proxy IDs routed to the expert subprocess.

- [ ] **Step 6: Commit the runtime allowlist enforcement**

```bash
git add server.py kady_agent/agent.py kady_agent/tools/gemini_cli.py tests/test_server_integration.py tests/test_gemini_cli_tool.py
git commit -m "feat: enforce proxy model allowlist"
```

### Task 3: Align LiteLLM routing with the configured proxy models

**Files:**
- Modify: `litellm_config.yaml:1-90`
- Test: `tests/test_server_integration.py`

- [ ] **Step 1: Write a failing integration test that asserts the configured defaults are proxy IDs**

```python
async def test_model_catalog_defaults_match_proxy_ids(asgi_client, monkeypatch):
    monkeypatch.setenv("OPENAI_COMPAT_API_BASE", "https://litellm.bisakerja.id")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "sk-test")
    monkeypatch.setenv(
        "OPENAI_COMPAT_MODELS",
        json.dumps([
            {"id": "gpt-4.1-proxy", "label": "GPT-4.1 Proxy", "provider": "OpenAI Compatible", "default": True},
            {"id": "gemini-3-pro-proxy", "label": "Gemini 3 Pro Proxy", "provider": "OpenAI Compatible", "expertDefault": True},
        ])
    )
    monkeypatch.setenv("DEFAULT_AGENT_MODEL", "gpt-4.1-proxy")
    monkeypatch.setenv("DEFAULT_EXPERT_MODEL", "gemini-3-pro-proxy")

    resp = await asgi_client.get("/models")
    assert resp.status_code == 200
    assert resp.json()["defaultModel"] == "gpt-4.1-proxy"
    assert resp.json()["defaultExpertModel"] == "gemini-3-pro-proxy"
```

- [ ] **Step 2: Run the targeted defaults test before editing LiteLLM routing**

Run: `uv run pytest tests/test_server_integration.py -k "defaults_match_proxy_ids" -v`
Expected: FAIL until the default fallbacks and config assumptions are fully aligned.

- [ ] **Step 3: Replace the OpenRouter-first primary model entries in `litellm_config.yaml` with explicit proxy deployments**

```yaml
.openai_compat_shared: &openai_compat_shared
  api_base: os.environ/OPENAI_COMPAT_API_BASE
  api_key: os.environ/OPENAI_COMPAT_API_KEY
  custom_llm_provider: openai
  timeout: 600
  stream_timeout: 600

model_list:
  - model_name: gemini-3-flash-proxy
    litellm_params:
      <<: *openai_compat_shared
      model: gemini-3-flash-proxy
  - model_name: gemini-3-pro-proxy
    litellm_params:
      <<: *openai_compat_shared
      model: gemini-3-pro-proxy
  - model_name: gemini-2.5-pro-proxy
    litellm_params:
      <<: *openai_compat_shared
      model: gemini-2.5-pro-proxy
  - model_name: gemini-3.1-flash-lite-preview-proxy
    litellm_params:
      <<: *openai_compat_shared
      model: gemini-3.1-flash-lite-preview-proxy
  - model_name: gpt-4.1-proxy
    litellm_params:
      <<: *openai_compat_shared
      model: gpt-4.1-proxy
  - model_name: gpt-5.4-proxy
    litellm_params:
      <<: *openai_compat_shared
      model: gpt-5.4-proxy
  - model_name: minimax-m2.5-proxy
    litellm_params:
      <<: *openai_compat_shared
      model: minimax-m2.5-proxy
```

- [ ] **Step 4: Remove the OpenRouter wildcard aliases that no longer serve the default model path**

```yaml
router_settings:
  num_retries: 4
  model_group_alias:
    "gemini-3-pro-proxy-customtools": "gemini-3-pro-proxy"
    "gemini-3-flash-proxy-customtools": "gemini-3-flash-proxy"
    "gemini-2.5-pro-proxy-customtools": "gemini-2.5-pro-proxy"
    "gemini-3.1-flash-lite-preview-proxy-customtools": "gemini-3.1-flash-lite-preview-proxy"
```

- [ ] **Step 5: Re-run the defaults test**

Run: `uv run pytest tests/test_server_integration.py -k "defaults_match_proxy_ids" -v`
Expected: PASS with backend defaults and LiteLLM routing assumptions aligned to proxy IDs.

- [ ] **Step 6: Commit the LiteLLM routing changes**

```bash
git add litellm_config.yaml tests/test_server_integration.py
git commit -m "feat: route default models through openai-compatible proxy"
```

### Task 4: Move frontend model loading to the backend catalog

**Files:**
- Create: `web/src/lib/model-catalog.ts`
- Modify: `web/src/lib/use-models.ts`
- Create: `web/src/lib/model-catalog.test.ts`
- Create: `web/src/lib/use-models.test.ts`

- [ ] **Step 1: Write failing frontend tests for catalog fetch and no-static fallback behavior**

```ts
import { describe, expect, it } from "vitest";
import { fetchModelCatalog, resolveCatalogDefaults } from "./model-catalog";

describe("resolveCatalogDefaults", () => {
  it("returns the backend-marked orchestrator and expert defaults", () => {
    const catalog = {
      models: [
        { id: "gpt-4.1-proxy", label: "GPT-4.1 Proxy", provider: "OpenAI Compatible", tier: "high", context_length: 1048576, pricing: { prompt: 0, completion: 0 }, modality: "text->text", description: "", default: true },
        { id: "gemini-3-pro-proxy", label: "Gemini 3 Pro Proxy", provider: "OpenAI Compatible", tier: "flagship", context_length: 1048576, pricing: { prompt: 0, completion: 0 }, modality: "text->text", description: "", expertDefault: true },
      ],
      defaultModel: "gpt-4.1-proxy",
      defaultExpertModel: "gemini-3-pro-proxy",
    };

    expect(resolveCatalogDefaults(catalog)).toEqual({
      defaultModelId: "gpt-4.1-proxy",
      defaultExpertModelId: "gemini-3-pro-proxy",
    });
  });
});
```

```ts
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/projects", () => ({
  apiFetch: vi.fn(),
  onProjectChange: vi.fn(() => () => {}),
}));

import { apiFetch } from "@/lib/projects";
import { useModels } from "./use-models";

describe("useModels", () => {
  beforeEach(() => vi.resetAllMocks());

  it("loads models from the backend catalog endpoint", async () => {
    vi.mocked(apiFetch).mockResolvedValue(
      new Response(
        JSON.stringify({
          models: [
            { id: "gpt-4.1-proxy", label: "GPT-4.1 Proxy", provider: "OpenAI Compatible", tier: "high", context_length: 1048576, pricing: { prompt: 0, completion: 0 }, modality: "text->text", description: "", default: true },
          ],
          defaultModel: "gpt-4.1-proxy",
          defaultExpertModel: "gpt-4.1-proxy",
        }),
        { status: 200 }
      )
    );

    const { result } = renderHook(() => useModels());

    await waitFor(() => {
      expect(result.current.models).toHaveLength(1);
    });
    expect(result.current.models[0].id).toBe("gpt-4.1-proxy");
  });
});
```

- [ ] **Step 2: Run the frontend tests to verify they fail first**

Run: `npm --prefix web test -- src/lib/model-catalog.test.ts src/lib/use-models.test.ts`
Expected: FAIL because the helper module does not exist and `useModels()` still reads static OpenRouter JSON.

- [ ] **Step 3: Implement `model-catalog.ts` and refactor `use-models.ts` to fetch `/models`**

```ts
import type { Model } from "@/components/model-selector";
import { apiFetch } from "@/lib/projects";

export interface ModelCatalogResponse {
  models: Model[];
  defaultModel: string;
  defaultExpertModel: string;
}

export async function fetchModelCatalog(): Promise<ModelCatalogResponse> {
  const resp = await apiFetch("/models");
  if (!resp.ok) throw new Error(`Failed to load models: ${resp.status}`);
  return resp.json() as Promise<ModelCatalogResponse>;
}

export function resolveCatalogDefaults(catalog: ModelCatalogResponse) {
  return {
    defaultModelId: catalog.defaultModel,
    defaultExpertModelId: catalog.defaultExpertModel,
  };
}
```

```ts
export interface UseModelsReturn {
  models: Model[];
  defaultModelId: string | null;
  defaultExpertModelId: string | null;
  refresh: () => void;
}

export function useModels(): UseModelsReturn {
  const [models, setModels] = useState<Model[]>([]);
  const [defaultModelId, setDefaultModelId] = useState<string | null>(null);
  const [defaultExpertModelId, setDefaultExpertModelId] = useState<string | null>(null);

  const refresh = useCallback(() => {
    fetchModelCatalog()
      .then((catalog) => {
        setModels(Array.isArray(catalog.models) ? catalog.models : []);
        setDefaultModelId(catalog.defaultModel || null);
        setDefaultExpertModelId(catalog.defaultExpertModel || null);
      })
      .catch(() => {
        setModels([]);
        setDefaultModelId(null);
        setDefaultExpertModelId(null);
      });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => onProjectChange(() => refresh()), [refresh]);

  return { models, defaultModelId, defaultExpertModelId, refresh };
}
```

- [ ] **Step 4: Re-run the frontend catalog tests**

Run: `npm --prefix web test -- src/lib/model-catalog.test.ts src/lib/use-models.test.ts`
Expected: PASS with the hook loading models from `/models` and no static fallback.

- [ ] **Step 5: Commit the frontend catalog refactor**

```bash
git add web/src/lib/model-catalog.ts web/src/lib/model-catalog.test.ts web/src/lib/use-models.ts web/src/lib/use-models.test.ts
git commit -m "feat: load model catalog from backend"
```

### Task 5: Switch UI defaults and selectors to the live catalog

**Files:**
- Modify: `web/src/components/model-selector.tsx`
- Modify: `web/src/app/page.tsx:975-980`
- Modify: `web/src/components/workflows-panel.tsx:283-348`
- Test: `web/src/lib/model-catalog.test.ts`
- Test: `web/src/lib/use-models.test.ts`

- [ ] **Step 1: Write a failing frontend test for default model selection from the live catalog**

```ts
import { describe, expect, it } from "vitest";
import type { Model } from "@/components/model-selector";
import { pickDefaultModel } from "./model-catalog";

const MODELS: Model[] = [
  { id: "gpt-4.1-proxy", label: "GPT-4.1 Proxy", provider: "OpenAI Compatible", tier: "high", context_length: 1048576, pricing: { prompt: 0, completion: 0 }, modality: "text->text", description: "", default: true },
  { id: "gemini-3-pro-proxy", label: "Gemini 3 Pro Proxy", provider: "OpenAI Compatible", tier: "flagship", context_length: 1048576, pricing: { prompt: 0, completion: 0 }, modality: "text->text", description: "", expertDefault: true },
];

describe("pickDefaultModel", () => {
  it("finds the configured default in the loaded catalog", () => {
    expect(pickDefaultModel(MODELS, "gpt-4.1-proxy")?.id).toBe("gpt-4.1-proxy");
    expect(pickDefaultModel(MODELS, "gemini-3-pro-proxy")?.id).toBe("gemini-3-pro-proxy");
  });
});
```

- [ ] **Step 2: Run the default-selection test before UI edits**

Run: `npm --prefix web test -- src/lib/model-catalog.test.ts`
Expected: FAIL because no helper currently resolves runtime defaults for the UI state.

- [ ] **Step 3: Refactor `model-selector.tsx`, `page.tsx`, and `workflows-panel.tsx` to use runtime defaults**

```ts
export function pickDefaultModel(models: Model[], preferredId: string | null): Model | null {
  if (!models.length) return null;
  return models.find((m) => m.id === preferredId) ?? models[0] ?? null;
}
```

```ts
const { models: allModels, defaultModelId, defaultExpertModelId, refresh } = useModels();
const hasStaticDefaults = allModels.length > 0;
const isRecommended = (m: Model): boolean =>
  role === "expert"
    ? m.id === defaultExpertModelId
    : m.id === defaultModelId;
```

```ts
const { models, defaultModelId, defaultExpertModelId } = useModels();
const [selectedModel, setSelectedModel] = useState<Model | null>(null);
const [selectedExpertModel, setSelectedExpertModel] = useState<Model | null>(null);

useEffect(() => {
  if (!models.length) return;
  setSelectedModel((current) => current ?? pickDefaultModel(models, defaultModelId));
  setSelectedExpertModel((current) => current ?? pickDefaultModel(models, defaultExpertModelId));
}, [models, defaultModelId, defaultExpertModelId]);
```

```ts
const { models, defaultModelId } = useModels();
const [model, setModel] = useState<Model | null>(null);

useEffect(() => {
  if (!models.length) return;
  setModel((current) => current ?? pickDefaultModel(models, defaultModelId));
}, [models, defaultModelId]);
```

- [ ] **Step 4: Re-run the frontend tests and a focused build check**

Run: `npm --prefix web test -- src/lib/model-catalog.test.ts src/lib/use-models.test.ts && npm --prefix web run build`
Expected: PASS with selectors using backend defaults and the app compiling without references to static defaults.

- [ ] **Step 5: Commit the UI default-selection changes**

```bash
git add web/src/components/model-selector.tsx web/src/app/page.tsx web/src/components/workflows-panel.tsx web/src/lib/model-catalog.ts web/src/lib/model-catalog.test.ts web/src/lib/use-models.test.ts
git commit -m "feat: drive model selection from live proxy catalog"
```

### Task 6: Final verification and cleanup

**Files:**
- Modify: none unless verification reveals issues
- Test: `tests/test_server_integration.py`
- Test: `tests/test_gemini_cli_tool.py`
- Test: `web/src/lib/model-catalog.test.ts`
- Test: `web/src/lib/use-models.test.ts`

- [ ] **Step 1: Run the full backend test slice for affected files**

Run: `uv run pytest tests/test_server_integration.py tests/test_gemini_cli_tool.py -v`
Expected: PASS for config parsing, allowlist enforcement, and expert routing tests.

- [ ] **Step 2: Run the full frontend test slice for affected files**

Run: `npm --prefix web test -- src/lib/model-catalog.test.ts src/lib/use-models.test.ts`
Expected: PASS for backend-driven catalog loading and runtime default selection.

- [ ] **Step 3: Verify the working tree only contains intended files**

Run: `git status --short`
Expected: Only the planned backend, frontend, config, and test files are modified.

- [ ] **Step 4: Commit the final verification state if fixes were required**

```bash
git add server.py kady_agent/agent.py kady_agent/tools/gemini_cli.py litellm_config.yaml web/src/lib/model-catalog.ts web/src/lib/use-models.ts web/src/components/model-selector.tsx web/src/app/page.tsx web/src/components/workflows-panel.tsx tests/test_server_integration.py tests/test_gemini_cli_tool.py web/src/lib/model-catalog.test.ts web/src/lib/use-models.test.ts
git commit -m "test: verify openai-compatible default provider flow"
```

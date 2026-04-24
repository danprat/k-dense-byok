# OpenAI-Compatible Proxy as Default Model Provider

## Summary

Replace the current default OpenRouter-backed model catalog and routing with a config-driven OpenAI-compatible provider backed by LiteLLM at a custom endpoint. The backend becomes the single source of truth for:

- provider endpoint and API key
- allowed model list
- default agent model
- default expert model

The frontend stops treating the static OpenRouter catalog as the primary model source and instead renders the model list returned by the backend.

## Goals

- Make an OpenAI-compatible endpoint the default provider for model execution.
- Read endpoint, API key, and allowed models from environment/config only.
- Limit the model dropdown to the configured proxy model list.
- Route agent and expert model selections through LiteLLM to the configured endpoint.
- Fail explicitly when required proxy configuration is missing or invalid.

## Non-Goals

- No UI for editing provider settings.
- No fallback to OpenRouter when the proxy is unavailable.
- No mixed provider selection model for this change.
- No hardcoded secret values in source files.

## Configuration Design

Add server-side configuration for the default OpenAI-compatible provider:

- `OPENAI_COMPAT_API_BASE`
- `OPENAI_COMPAT_API_KEY`
- `OPENAI_COMPAT_MODELS`
- `DEFAULT_AGENT_MODEL`
- `DEFAULT_EXPERT_MODEL`

`OPENAI_COMPAT_MODELS` should define the allowlisted models exposed to the UI and accepted by the backend. The initial configured list is:

- `gemini-3-flash-proxy`
- `gemini-3-pro-proxy`
- `gemini-2.5-pro-proxy`
- `gemini-3.1-flash-lite-preview-proxy`
- `gpt-4.1-proxy`
- `gpt-5.4-proxy`
- `minimax-m2.5-proxy`

The config format should be easy to edit in `.env` or equivalent runtime configuration. The backend must parse this into a normalized list and reject empty or malformed required values.

## Architecture

### Backend as source of truth

The backend will own the active model catalog. Instead of shipping a frontend-first OpenRouter catalog, the server will expose a model list derived from config. The returned payload should match the existing frontend `Model` shape closely enough to avoid UI rewrites.

### LiteLLM routing

`litellm_config.yaml` will define model entries for each allowlisted proxy model. Each entry will route to the configured OpenAI-compatible endpoint using LiteLLM's OpenAI-compatible provider settings.

This keeps runtime routing and visible model availability aligned.

### Frontend model loading

The frontend model hook will load models from a backend endpoint rather than treating `web/src/data/models.json` as the primary catalog. The selector UI remains unchanged except for sourcing its data from the backend response.

## Components Affected

### `litellm_config.yaml`

- Add or replace model definitions so the allowlisted proxy models route to the configured OpenAI-compatible endpoint.
- Use environment-backed values for `api_base` and `api_key`.
- Remove default-path assumptions that require OpenRouter for the primary model set.

### `kady_agent/env.example`

- Document the new OpenAI-compatible endpoint variables.
- Document the allowlisted model list configuration.
- Set example defaults for `DEFAULT_AGENT_MODEL` and `DEFAULT_EXPERT_MODEL` using proxy model IDs.
- Do not include a real API key.

### `server.py`

- Add config parsing and validation for the OpenAI-compatible provider settings.
- Add an endpoint that returns the configured model catalog in the UI's model shape.
- Reject requests that reference models outside the configured allowlist.
- Fail fast when required provider config is missing.

### `web/src/lib/use-models.ts`

- Replace the static OpenRouter-first loading path with a backend fetch for the configured model catalog.
- Preserve the current hook contract for downstream UI components.

### `web/src/components/model-selector.tsx`

- Keep the existing presentation logic.
- Continue rendering the shared `Model` shape returned by the updated hook.

## Data Flow

1. The app starts.
2. The backend reads provider config from environment/runtime config.
3. The backend validates endpoint, API key, and allowlisted models.
4. The backend exposes the normalized model catalog to the frontend.
5. The frontend fetches that catalog and renders only the configured proxy models.
6. The user selects a model.
7. The selected model ID is passed through the existing agent/expert flow.
8. LiteLLM routes the request to the configured OpenAI-compatible endpoint.
9. If the model is not in the allowlist, the backend rejects it.

## Error Handling

### Startup validation

Treat the proxy configuration as required. If any required setting is missing or unusable, startup should fail with a direct configuration error.

### Runtime validation

If a request references a model outside the configured allowlist, return a clear error instead of attempting a fallback.

### Endpoint failures

If the upstream proxy endpoint is down or returns an error, surface that failure. Do not retry by switching providers.

## Testing Strategy

- Unit tests for config parsing and validation.
- Unit tests for the backend model catalog endpoint.
- Unit tests for allowlist enforcement.
- Frontend tests for loading and rendering the backend-provided model list.
- Integration test covering configured model selection through the server-side flow.

## Success Criteria

- The app starts successfully when the required OpenAI-compatible config is present.
- The model dropdown shows only the configured proxy models.
- Default agent and expert models can use configured proxy model IDs.
- Requests for configured models route to the OpenAI-compatible endpoint through LiteLLM.
- Requests for non-allowlisted models are rejected clearly.
- Missing required config fails fast without silent fallback.

## Open Questions Resolved

- Provider behavior: single default provider only.
- Model visibility: only configured proxy models appear.
- Configuration surface: config only, not editable in UI.
- Fallback behavior: none.

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import type { Model } from "@/components/model-selector";
import { apiFetch, onProjectChange } from "@/lib/projects";

interface DynamicModelListResponse {
  available?: boolean;
  configured?: boolean;
  models?: Model[];
}

export interface UseModelsReturn {
  /** Every model currently available from the supported live providers. */
  models: Model[];
  /** Just the Ollama-sourced entries, in the order returned by the backend. */
  ollamaModels: Model[];
  /** Just the custom-endpoint entries, in the order returned by the backend. */
  customModels: Model[];
  /** True when the backend was able to reach `OLLAMA_BASE_URL/api/tags`. */
  ollamaAvailable: boolean;
  /** True when the custom provider is configured and available. */
  customAvailable: boolean;
  /** Explicit orchestrator default, preferring the custom GPT proxy when available. */
  defaultModel?: Model;
  /** Explicit expert default, preferring the custom Gemini proxy when available. */
  defaultExpertModel?: Model;
  /** Re-fetch the dynamic provider lists. */
  refresh: () => void;
}

/**
 * Return the models currently exposed by our supported runtime providers.
 *
 * We intentionally prefer live provider discovery over the old static
 * OpenRouter catalogue so both the orchestrator and Gemini CLI expert stay
 * on the project's configured custom endpoint (or local Ollama) end-to-end.
 */
export function useModels(): UseModelsReturn {
  const [ollamaModels, setOllamaModels] = useState<Model[]>([]);
  const [customModels, setCustomModels] = useState<Model[]>([]);
  const [ollamaAvailable, setOllamaAvailable] = useState(false);
  const [customAvailable, setCustomAvailable] = useState(false);

  const fetchDynamicModels = useCallback(() => {
    apiFetch("/ollama/models")
      .then((r) => (r.ok ? (r.json() as Promise<DynamicModelListResponse>) : null))
      .then((data) => {
        if (!data) return;
        setOllamaAvailable(Boolean(data.available));
        setOllamaModels(Array.isArray(data.models) ? data.models : []);
      })
      .catch(() => {
        setOllamaAvailable(false);
        setOllamaModels([]);
      });

    apiFetch("/custom/models")
      .then((r) => (r.ok ? (r.json() as Promise<DynamicModelListResponse>) : null))
      .then((data) => {
        if (!data) return;
        setCustomAvailable(Boolean(data.available));
        setCustomModels(Array.isArray(data.models) ? data.models : []);
      })
      .catch(() => {
        setCustomAvailable(false);
        setCustomModels([]);
      });
  }, []);

  useEffect(() => {
    fetchDynamicModels();
  }, [fetchDynamicModels]);

  useEffect(() => onProjectChange(() => fetchDynamicModels()), [fetchDynamicModels]);

  const models = useMemo(() => [...customModels, ...ollamaModels], [customModels, ollamaModels]);

  const defaultModel = useMemo(
    () => customModels.find((m) => m.id === "custom/gpt-5.4-proxy") ?? customModels[0] ?? ollamaModels[0],
    [customModels, ollamaModels],
  );

  const defaultExpertModel = useMemo(
    () => customModels.find((m) => m.id === "custom/gemini-3-pro-proxy"),
    [customModels],
  );

  return {
    models,
    ollamaModels,
    customModels,
    ollamaAvailable,
    customAvailable,
    defaultModel,
    defaultExpertModel,
    refresh: fetchDynamicModels,
  };
}

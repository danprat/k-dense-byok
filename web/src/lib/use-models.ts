"use client";

import { useCallback, useEffect, useState } from "react";

import staticModels from "@/data/models.json";
import type { Model } from "@/components/model-selector";
import { apiFetch, onProjectChange } from "@/lib/projects";

const OPENROUTER_MODELS = staticModels as Model[];

interface OllamaListResponse {
  available?: boolean;
  models?: Model[];
}

export interface UseModelsReturn {
  /** Every model available to the user: static OpenRouter catalogue + live Ollama tags. */
  models: Model[];
  /** Just the Ollama-sourced entries, in the order returned by the backend. */
  ollamaModels: Model[];
  /** True when the backend was able to reach `OLLAMA_BASE_URL/api/tags`. */
  ollamaAvailable: boolean;
  /** Re-fetch the Ollama list. */
  refresh: () => void;
}

/**
 * Merge the static OpenRouter-derived `models.json` with whatever models
 * are currently pulled in the user's local Ollama server.
 *
 * Ollama discovery is best-effort: if the daemon is offline we silently
 * fall back to OpenRouter-only. The hook re-fetches on project change to
 * keep the list fresh when the user returns after pulling a new model.
 */
export function useModels(): UseModelsReturn {
  const [ollamaModels, setOllamaModels] = useState<Model[]>([]);
  const [ollamaAvailable, setOllamaAvailable] = useState(false);

  const fetchOllama = useCallback(() => {
    apiFetch("/ollama/models")
      .then((r) => (r.ok ? (r.json() as Promise<OllamaListResponse>) : null))
      .then((data) => {
        if (!data) return;
        setOllamaAvailable(Boolean(data.available));
        setOllamaModels(Array.isArray(data.models) ? data.models : []);
      })
      .catch(() => {
        setOllamaAvailable(false);
        setOllamaModels([]);
      });
  }, []);

  useEffect(() => {
    fetchOllama();
  }, [fetchOllama]);

  useEffect(() => onProjectChange(() => fetchOllama()), [fetchOllama]);

  return {
    models: [...OPENROUTER_MODELS, ...ollamaModels],
    ollamaModels,
    ollamaAvailable,
    refresh: fetchOllama,
  };
}

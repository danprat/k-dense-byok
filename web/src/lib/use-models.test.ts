import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
const onProjectChangeMock = vi.fn(() => () => {});

vi.mock("@/lib/projects", () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
  onProjectChange: (...args: unknown[]) => onProjectChangeMock(...args),
}));

import { useModels } from "./use-models";

describe("useModels", () => {
  beforeEach(() => {
    apiFetchMock.mockReset();
    onProjectChangeMock.mockClear();
  });

  it("returns only live custom and ollama models", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/ollama/models") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            available: true,
            models: [
              {
                id: "ollama/qwen3.6:8b",
                label: "qwen3.6:8b",
                provider: "Ollama",
                tier: "budget",
                context_length: 0,
                pricing: { prompt: 0, completion: 0 },
                modality: "text->text",
                description: "Local model",
              },
            ],
          }),
        });
      }
      if (path === "/custom/models") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            available: true,
            configured: true,
            models: [
              {
                id: "custom/gpt-5.4-proxy",
                label: "gpt-5.4-proxy",
                provider: "Custom",
                tier: "high",
                context_length: 0,
                pricing: { prompt: 0, completion: 0 },
                modality: "text->text",
                description: "Custom endpoint model",
              },
            ],
          }),
        });
      }
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });

    const { result } = renderHook(() => useModels());

    await waitFor(() => {
      expect(result.current.customAvailable).toBe(true);
      expect(result.current.ollamaAvailable).toBe(true);
    });

    expect(result.current.models).toEqual([
      expect.objectContaining({ id: "custom/gpt-5.4-proxy" }),
      expect.objectContaining({ id: "ollama/qwen3.6:8b" }),
    ]);
    expect(result.current.defaultModel).toEqual(
      expect.objectContaining({ id: "custom/gpt-5.4-proxy" }),
    );
  });

  it("exposes the first custom Gemini proxy as the explicit expert default", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/ollama/models") {
        return Promise.resolve({ ok: true, json: async () => ({ available: true, models: [] }) });
      }
      if (path === "/custom/models") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            available: true,
            configured: true,
            models: [
              {
                id: "custom/gpt-5.4-proxy",
                label: "gpt-5.4-proxy",
                provider: "Custom",
                tier: "high",
                context_length: 0,
                pricing: { prompt: 0, completion: 0 },
                modality: "text->text",
                description: "Custom endpoint model",
              },
              {
                id: "custom/gemini-3-pro-proxy",
                label: "gemini-3-pro-proxy",
                provider: "Custom",
                tier: "high",
                context_length: 0,
                pricing: { prompt: 0, completion: 0 },
                modality: "text->text",
                description: "Custom Gemini expert model",
              },
            ],
          }),
        });
      }
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });

    const { result } = renderHook(() => useModels());

    await waitFor(() => {
      expect(result.current.customAvailable).toBe(true);
    });

    expect(result.current.defaultExpertModel).toEqual(
      expect.objectContaining({ id: "custom/gemini-3-pro-proxy" }),
    );
  });

  it("does not fall back to the orchestrator default when no Gemini proxy exists", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/ollama/models") {
        return Promise.resolve({ ok: true, json: async () => ({ available: true, models: [] }) });
      }
      if (path === "/custom/models") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            available: true,
            configured: true,
            models: [
              {
                id: "custom/gpt-5.4-proxy",
                label: "gpt-5.4-proxy",
                provider: "Custom",
                tier: "high",
                context_length: 0,
                pricing: { prompt: 0, completion: 0 },
                modality: "text->text",
                description: "Custom endpoint model",
              },
            ],
          }),
        });
      }
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });

    const { result } = renderHook(() => useModels());

    await waitFor(() => {
      expect(result.current.customAvailable).toBe(true);
    });

    expect(result.current.defaultExpertModel).toBeUndefined();
  });
});

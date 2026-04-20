"use client";

import { useEffect, useState } from "react";

import { apiFetch, getActiveProjectId, onProjectChange } from "@/lib/projects";

export type BudgetState = "ok" | "warn" | "exceeded";

export interface ProjectSessionCostRow {
  sessionId: string;
  totalUsd: number;
  totalTokens: number;
  orchestratorUsd: number;
  expertUsd: number;
  hasPending: boolean;
}

export interface ProjectBudgetStatus {
  totalUsd: number;
  limitUsd: number | null;
  ratio: number | null;
  state: BudgetState;
}

export interface ProjectCostSummary {
  projectId: string;
  totalUsd: number;
  orchestratorUsd: number;
  expertUsd: number;
  totalTokens: number;
  orchestratorTokens: number;
  expertTokens: number;
  sessionCount: number;
  hasPending: boolean;
  bySession: Record<string, ProjectSessionCostRow>;
  limitUsd: number | null;
  budget: ProjectBudgetStatus;
}

function emptySummary(projectId: string): ProjectCostSummary {
  return {
    projectId,
    totalUsd: 0,
    orchestratorUsd: 0,
    expertUsd: 0,
    totalTokens: 0,
    orchestratorTokens: 0,
    expertTokens: 0,
    sessionCount: 0,
    hasPending: false,
    bySession: {},
    limitUsd: null,
    budget: { totalUsd: 0, limitUsd: null, ratio: null, state: "ok" },
  };
}

/**
 * Fetches the cumulative cost across every session in the currently-active
 * project. ``refreshKey`` is a monotonic counter bumped whenever a turn
 * completes (see page.tsx) so the header pill reflects the latest totals
 * after the session ledger absorbs new rows.
 *
 * Also subscribes to project-change events so switching projects reloads
 * the totals, and keeps a short polling loop while any session reports
 * ``costPending`` entries (same pattern as ``useSessionCost``).
 */
export function useProjectCost(
  refreshKey: number,
): { summary: ProjectCostSummary; loading: boolean } {
  const [projectId, setProjectId] = useState<string>(() => getActiveProjectId());
  const [summary, setSummary] = useState<ProjectCostSummary>(() =>
    emptySummary(getActiveProjectId()),
  );
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    return onProjectChange((id) => {
      setProjectId(id);
      setSummary(emptySummary(id));
    });
  }, []);

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;

    const fetchOnce = async (isPoll: boolean) => {
      if (!isPoll) setLoading(true);
      try {
        const r = await apiFetch(
          `/projects/${encodeURIComponent(projectId)}/costs`,
        );
        if (!r.ok) return;
        const data = await r.json();
        if (cancelled || !data || typeof data !== "object") return;
        const next: ProjectCostSummary = { ...emptySummary(projectId), ...data };
        setSummary(next);
        if (next.hasPending && !cancelled) {
          pollTimer = setTimeout(() => fetchOnce(true), 2000);
        }
      } catch {
        // swallow -- next refreshKey bump or project change will retry
      } finally {
        if (!cancelled && !isPoll) setLoading(false);
      }
    };

    fetchOnce(false);

    return () => {
      cancelled = true;
      if (pollTimer) clearTimeout(pollTimer);
    };
  }, [projectId, refreshKey]);

  return { summary, loading };
}

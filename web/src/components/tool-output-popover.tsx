"use client";

import { Loader2Icon, TerminalIcon, XIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_ADK_API_URL ?? "http://localhost:8000";

export interface ToolOutputTarget {
  sessionId: string;
  turnId: string;
  delegationId: string;
  eventIndex: number;
  anchor: DOMRect;
  value?: string;
}

interface StreamJsonEvent {
  type?: string;
  tool_name?: string;
  tool_result?: unknown;
  output?: string;
  content?: string;
  parameters?: Record<string, unknown>;
  [k: string]: unknown;
}

/**
 * Floating popover anchored at a claim span's bounding rect. Fetches one
 * event from the expert's stream-json stdout (via the backend) and renders
 * its tool name + output. Closes on outside click or Escape.
 */
export function ToolOutputPopover({
  target,
  onClose,
}: {
  target: ToolOutputTarget;
  onClose: () => void;
}) {
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const [loading, setLoading] = useState(true);
  const [event, setEvent] = useState<StreamJsonEvent | null>(null);
  const [raw, setRaw] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setEvent(null);
    setRaw(null);

    const url =
      `${API_BASE}/turns/${encodeURIComponent(target.sessionId)}` +
      `/${encodeURIComponent(target.turnId)}/expert/${encodeURIComponent(target.delegationId)}` +
      `/stdout?eventIndex=${target.eventIndex}`;

    fetch(url)
      .then(async (resp) => {
        if (cancelled) return;
        if (resp.status === 404) {
          setError("Tool output not found — the expert's stdout may have been pruned.");
          return;
        }
        if (!resp.ok) {
          setError(`Failed to load (HTTP ${resp.status}).`);
          return;
        }
        const data = await resp.json();
        if (cancelled) return;
        if (data.event) setEvent(data.event as StreamJsonEvent);
        if (typeof data.raw === "string") setRaw(data.raw);
      })
      .catch((exc) => {
        if (cancelled) return;
        setError(exc instanceof Error ? exc.message : "Network error");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [target.sessionId, target.turnId, target.delegationId, target.eventIndex]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    const onClick = (e: MouseEvent) => {
      if (!popoverRef.current) return;
      if (!popoverRef.current.contains(e.target as Node)) onClose();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onClick);
    };
  }, [onClose]);

  const style = useMemo<React.CSSProperties>(() => {
    const gap = 6;
    const preferredWidth = 440;
    const viewportWidth = typeof window !== "undefined" ? window.innerWidth : 1024;
    const left = Math.min(
      Math.max(8, target.anchor.left),
      viewportWidth - preferredWidth - 8
    );
    return {
      position: "fixed",
      top: target.anchor.bottom + gap,
      left,
      width: preferredWidth,
      zIndex: 60,
    };
  }, [target.anchor]);

  const body = renderEventBody(event, raw);

  return (
    <div
      ref={popoverRef}
      style={style}
      className="rounded-lg border border-border bg-popover text-popover-foreground shadow-lg"
    >
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <TerminalIcon className="size-3.5 text-muted-foreground" />
        <span className="text-xs font-medium">
          {event?.tool_name || event?.type || "Tool output"}
        </span>
        <span className="ml-1 rounded bg-muted px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground">
          #{target.eventIndex}
        </span>
        {target.value && (
          <span className="ml-1 rounded bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-mono text-emerald-700 dark:text-emerald-400">
            = {target.value}
          </span>
        )}
        <span className="flex-1" />
        <button
          type="button"
          onClick={onClose}
          className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          title="Close"
        >
          <XIcon className="size-3" />
        </button>
      </div>
      <div className="max-h-[320px] overflow-auto px-3 py-2">
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2Icon className="size-3 animate-spin" />
            Loading event...
          </div>
        ) : error ? (
          <p className="text-xs text-muted-foreground italic">{error}</p>
        ) : (
          body
        )}
      </div>
    </div>
  );
}

function renderEventBody(event: StreamJsonEvent | null, raw: string | null) {
  if (!event && raw) {
    return (
      <pre className="whitespace-pre-wrap break-words font-mono text-[11px] text-muted-foreground">
        {raw}
      </pre>
    );
  }
  if (!event) {
    return (
      <p className="text-xs text-muted-foreground italic">No event body.</p>
    );
  }

  const outputText = extractDisplayText(event);
  const params = event.parameters;

  return (
    <div className="space-y-2">
      {params && Object.keys(params).length > 0 && (
        <details className="text-[11px]">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
            Parameters
          </summary>
          <pre className="mt-1 whitespace-pre-wrap break-words rounded bg-muted/50 p-2 font-mono text-muted-foreground">
            {JSON.stringify(params, null, 2)}
          </pre>
        </details>
      )}
      {outputText ? (
        <pre className="whitespace-pre-wrap break-words font-mono text-[11px] text-foreground">
          {outputText}
        </pre>
      ) : (
        <pre className="whitespace-pre-wrap break-words font-mono text-[11px] text-muted-foreground">
          {JSON.stringify(event, null, 2)}
        </pre>
      )}
    </div>
  );
}

function extractDisplayText(event: StreamJsonEvent): string | null {
  const direct = [event.output, event.content];
  for (const val of direct) {
    if (typeof val === "string" && val.trim()) return val;
  }
  const tr = event.tool_result;
  if (typeof tr === "string" && tr.trim()) return tr;
  if (tr && typeof tr === "object") {
    const maybe = (tr as Record<string, unknown>).output
      ?? (tr as Record<string, unknown>).result
      ?? (tr as Record<string, unknown>).stdout;
    if (typeof maybe === "string" && maybe.trim()) return maybe;
  }
  return null;
}

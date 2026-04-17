"use client";

import { apiFetch } from "@/lib/projects";
import { cn } from "@/lib/utils";
import { diffWords, type Change } from "diff";
import { LoaderCircleIcon, SparklesIcon, XIcon } from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface MarkdownRevisePopoverProps {
  /** Viewport-relative bounding rect of the current text selection. */
  anchorRect: DOMRect;
  /** The exact selected text the user wants revised. */
  selection: string;
  /** Up to ~800 chars of source immediately before the selection (context only). */
  before?: string;
  /** Up to ~800 chars of source immediately after the selection (context only). */
  after?: string;
  /** Optional human-readable path shown in the header. */
  filePath?: string;
  /** Called with the accepted revised text. */
  onAccept: (revised: string) => void;
  /** Called when the user discards or the popover should otherwise close. */
  onClose: () => void;
}

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "review"; revised: string }
  | { kind: "error"; message: string };

const POPOVER_WIDTH = 460;
const POPOVER_MIN_HEIGHT = 200;
const VIEWPORT_MARGIN = 8;
const PILL_GAP = 8;

// ---------------------------------------------------------------------------
// Diff rendering
// ---------------------------------------------------------------------------

function DiffPanes({ original, revised }: { original: string; revised: string }) {
  const changes = useMemo<Change[]>(
    () => diffWords(original, revised),
    [original, revised],
  );

  return (
    <div className="grid grid-cols-2 gap-2">
      <div className="rounded-md border bg-muted/20 p-2 text-xs leading-relaxed whitespace-pre-wrap break-words">
        <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          Original
        </div>
        <div>
          {changes.map((part, i) => {
            if (part.added) return null;
            return (
              <span
                key={i}
                className={cn(
                  part.removed &&
                    "bg-rose-100 text-rose-800 line-through decoration-rose-400/70",
                )}
              >
                {part.value}
              </span>
            );
          })}
        </div>
      </div>
      <div className="rounded-md border bg-muted/20 p-2 text-xs leading-relaxed whitespace-pre-wrap break-words">
        <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          Revised
        </div>
        <div>
          {changes.map((part, i) => {
            if (part.removed) return null;
            return (
              <span
                key={i}
                className={cn(part.added && "bg-emerald-100 text-emerald-800")}
              >
                {part.value}
              </span>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// MarkdownRevisePopover
// ---------------------------------------------------------------------------

export function MarkdownRevisePopover({
  anchorRect,
  selection,
  before,
  after,
  filePath,
  onAccept,
  onClose,
}: MarkdownRevisePopoverProps) {
  const [state, setState] = useState<State>({ kind: "idle" });
  const [instruction, setInstruction] = useState("");
  const [style, setStyle] = useState<React.CSSProperties | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  // Position using the viewport-relative anchor rect; flip above if no room
  // below. Uses `position: fixed` so it stays glued to the selection regardless
  // of ancestor scroll.
  useLayoutEffect(() => {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const measured = containerRef.current?.getBoundingClientRect();
    const height = Math.max(measured?.height ?? 0, POPOVER_MIN_HEIGHT);

    const spaceBelow = vh - anchorRect.bottom - VIEWPORT_MARGIN;
    const spaceAbove = anchorRect.top - VIEWPORT_MARGIN;
    const placeAbove = spaceBelow < height && spaceAbove > spaceBelow;

    const top = placeAbove
      ? Math.max(VIEWPORT_MARGIN, anchorRect.top - height - PILL_GAP)
      : Math.min(
          anchorRect.bottom + PILL_GAP,
          vh - height - VIEWPORT_MARGIN,
        );

    const anchorCenter = anchorRect.left + anchorRect.width / 2;
    const left = Math.max(
      VIEWPORT_MARGIN,
      Math.min(
        anchorCenter - POPOVER_WIDTH / 2,
        vw - POPOVER_WIDTH - VIEWPORT_MARGIN,
      ),
    );

    setStyle({
      position: "fixed",
      top,
      left,
      width: POPOVER_WIDTH,
    });
  }, [anchorRect, state.kind]);

  // Autofocus the instruction input when the popover opens in idle state.
  useEffect(() => {
    if (state.kind === "idle") {
      inputRef.current?.focus();
    }
  }, [state.kind]);

  // Esc closes; click outside closes. We skip close-on-outside-click while
  // loading so a stray click doesn't orphan an in-flight request.
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCloseRef.current();
      }
    };
    const handlePointer = (e: MouseEvent) => {
      const target = e.target as Node | null;
      if (!target || !containerRef.current) return;
      if (containerRef.current.contains(target)) return;
      onCloseRef.current();
    };
    window.addEventListener("keydown", handleKey, true);
    // Use mousedown so we beat the browser's native selection-clear click.
    window.addEventListener("mousedown", handlePointer, true);
    return () => {
      window.removeEventListener("keydown", handleKey, true);
      window.removeEventListener("mousedown", handlePointer, true);
    };
  }, []);

  // Abort any in-flight request on unmount.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const submit = useCallback(async () => {
    const trimmed = instruction.trim();
    if (!trimmed) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ kind: "loading" });
    try {
      const res = await apiFetch("/revise-markdown", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          selection,
          instruction: trimmed,
          before: before ?? "",
          after: after ?? "",
        }),
        signal: controller.signal,
      });
      if (!res.ok) {
        const detail = await res.text().catch(() => "");
        throw new Error(
          detail?.trim() ? `${res.status}: ${detail}` : `HTTP ${res.status}`,
        );
      }
      const data = (await res.json()) as { revised?: string };
      const revised = (data.revised ?? "").trim();
      if (!revised) throw new Error("Model returned empty revision");
      setState({ kind: "review", revised });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      const message = err instanceof Error ? err.message : "Request failed";
      setState({ kind: "error", message });
    }
  }, [instruction, selection, before, after]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        void submit();
      }
    },
    [submit],
  );

  const accept = useCallback(() => {
    if (state.kind !== "review") return;
    onAccept(state.revised);
  }, [state, onAccept]);

  const retryFromReview = useCallback(() => {
    setState({ kind: "idle" });
    setTimeout(() => inputRef.current?.focus(), 0);
  }, []);

  return (
    <div
      ref={containerRef}
      role="dialog"
      aria-label="Ask Kady to revise"
      style={style ?? { position: "fixed", visibility: "hidden" }}
      className={cn(
        "z-50 flex flex-col gap-2 rounded-lg border bg-background p-3 shadow-xl",
        "text-foreground",
      )}
      onMouseDown={(e) => e.stopPropagation()}
    >
      {/* Header */}
      <div className="flex items-center gap-2">
        <SparklesIcon className="size-3.5 text-violet-500" />
        <span className="text-xs font-semibold">Ask Kady to revise</span>
        {filePath && (
          <span
            className="ml-1 max-w-[200px] truncate font-mono text-[10px] text-muted-foreground"
            title={filePath}
          >
            {filePath.split("/").pop()}
          </span>
        )}
        <button
          type="button"
          onClick={onClose}
          className="ml-auto rounded p-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          title="Close (Esc)"
        >
          <XIcon className="size-3.5" />
        </button>
      </div>

      {/* Original selection preview */}
      <div className="max-h-20 overflow-auto rounded border bg-muted/20 px-2 py-1.5 text-[11px] text-muted-foreground whitespace-pre-wrap break-words">
        <span className="font-mono">{selection}</span>
      </div>

      {/* Body: varies by state */}
      {state.kind === "idle" && (
        <>
          <textarea
            ref={inputRef}
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="e.g. Tighten this, make it more concrete, fix the markdown formatting…"
            rows={2}
            className="resize-none rounded-md border bg-background px-2 py-1.5 text-xs outline-none ring-0 transition-colors focus:border-primary"
          />
          <div className="flex items-center justify-end gap-2">
            <span className="mr-auto text-[10px] text-muted-foreground/60">
              ⌘↵ to submit · Esc to close
            </span>
            <button
              type="button"
              onClick={onClose}
              className="rounded-md px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void submit()}
              disabled={!instruction.trim()}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1 text-xs text-primary-foreground transition-opacity disabled:opacity-40"
            >
              <SparklesIcon className="size-3" />
              Revise
            </button>
          </div>
        </>
      )}

      {state.kind === "loading" && (
        <div className="flex items-center gap-2 rounded-md border bg-muted/10 px-2 py-3 text-xs text-muted-foreground">
          <LoaderCircleIcon className="size-3.5 animate-spin" />
          Kady is revising…
          <button
            type="button"
            onClick={() => {
              abortRef.current?.abort();
              setState({ kind: "idle" });
            }}
            className="ml-auto rounded px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            Cancel
          </button>
        </div>
      )}

      {state.kind === "review" && (
        <>
          <DiffPanes original={selection} revised={state.revised} />
          <div className="flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={retryFromReview}
              className="mr-auto rounded-md px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              Revise again
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-md px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              Discard
            </button>
            <button
              type="button"
              onClick={accept}
              className="rounded-md bg-emerald-600 px-3 py-1 text-xs text-white transition-opacity hover:bg-emerald-700"
            >
              Accept
            </button>
          </div>
        </>
      )}

      {state.kind === "error" && (
        <>
          <div className="rounded-md border border-rose-200 bg-rose-50 px-2 py-1.5 text-xs text-rose-700">
            {state.message}
          </div>
          <div className="flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              Close
            </button>
            <button
              type="button"
              onClick={() => setState({ kind: "idle" })}
              className="rounded-md bg-primary px-3 py-1 text-xs text-primary-foreground"
            >
              Try again
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// RevisePill — floating "Ask Kady to revise" trigger anchored to a selection
// ---------------------------------------------------------------------------

export interface RevisePillProps {
  anchorRect: DOMRect;
  onClick: () => void;
}

export function RevisePill({ anchorRect, onClick }: RevisePillProps) {
  // Place just below the selection's right edge, clamped to the viewport.
  const style: React.CSSProperties = useMemo(() => {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const width = 150;
    const top = Math.min(
      anchorRect.bottom + 6,
      vh - 32 - VIEWPORT_MARGIN,
    );
    const left = Math.max(
      VIEWPORT_MARGIN,
      Math.min(anchorRect.right - width, vw - width - VIEWPORT_MARGIN),
    );
    return { position: "fixed", top, left };
  }, [anchorRect]);

  return (
    <button
      type="button"
      style={style}
      onMouseDown={(e) => {
        // Prevent the pill's click from collapsing the current text selection
        // before our onClick fires.
        e.preventDefault();
      }}
      onClick={onClick}
      className={cn(
        "z-40 inline-flex items-center gap-1.5 rounded-full border bg-background px-2.5 py-1",
        "text-[11px] font-medium text-foreground shadow-md",
        "transition-colors hover:bg-muted",
      )}
    >
      <SparklesIcon className="size-3 text-violet-500" />
      Ask Kady to revise
    </button>
  );
}

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { Kbd } from "@/components/ui/Kbd";
import { api } from "@/lib/api";
import { cx } from "@/lib/cx";
import type { Workflow } from "@/lib/types";

/**
 * The ⌘K command palette — the primary interface (UI_SYSTEM.md §1.3, §6, the
 * Raycast lineage). Everything funnels through here: navigation, creation, and
 * jumping straight to any workflow. Keyboard-first by design — it both feels
 * premium and self-selects the technical users the wedge targets.
 *
 * Interaction contract, scoped so it never fights the builder canvas:
 *   * ⌘K / Ctrl+K toggles open (global, but only that chord).
 *   * Arrows move the selection, Enter runs it, Esc closes — but ONLY while the
 *     palette is open, so the builder keeps Backspace/Delete for node deletion
 *     and free typing in its config editor.
 */

interface Command {
  id: string;
  label: string;
  hint?: string;
  group: string;
  run: () => void;
}

export function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    setActive(0);
  }, []);

  // Global open chord. Capture phase so it works even from inside inputs/canvas.
  // Also opens from a custom event so a clickable nav affordance can trigger it.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    }
    function onOpenEvent() {
      setOpen(true);
    }
    window.addEventListener("keydown", onKey);
    window.addEventListener("axiom:open-command-palette", onOpenEvent);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("axiom:open-command-palette", onOpenEvent);
    };
  }, []);

  // On open: focus the field and refresh the workflow list for "jump to".
  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    let cancelled = false;
    api
      .listWorkflows()
      .then((ws) => {
        if (!cancelled) setWorkflows(ws);
      })
      .catch(() => {
        /* palette still works for static actions if the API is down */
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const commands = useMemo<Command[]>(() => {
    const actions: Command[] = [
      {
        id: "new",
        label: "Create new workflow",
        hint: "Builder",
        group: "Actions",
        run: () => router.push("/workflows/new"),
      },
      {
        id: "home",
        label: "Go to workflows",
        hint: "Home",
        group: "Actions",
        run: () => router.push("/"),
      },
    ];
    const jumps: Command[] = workflows.map((w) => ({
      id: `wf-${w.id}`,
      label: w.name,
      hint: "Workflow",
      group: "Jump to",
      run: () => router.push(`/workflows/${w.id}`),
    }));
    return [...actions, ...jumps];
  }, [workflows, router]);

  // Substring fuzzy match — every query char appears in order in the label.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((c) => subsequence(q, c.label.toLowerCase()));
  }, [commands, query]);

  // Clamp the selection to the current filtered set during render. Deriving it
  // here (rather than syncing `active` in an effect) avoids a setState-in-effect
  // cascade when the query narrows the list.
  const activeIndex = filtered.length === 0 ? 0 : Math.min(active, filtered.length - 1);

  const runActive = useCallback(() => {
    const cmd = filtered[activeIndex];
    if (cmd) {
      close();
      cmd.run();
    }
  }, [filtered, activeIndex, close]);

  function onInputKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      runActive();
    } else if (e.key === "Escape") {
      e.preventDefault();
      close();
    }
  }

  if (!open) return null;

  // Group the filtered commands in display order.
  const groups: { name: string; items: Command[] }[] = [];
  for (const cmd of filtered) {
    const g = groups.find((x) => x.name === cmd.group) ?? addGroup(groups, cmd.group);
    g.items.push(cmd);
  }
  let flatIndex = -1;

  return (
    <div
      className="fixed inset-0 z-[100] flex items-start justify-center bg-ink/20 px-4 pt-[12vh] [animation:var(--animate-overlay-in)] backdrop-blur-[2px]"
      onMouseDown={close}
      role="presentation"
    >
      <div
        className="w-full max-w-xl overflow-hidden rounded-xl border border-hairline bg-canvas shadow-[var(--shadow-e5)] [animation:var(--animate-pop-in)]"
        onMouseDown={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
      >
        <div className="flex items-center gap-3 border-b border-hairline px-4">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <circle cx="7" cy="7" r="5" stroke="#888888" strokeWidth="1.5" />
            <path d="M11 11l3 3" stroke="#888888" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onInputKey}
            placeholder="Search workflows and actions…"
            className="h-12 w-full bg-transparent text-[15px] text-ink outline-none placeholder:text-mute"
            aria-label="Search"
          />
          <Kbd keys={["Esc"]} />
        </div>

        <div ref={listRef} className="max-h-[52vh] overflow-y-auto p-2">
          {filtered.length === 0 ? (
            <p className="px-3 py-8 text-center text-sm text-mute">No matches.</p>
          ) : (
            groups.map((group) => (
              <div key={group.name} className="mb-1">
                <p className="eyebrow px-3 py-1.5">{group.name}</p>
                {group.items.map((cmd) => {
                  flatIndex += 1;
                  const isActive = flatIndex === activeIndex;
                  const idx = flatIndex;
                  return (
                    <button
                      key={cmd.id}
                      type="button"
                      onMouseEnter={() => setActive(idx)}
                      onClick={runActive}
                      className={cx(
                        "flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm",
                        isActive ? "bg-canvas-soft-2 text-ink" : "text-body",
                      )}
                    >
                      <span className="truncate">{cmd.label}</span>
                      {cmd.hint && (
                        <span className="ml-3 shrink-0 font-mono text-[11px] text-mute">
                          {cmd.hint}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>

        <div className="flex items-center justify-between border-t border-hairline px-4 py-2 text-[11px] text-mute">
          <span className="flex items-center gap-1.5">
            <Kbd keys={["↑", "↓"]} /> navigate
          </span>
          <span className="flex items-center gap-1.5">
            <Kbd keys={["↵"]} /> open
          </span>
        </div>
      </div>
    </div>
  );
}

function addGroup(groups: { name: string; items: Command[] }[], name: string) {
  const g = { name, items: [] as Command[] };
  groups.push(g);
  return g;
}

/** True if `needle` is a subsequence of `haystack` (loose fuzzy match). */
function subsequence(needle: string, haystack: string): boolean {
  let i = 0;
  for (const ch of haystack) {
    if (ch === needle[i]) i += 1;
    if (i === needle.length) return true;
  }
  return needle.length === 0;
}

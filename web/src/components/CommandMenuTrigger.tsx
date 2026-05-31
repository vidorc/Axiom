"use client";

import { Kbd } from "@/components/ui/Kbd";

/**
 * The nav affordance for the command palette — a discoverable button that shows
 * the ⌘K shortcut so users learn the keyboard path. Dispatches the same event
 * the global chord does, so there's one source of truth for "open the palette".
 */
export function CommandMenuTrigger() {
  return (
    <button
      type="button"
      onClick={() => window.dispatchEvent(new Event("axiom:open-command-palette"))}
      className="flex h-8 items-center gap-2 rounded-md border border-hairline bg-canvas px-2.5 text-sm text-mute transition-colors hover:border-hairline-strong hover:text-body"
      aria-label="Open command palette"
    >
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.5" />
        <path d="M11 11l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
      <span className="hidden sm:inline">Search</span>
      <Kbd keys={["⌘", "K"]} />
    </button>
  );
}

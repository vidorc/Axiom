import { cx } from "@/lib/cx";

/**
 * A keycap, for shortcut hints (the ⌘K affordance, palette footer). Renders the
 * platform-appropriate glyphs; styling lives in the `.kbd` utility (globals.css).
 */
export function Kbd({ keys, className }: { keys: string[]; className?: string }) {
  return (
    <span className={cx("inline-flex items-center gap-1", className)}>
      {keys.map((k) => (
        <kbd key={k} className="kbd">
          {k}
        </kbd>
      ))}
    </span>
  );
}

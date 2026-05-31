import type { HTMLAttributes } from "react";

import { cx } from "@/lib/cx";

/**
 * A surface card on the DESIGN.md elevation ladder. `interactive` adds the
 * hover lift used for clickable list items (workflow cards, run rows).
 */
interface CardProps extends HTMLAttributes<HTMLDivElement> {
  interactive?: boolean;
  elevation?: "e2" | "e3" | "e4";
}

const ELEVATION = {
  e2: "shadow-[var(--shadow-e2)]",
  e3: "shadow-[var(--shadow-e3)]",
  e4: "shadow-[var(--shadow-e4)]",
} as const;

export function Card({
  interactive = false,
  elevation = "e2",
  className,
  ...props
}: CardProps) {
  return (
    <div
      className={cx(
        "rounded-lg border border-hairline bg-canvas",
        ELEVATION[elevation],
        interactive &&
          "transition-[box-shadow,transform,border-color] duration-150 " +
            "hover:-translate-y-0.5 hover:shadow-[var(--shadow-e4)] hover:border-hairline-strong",
        className,
      )}
      {...props}
    />
  );
}

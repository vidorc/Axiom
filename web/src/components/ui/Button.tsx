import type { ButtonHTMLAttributes } from "react";

import { cx } from "@/lib/cx";

/**
 * The brand's two button scales coexist deliberately (DESIGN.md Do's): the
 * 100px marketing pill for primary actions, and a tight square scale for nav.
 * We use the pill scale in-product for primary/secondary CTAs and a ghost
 * variant for low-emphasis actions.
 */
export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "md" | "sm";

const BASE =
  "inline-flex items-center justify-center gap-2 rounded-pill font-medium whitespace-nowrap " +
  "transition-[opacity,background-color,border-color,transform] duration-150 " +
  "active:translate-y-px disabled:pointer-events-none disabled:opacity-50";

const SIZES: Record<ButtonSize, string> = {
  md: "h-10 px-5 text-sm",
  sm: "h-8 px-4 text-[13px]",
};

const VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-ink text-on-ink hover:opacity-90",
  secondary: "border border-hairline bg-canvas text-ink hover:border-hairline-strong",
  ghost: "text-body hover:bg-canvas-soft-2 hover:text-ink",
  danger: "border border-error/40 text-error hover:bg-error-soft/40",
};

/** Class string for the button look — exported so `<Link>` can wear it too. */
export function buttonClasses(
  variant: ButtonVariant = "primary",
  size: ButtonSize = "md",
  extra?: string,
): string {
  return cx(BASE, SIZES[size], VARIANTS[variant], extra);
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export function Button({ variant, size, className, ...props }: ButtonProps) {
  return <button className={buttonClasses(variant, size, className)} {...props} />;
}

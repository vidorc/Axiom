/** Join class names, dropping falsy values. The minimal `clsx` we actually need. */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

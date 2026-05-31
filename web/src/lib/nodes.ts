/**
 * The palette of built-in nodes the builder offers.
 *
 * Mirrors the registry assembled by `build_default_registry()` in
 * `src/axiom/domains/node_runtime/builtin/`. Each entry describes how the node
 * appears in the palette and a sensible default config so a freshly-dropped node
 * is valid without further editing. The `node_ref` strings are the contract the
 * engine resolves against — they must match the backend exactly.
 */

export interface NodeKind {
  nodeRef: string;
  label: string;
  description: string;
  /** A default config object for a newly-added node of this kind. */
  defaultConfig: Record<string, unknown>;
}

export const NODE_KINDS: NodeKind[] = [
  {
    nodeRef: "axiom/echo",
    label: "Echo",
    description: "Passes its input through unchanged. The simplest node.",
    defaultConfig: {},
  },
  {
    nodeRef: "axiom/set-fields",
    label: "Set fields",
    description: "Merges static fields onto the input. Shapes data downstream.",
    defaultConfig: { fields: { key: "value" } },
  },
  {
    nodeRef: "axiom/delay",
    label: "Delay",
    description: "Waits N seconds, then continues. Useful for pacing.",
    defaultConfig: { seconds: 1 },
  },
  {
    nodeRef: "axiom/http-request",
    label: "HTTP request",
    description: "Calls an HTTP endpoint and returns status, headers, body.",
    defaultConfig: { method: "GET", url: "https://example.com", timeout_seconds: 30 },
  },
];

const BY_REF = new Map(NODE_KINDS.map((k) => [k.nodeRef, k]));

export function nodeKind(nodeRef: string): NodeKind | undefined {
  return BY_REF.get(nodeRef);
}

/** A short display label for a node_ref ("axiom/set-fields" → "Set fields"). */
export function nodeKindLabel(nodeRef: string): string {
  return BY_REF.get(nodeRef)?.label ?? nodeRef;
}

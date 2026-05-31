"use client";

import { memo } from "react";
import { Handle, type NodeProps, Position } from "@xyflow/react";

import { nodeKindLabel } from "@/lib/nodes";

/**
 * The canvas node, shared by the read-only viewer and the editable builder.
 *
 * A custom node (rather than React Flow's default) for two reasons: the default
 * collapses our two-line label to one, and we want real hierarchy — the
 * graph-local id in ink, the node kind in muted mono beneath it (the
 * "deployment-dashboard" voice, UI_SYSTEM.md §2). The left/right `Handle`s are
 * load-bearing: they are the connection points the builder drags between, so a
 * custom node MUST declare them or edges can't be drawn.
 */
function WorkflowNodeImpl({ id, data, selected }: NodeProps) {
  const nodeRef = (data as { nodeRef: string }).nodeRef;
  return (
    <div
      className="w-[180px] rounded-md border bg-canvas px-3 py-2 shadow-e3 transition"
      style={{ borderColor: selected ? "#171717" : "#ebebeb" }}
    >
      <Handle type="target" position={Position.Left} style={HANDLE} />
      <div className="truncate text-sm font-medium text-ink">{id}</div>
      <div className="truncate font-mono text-[11px] text-mute">{nodeKindLabel(nodeRef)}</div>
      <Handle type="source" position={Position.Right} style={HANDLE} />
    </div>
  );
}

const HANDLE = { width: 8, height: 8, background: "#a1a1a1", border: "none" } as const;

export const WorkflowNode = memo(WorkflowNodeImpl);

/**
 * Stable `nodeTypes` map. Defined at module scope (not inside a component) so its
 * identity never changes between renders — React Flow warns and re-mounts nodes
 * otherwise.
 */
export const NODE_TYPES = { workflow: WorkflowNode } as const;

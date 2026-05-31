"use client";

import { useMemo } from "react";
import {
  Background,
  Controls,
  type Edge,
  type Node,
  ReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { NODE_TYPES } from "@/components/WorkflowNode";
import type { WorkflowGraph } from "@/lib/types";

/**
 * A read-only render of a workflow version's DAG.
 *
 * Phase-1 scope is *legibility of structure*, not editing (UI_SYSTEM.md §1.1 —
 * "a beautiful canvas with shallow nodes loses to a plain canvas with deep
 * ones"). So this lays the graph out with a simple longest-path layering — roots
 * on the left, each node one column right of its deepest predecessor — which is
 * enough to read a linear or fan-in/out flow at a glance. The editable builder
 * (drag, connect, autosave) is the next milestone.
 */
export function GraphView({ graph }: { graph: WorkflowGraph }) {
  const { nodes, edges } = useMemo(() => layout(graph), [graph]);

  return (
    <div className="h-[420px] w-full overflow-hidden rounded-lg border border-hairline bg-canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        edgesFocusable={false}
      >
        <Background color="#ebebeb" gap={20} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

const COL_WIDTH = 220;
const ROW_HEIGHT = 90;

function layout(graph: WorkflowGraph): { nodes: Node[]; edges: Edge[] } {
  // Longest-path depth per node → column. Acyclic by construction (the backend
  // validates the DAG at save time), so a memoised DFS terminates.
  const succ = new Map<string, string[]>();
  const preds = new Map<string, string[]>();
  for (const n of graph.nodes) {
    succ.set(n.id, []);
    preds.set(n.id, []);
  }
  for (const e of graph.edges) {
    succ.get(e.from)?.push(e.to);
    preds.get(e.to)?.push(e.from);
  }

  const depthCache = new Map<string, number>();
  const depthOf = (id: string, seen: Set<string>): number => {
    const cached = depthCache.get(id);
    if (cached !== undefined) return cached;
    if (seen.has(id)) return 0; // defensive: cycle guard (shouldn't happen)
    seen.add(id);
    const parents = preds.get(id) ?? [];
    const d = parents.length === 0 ? 0 : 1 + Math.max(...parents.map((p) => depthOf(p, seen)));
    depthCache.set(id, d);
    return d;
  };

  const rowInCol = new Map<number, number>();
  const nodes: Node[] = graph.nodes.map((n) => {
    const col = depthOf(n.id, new Set());
    const row = rowInCol.get(col) ?? 0;
    rowInCol.set(col, row + 1);
    return {
      id: n.id,
      type: "workflow",
      position: { x: col * COL_WIDTH, y: row * ROW_HEIGHT },
      data: { nodeRef: n.node_ref },
    };
  });

  const edges: Edge[] = graph.edges.map((e, i) => ({
    id: `e${i}`,
    source: e.from,
    target: e.to,
    animated: false,
    style: { stroke: "#a1a1a1", strokeWidth: 1.5 },
  }));

  return { nodes, edges };
}

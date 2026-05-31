"use client";

import { useCallback, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  addEdge,
  Background,
  type Connection,
  Controls,
  type Edge,
  type Node,
  type NodeMouseHandler,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { NODE_TYPES } from "@/components/WorkflowNode";
import { api, ApiError } from "@/lib/api";
import { NODE_KINDS } from "@/lib/nodes";
import type { WorkflowGraph } from "@/lib/types";

/**
 * The editable workflow builder.
 *
 * Add nodes from the palette, drag to connect, select a node to edit its config,
 * delete with the keyboard, then save — which serialises the canvas to the graph
 * JSON shape and persists it through the API (a new workflow, or a new immutable
 * version of an existing one). Graph validation stays server-side: an invalid DAG
 * comes back as a 422 with the full problem list, surfaced inline.
 *
 * Scope note (UI_SYSTEM.md §1.1): this is a *functional* builder, not a polished
 * canvas — legibility and a correct save round-trip first, pan/zoom flourish later.
 */

type BuilderNodeData = {
  nodeRef: string;
  config: Record<string, unknown>;
};

export interface BuilderProps {
  /** When set, save appends a version to this workflow; else it creates one. */
  workflowId?: string;
  initialName?: string;
  initialGraph?: WorkflowGraph;
}

function toFlow(graph: WorkflowGraph): { nodes: Node<BuilderNodeData>[]; edges: Edge[] } {
  const nodes: Node<BuilderNodeData>[] = graph.nodes.map((n, i) => ({
    id: n.id,
    type: "workflow",
    position: { x: (i % 3) * 220, y: Math.floor(i / 3) * 120 },
    data: {
      nodeRef: n.node_ref,
      config: (n.config as Record<string, unknown>) ?? {},
    },
  }));
  const edges: Edge[] = graph.edges.map((e, i) => ({
    id: `e${i}`,
    source: e.from,
    target: e.to,
    style: { stroke: "#a1a1a1", strokeWidth: 1.5 },
  }));
  return { nodes, edges };
}

function BuilderInner({ workflowId, initialName, initialGraph }: BuilderProps) {
  const router = useRouter();
  const seed = useMemo(
    () => (initialGraph ? toFlow(initialGraph) : { nodes: [], edges: [] }),
    [initialGraph],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState<Node<BuilderNodeData>>(seed.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(seed.edges);
  const [name, setName] = useState(initialName ?? "");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [counter, setCounter] = useState(seed.nodes.length + 1);

  const [saving, setSaving] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const onConnect = useCallback(
    (c: Connection) => setEdges((eds) => addEdge({ ...c, style: { stroke: "#a1a1a1", strokeWidth: 1.5 } }, eds)),
    [setEdges],
  );

  const onNodeClick: NodeMouseHandler = useCallback((_e, node) => setSelectedId(node.id), []);

  const addNode = useCallback(
    (nodeRef: string, defaultConfig: Record<string, unknown>) => {
      const id = `n${counter}`;
      setCounter((c) => c + 1);
      setNodes((nds) => [
        ...nds,
        {
          id,
          type: "workflow",
          position: { x: 80 + (nds.length % 4) * 60, y: 80 + (nds.length % 6) * 50 },
          data: { nodeRef, config: { ...defaultConfig } },
        },
      ]);
      setSelectedId(id);
    },
    [counter, setNodes],
  );

  const selected = nodes.find((n) => n.id === selectedId) ?? null;

  const updateSelectedConfig = useCallback(
    (raw: string) => {
      if (!selectedId) return;
      let parsed: Record<string, unknown>;
      try {
        parsed = JSON.parse(raw);
      } catch {
        return; // ignore until valid JSON; the field shows the raw text via local state
      }
      setNodes((nds) =>
        nds.map((n) =>
          n.id === selectedId ? { ...n, data: { ...n.data, config: parsed } } : n,
        ),
      );
    },
    [selectedId, setNodes],
  );

  const deleteSelected = useCallback(() => {
    if (!selectedId) return;
    setNodes((nds) => nds.filter((n) => n.id !== selectedId));
    setEdges((eds) => eds.filter((e) => e.source !== selectedId && e.target !== selectedId));
    setSelectedId(null);
  }, [selectedId, setNodes, setEdges]);

  function serialize(): WorkflowGraph {
    return {
      nodes: nodes.map((n) => ({
        id: n.id,
        node_ref: n.data.nodeRef,
        config: n.data.config,
      })),
      edges: edges.map((e) => ({ from: e.source, to: e.target })),
    };
  }

  async function save() {
    setSaving(true);
    setProblems([]);
    setError(null);
    const graph = serialize();
    try {
      if (workflowId) {
        await api.createVersion(workflowId, { graph });
        router.push(`/workflows/${workflowId}`);
      } else {
        const { workflow_id } = await api.createWorkflow({ name: name.trim() || "Untitled workflow", graph });
        router.push(`/workflows/${workflow_id}`);
      }
      router.refresh();
    } catch (e) {
      if (e instanceof ApiError && e.status === 422 && isProblemDetail(e.detail)) {
        setProblems(e.detail.problems);
      } else {
        setError(e instanceof ApiError ? e.message : "failed to save");
      }
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Workflow name"
          disabled={Boolean(workflowId)}
          className="h-10 w-64 rounded-md border border-hairline bg-canvas px-3 text-sm outline-none focus:border-hairline-strong disabled:bg-canvas-soft-2 disabled:text-mute"
        />
        <div className="ml-auto flex items-center gap-3">
          {selected && (
            <button
              type="button"
              onClick={deleteSelected}
              className="inline-flex h-10 items-center rounded-full border border-error/40 px-4 text-sm text-error transition hover:bg-error-soft/40"
            >
              Delete node
            </button>
          )}
          <button
            type="button"
            onClick={save}
            disabled={saving || nodes.length === 0}
            className="inline-flex h-10 items-center rounded-full bg-ink px-5 text-sm font-medium text-on-ink transition hover:opacity-90 disabled:opacity-50"
          >
            {saving ? "Saving…" : workflowId ? "Save new version" : "Create workflow"}
          </button>
        </div>
      </div>

      {/* Validation feedback from the server. */}
      {problems.length > 0 && (
        <div className="rounded-lg border border-error/30 bg-error-soft/40 p-4">
          <p className="text-sm font-medium text-error-deep">This graph won&apos;t run:</p>
          <ul className="mt-1 list-disc pl-5 font-mono text-xs text-error">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}
      {error && <p className="text-sm text-error">{error}</p>}

      <div className="grid gap-4 lg:grid-cols-[1fr_280px]">
        {/* Canvas */}
        <div className="h-[520px] overflow-hidden rounded-lg border border-hairline bg-canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={onNodeClick}
            onPaneClick={() => setSelectedId(null)}
            fitView
            deleteKeyCode={["Backspace", "Delete"]}
            proOptions={{ hideAttribution: true }}
          >
            <Background color="#ebebeb" gap={20} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>

        {/* Right rail: palette + inspector */}
        <aside className="space-y-6">
          <section>
            <h3 className="mb-2 font-mono text-xs uppercase tracking-wide text-mute">Add node</h3>
            <div className="space-y-2">
              {NODE_KINDS.map((k) => (
                <button
                  key={k.nodeRef}
                  type="button"
                  onClick={() => addNode(k.nodeRef, k.defaultConfig)}
                  className="block w-full rounded-md border border-hairline bg-canvas p-3 text-left transition hover:border-hairline-strong hover:bg-canvas-soft"
                >
                  <span className="text-sm font-medium">{k.label}</span>
                  <span className="mt-0.5 block font-mono text-[11px] text-mute">{k.nodeRef}</span>
                </button>
              ))}
            </div>
          </section>

          <section>
            <h3 className="mb-2 font-mono text-xs uppercase tracking-wide text-mute">Inspector</h3>
            {selected ? (
              <NodeInspector
                key={selected.id}
                nodeId={selected.id}
                nodeRef={selected.data.nodeRef}
                config={selected.data.config}
                onConfigChange={updateSelectedConfig}
              />
            ) : (
              <p className="rounded-md border border-dashed border-hairline p-3 text-xs text-mute">
                Select a node to edit its config, or drag from one node&apos;s right edge to
                another&apos;s left to connect them.
              </p>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}

function NodeInspector({
  nodeId,
  nodeRef,
  config,
  onConfigChange,
}: {
  nodeId: string;
  nodeRef: string;
  config: Record<string, unknown>;
  onConfigChange: (raw: string) => void;
}) {
  const [text, setText] = useState(() => JSON.stringify(config, null, 2));
  const [invalid, setInvalid] = useState(false);

  return (
    <div className="space-y-2 rounded-md border border-hairline bg-canvas p-3">
      <div className="font-mono text-xs">
        <span className="text-ink">{nodeId}</span>
        <span className="ml-2 text-mute">{nodeRef}</span>
      </div>
      <label className="block font-mono text-[11px] uppercase tracking-wide text-mute">
        config (JSON)
      </label>
      <textarea
        value={text}
        spellCheck={false}
        onChange={(e) => {
          setText(e.target.value);
          try {
            JSON.parse(e.target.value);
            setInvalid(false);
            onConfigChange(e.target.value);
          } catch {
            setInvalid(true);
          }
        }}
        rows={8}
        className={`w-full rounded-md border bg-canvas-soft p-2 font-mono text-xs outline-none ${
          invalid ? "border-error" : "border-hairline focus:border-hairline-strong"
        }`}
      />
      {invalid && <p className="text-[11px] text-error">Invalid JSON — not applied.</p>}
    </div>
  );
}

function isProblemDetail(d: unknown): d is { problems: string[] } {
  return (
    typeof d === "object" &&
    d !== null &&
    Array.isArray((d as { problems?: unknown }).problems)
  );
}

export function WorkflowBuilder(props: BuilderProps) {
  return (
    <ReactFlowProvider>
      <BuilderInner {...props} />
    </ReactFlowProvider>
  );
}

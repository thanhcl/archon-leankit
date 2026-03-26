import { useId, useMemo, useState } from "react";
import { Card } from "../../../ui/primitives";
import { cn } from "../../../ui/primitives/styles";
import type { ImplementationDependency, ImplementationItem, PlanItemStatus } from "../types";

interface DependencyDAGProps {
  items: ImplementationItem[];
  dependencies: ImplementationDependency[];
  isLoading?: boolean;
}

const STATUS_COLORS: Record<PlanItemStatus, string> = {
  planned: "#6b7280",
  ready: "#3b82f6",
  in_progress: "#f59e0b",
  blocked: "#ef4444",
  review: "#8b5cf6",
  done: "#22c55e",
  deferred: "#9ca3af",
  cancelled: "#d1d5db",
};

const STATUS_LABELS: Record<PlanItemStatus, string> = {
  planned: "Planned",
  ready: "Ready",
  in_progress: "In Progress",
  blocked: "Blocked",
  review: "Review",
  done: "Done",
  deferred: "Deferred",
  cancelled: "Cancelled",
};

const NODE_WIDTH = 140;
const NODE_HEIGHT = 48;
const H_GAP = 60;
const V_GAP = 30;

interface LayoutNode {
  item: ImplementationItem;
  x: number;
  y: number;
  col: number;
  row: number;
  isCritical: boolean;
}

function buildLayout(
  items: ImplementationItem[],
  dependencies: ImplementationDependency[],
): { nodes: LayoutNode[]; width: number; height: number; criticalPath: Set<string> } {
  if (items.length === 0) return { nodes: [], width: 0, height: 0, criticalPath: new Set() };

  // Build adjacency: dependent_id depends on dependency_id
  // Edge: dependency_id → dependent_id (dependency must come before dependent)
  const inDegree = new Map<string, number>();
  const outEdges = new Map<string, string[]>(); // dependency_id → [dependent_ids]

  for (const item of items) {
    inDegree.set(item.id, 0);
    outEdges.set(item.id, []);
  }

  for (const dep of dependencies) {
    if (inDegree.has(dep.dependent_id) && outEdges.has(dep.dependency_id)) {
      inDegree.set(dep.dependent_id, (inDegree.get(dep.dependent_id) ?? 0) + 1);
      outEdges.get(dep.dependency_id)?.push(dep.dependent_id);
    }
  }

  // Topological sort (Kahn's algorithm)
  const levels = new Map<string, number>();
  const queue: string[] = [];
  const remaining = new Map(inDegree);

  for (const [id, deg] of remaining) {
    if (deg === 0) queue.push(id);
  }

  while (queue.length > 0) {
    const id = queue.shift();
    if (!id) break;
    const level = levels.get(id) ?? 0;
    for (const next of outEdges.get(id) ?? []) {
      levels.set(next, Math.max(levels.get(next) ?? 0, level + 1));
      remaining.set(next, (remaining.get(next) ?? 1) - 1);
      if (remaining.get(next) === 0) queue.push(next);
    }
  }

  // Assign nodes to levels — items without explicit level get level 0
  const levelMap = new Map<number, ImplementationItem[]>();
  for (const item of items) {
    const col = levels.get(item.id) ?? 0;
    if (!levelMap.has(col)) levelMap.set(col, []);
    levelMap.get(col)?.push(item);
  }

  // Compute critical path: longest incomplete chain from any node
  // Use reverse-topo order to compute longest paths to sinks
  const itemById = new Map(items.map((i) => [i.id, i]));
  const incompleteStatuses = new Set<string>(["planned", "ready", "in_progress", "blocked", "review"]);
  const pathLen = new Map<string, number>(); // longest path from this node to a sink

  // Process in reverse topological order
  const sortedIds = [...items].sort((a, b) => (levels.get(b.id) ?? 0) - (levels.get(a.id) ?? 0)).map((i) => i.id);

  for (const id of sortedIds) {
    const item = itemById.get(id);
    if (!item || !incompleteStatuses.has(item.status)) {
      pathLen.set(id, 0);
      continue;
    }
    const nexts = outEdges.get(id) ?? [];
    const maxNext = nexts.reduce((max, nid) => Math.max(max, pathLen.get(nid) ?? 0), 0);
    pathLen.set(id, 1 + maxNext);
  }

  // Find the critical path start (node with max pathLen)
  let maxLen = 0;
  let startId = "";
  for (const [id, len] of pathLen) {
    if (len > maxLen) {
      maxLen = len;
      startId = id;
    }
  }

  // Trace the critical path greedily
  const criticalPath = new Set<string>();
  if (startId) {
    let cur = startId;
    while (cur) {
      criticalPath.add(cur);
      const nexts = outEdges.get(cur) ?? [];
      const best = nexts.reduce<string | null>((best, nid) => {
        if (!best) return nid;
        return (pathLen.get(nid) ?? 0) > (pathLen.get(best) ?? 0) ? nid : best;
      }, null);
      cur = best ?? "";
    }
  }

  // Build layout positions
  const maxCol = Math.max(...[...levelMap.keys()], 0);
  const nodes: LayoutNode[] = [];

  for (let col = 0; col <= maxCol; col++) {
    const colItems = levelMap.get(col) ?? [];
    for (let row = 0; row < colItems.length; row++) {
      const item = colItems[row];
      nodes.push({
        item,
        x: col * (NODE_WIDTH + H_GAP),
        y: row * (NODE_HEIGHT + V_GAP),
        col,
        row,
        isCritical: criticalPath.has(item.id),
      });
    }
  }

  const maxRows = Math.max(...[...levelMap.values()].map((v) => v.length), 1);
  const width = (maxCol + 1) * (NODE_WIDTH + H_GAP);
  const height = maxRows * (NODE_HEIGHT + V_GAP);

  return { nodes, width, height, criticalPath };
}

export function DependencyDAG({ items, dependencies, isLoading }: DependencyDAGProps) {
  const [tooltip, setTooltip] = useState<{ item: ImplementationItem; x: number; y: number } | null>(null);
  const uid = useId();
  const markerId = (name: string) => `${uid}-${name}`;

  const { nodes, width, height } = useMemo(() => buildLayout(items, dependencies), [items, dependencies]);

  const nodeMap = useMemo(() => new Map(nodes.map((n) => [n.item.id, n])), [nodes]);

  const PADDING = 20;
  const svgWidth = width + PADDING * 2;
  const svgHeight = height + PADDING * 2;

  if (isLoading) {
    return (
      <Card blur="md" transparency="light" size="sm" className="border-white/10 dark:border-white/[0.06]">
        <div className="h-40 flex items-center justify-center text-sm text-gray-500 dark:text-gray-400">
          Loading dependency graph…
        </div>
      </Card>
    );
  }

  if (items.length === 0) {
    return (
      <Card blur="md" transparency="light" size="sm" className="border-white/10 dark:border-white/[0.06]">
        <div className="h-24 flex items-center justify-center text-sm text-gray-500 dark:text-gray-400">
          No items in this plan.
        </div>
      </Card>
    );
  }

  return (
    <Card blur="md" transparency="light" size="sm" className="border-white/10 dark:border-white/[0.06] overflow-hidden">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">Dependency DAG</h3>
        <div className="flex items-center gap-3 text-[10px] text-gray-500 dark:text-gray-400">
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-1.5 rounded" style={{ backgroundColor: "#ef4444" }} />
            Critical path
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-1.5 rounded bg-gray-400/50" />
            Dependency
          </span>
        </div>
      </div>

      <div className="overflow-auto relative">
        <svg
          width={Math.max(svgWidth, 400)}
          height={Math.max(svgHeight, 120)}
          className="block"
          role="img"
          aria-label="Dependency DAG"
          onMouseLeave={() => setTooltip(null)}
        >
          <title>Dependency DAG</title>
          <defs>
            <marker id={markerId("arrow-dep")} markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
              <path d="M0,0 L8,3 L0,6 Z" fill="rgba(156,163,175,0.6)" />
            </marker>
            <marker id={markerId("arrow-critical")} markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
              <path d="M0,0 L8,3 L0,6 Z" fill="#ef4444" />
            </marker>
          </defs>

          <g transform={`translate(${PADDING},${PADDING})`}>
            {/* Edges */}
            {dependencies.map((dep) => {
              const from = nodeMap.get(dep.dependency_id);
              const to = nodeMap.get(dep.dependent_id);
              if (!from || !to) return null;

              const x1 = from.x + NODE_WIDTH;
              const y1 = from.y + NODE_HEIGHT / 2;
              const x2 = to.x;
              const y2 = to.y + NODE_HEIGHT / 2;
              const isCritical = from.isCritical && to.isCritical;
              const cx1 = x1 + (x2 - x1) * 0.5;

              return (
                <path
                  key={dep.id}
                  d={`M ${x1} ${y1} C ${cx1} ${y1}, ${cx1} ${y2}, ${x2} ${y2}`}
                  fill="none"
                  stroke={isCritical ? "#ef4444" : "rgba(156,163,175,0.5)"}
                  strokeWidth={isCritical ? 2 : 1.5}
                  strokeDasharray={isCritical ? undefined : "4 3"}
                  markerEnd={isCritical ? `url(#${markerId("arrow-critical")})` : `url(#${markerId("arrow-dep")})`}
                />
              );
            })}

            {/* Nodes */}
            {nodes.map((n) => {
              const color = STATUS_COLORS[n.item.status as PlanItemStatus] ?? "#6b7280";
              return (
                // biome-ignore lint/a11y/useSemanticElements: SVG <g> cannot be replaced with <button>
                <g
                  key={n.item.id}
                  transform={`translate(${n.x},${n.y})`}
                  className="cursor-pointer"
                  role="button"
                  tabIndex={0}
                  aria-label={n.item.title}
                  onMouseEnter={(e) => {
                    const rect = (e.currentTarget as SVGGElement).getBoundingClientRect();
                    setTooltip({ item: n.item, x: rect.left, y: rect.top });
                  }}
                  onMouseLeave={() => setTooltip(null)}
                >
                  {/* Critical path glow */}
                  {n.isCritical && (
                    <rect
                      x={-2}
                      y={-2}
                      width={NODE_WIDTH + 4}
                      height={NODE_HEIGHT + 4}
                      rx={8}
                      fill="none"
                      stroke="#ef4444"
                      strokeWidth={1.5}
                      strokeDasharray="4 2"
                      opacity={0.7}
                    />
                  )}

                  {/* Node background */}
                  <rect
                    width={NODE_WIDTH}
                    height={NODE_HEIGHT}
                    rx={6}
                    fill="rgba(15,23,42,0.85)"
                    stroke={color}
                    strokeWidth={1.5}
                  />

                  {/* Status stripe */}
                  <rect width={4} height={NODE_HEIGHT} rx={2} fill={color} />

                  {/* Item key */}
                  {n.item.item_key && (
                    <text x={12} y={17} fontSize={9} fill={color} fontWeight="600" fontFamily="monospace">
                      {n.item.item_key}
                    </text>
                  )}

                  {/* Title — truncated */}
                  <text
                    x={12}
                    y={n.item.item_key ? 32 : 28}
                    fontSize={10}
                    fill="rgba(255,255,255,0.85)"
                    className="pointer-events-none"
                  >
                    {n.item.title.length > 15 ? `${n.item.title.substring(0, 15)}…` : n.item.title}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>

        {/* Floating tooltip */}
        {tooltip && (
          <div
            className={cn(
              "fixed z-50 pointer-events-none",
              "rounded-lg border border-white/10 p-2.5",
              "backdrop-blur-xl bg-gray-900/95",
              "shadow-lg text-xs max-w-xs",
            )}
            style={{ left: tooltip.x + 8, top: tooltip.y - 8 }}
          >
            <p className="font-medium text-white mb-1">{tooltip.item.title}</p>
            {tooltip.item.item_key && (
              <p className="text-cyan-400 font-mono text-[10px] mb-1">{tooltip.item.item_key}</p>
            )}
            <div className="flex items-center gap-2">
              <span
                className="inline-block w-2 h-2 rounded-full"
                style={{ backgroundColor: STATUS_COLORS[tooltip.item.status as PlanItemStatus] ?? "#6b7280" }}
              />
              <span className="text-gray-300">
                {STATUS_LABELS[tooltip.item.status as PlanItemStatus] ?? tooltip.item.status}
              </span>
            </div>
            {tooltip.item.description && (
              <p className="mt-1 text-gray-400 text-[10px] line-clamp-2">{tooltip.item.description}</p>
            )}
          </div>
        )}
      </div>

      {/* Legend */}
      <div className="mt-3 flex flex-wrap gap-2">
        {Object.entries(STATUS_COLORS).map(([status, color]) => (
          <span key={status} className="flex items-center gap-1 text-[10px] text-gray-500 dark:text-gray-400">
            <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: color }} />
            {STATUS_LABELS[status as PlanItemStatus]}
          </span>
        ))}
      </div>
    </Card>
  );
}

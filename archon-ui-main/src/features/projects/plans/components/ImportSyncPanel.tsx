import { ChevronDown, ChevronRight, FileText, Loader2, RefreshCw, Upload } from "lucide-react";
import { useState } from "react";
import { Button, Card } from "../../../ui/primitives";
import { cn } from "../../../ui/primitives/styles";
import { useImportPlan } from "../hooks/usePlanQueries";
import type { ImplementationPlan, ImportDiffItem, ImportPlanDiff, ImportPlanResponse } from "../types";

interface ImportSyncPanelProps {
  projectId: string;
  plan?: ImplementationPlan;
}

function DiffSection({ title, items, color }: { title: string; items: ImportDiffItem[]; color: string }) {
  const [expanded, setExpanded] = useState(true);
  if (items.length === 0) return null;

  return (
    <div className="mb-2">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-1.5 text-xs font-semibold mb-1"
        style={{ color }}
      >
        {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        {title} ({items.length})
      </button>
      {expanded && (
        <ul className="space-y-1 pl-4">
          {items.map((item) => (
            <li key={item.item_key} className="text-[11px] text-gray-600 dark:text-gray-400">
              <span className="font-mono text-[10px] mr-1" style={{ color }}>
                {item.item_key}
              </span>
              {item.title}
              {item.note && <span className="ml-1 italic text-gray-400 dark:text-gray-600">({item.note})</span>}
              {item.changes && item.changes.length > 0 && (
                <ul className="pl-3 mt-0.5">
                  {item.changes.map((change, i) => (
                    // biome-ignore lint/suspicious/noArrayIndexKey: stable index for static diff items
                    <li key={i} className="text-[10px] text-gray-500 dark:text-gray-500">
                      {JSON.stringify(change)}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function DiffPreview({ diff }: { diff: ImportPlanDiff }) {
  return (
    <div className="mt-3 p-3 rounded-lg bg-gray-50 dark:bg-gray-900/50 border border-white/10 dark:border-white/[0.06]">
      <h4 className="text-xs font-semibold text-gray-700 dark:text-gray-300 mb-2">Import Diff Preview</h4>
      <DiffSection title="Added" items={diff.added} color="#22c55e" />
      <DiffSection title="Changed" items={diff.changed} color="#f59e0b" />
      <DiffSection title="Removed" items={diff.removed} color="#ef4444" />
      <DiffSection title="Unchanged" items={diff.unchanged} color="#6b7280" />
      {diff.added.length === 0 && diff.changed.length === 0 && diff.removed.length === 0 && (
        <p className="text-xs text-gray-500 dark:text-gray-400">No changes detected — plan is up to date.</p>
      )}
    </div>
  );
}

function ImportResult({ response }: { response: ImportPlanResponse }) {
  return (
    <div className="mt-2 p-2.5 rounded-lg bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800/40">
      <p className="text-xs font-medium text-green-700 dark:text-green-400">Import applied — {response.action}</p>
      <ul className="mt-1 text-[11px] text-green-600 dark:text-green-500 space-y-0.5">
        {response.phases_created != null && <li>Phases created: {response.phases_created}</li>}
        {response.items_created != null && <li>Items created: {response.items_created}</li>}
        {response.dependencies_created != null && <li>Dependencies created: {response.dependencies_created}</li>}
        {response.dependencies_updated != null && <li>Dependencies updated: {response.dependencies_updated}</li>}
      </ul>
    </div>
  );
}

export function ImportSyncPanel({ projectId, plan }: ImportSyncPanelProps) {
  const [content, setContent] = useState("");
  const [previewResult, setPreviewResult] = useState<ImportPlanResponse | null>(null);
  const [applyResult, setApplyResult] = useState<ImportPlanResponse | null>(null);
  const importPlan = useImportPlan();

  const handlePreview = async () => {
    if (!content.trim()) return;
    setPreviewResult(null);
    setApplyResult(null);
    const result = await importPlan.mutateAsync({
      project_id: projectId,
      content: content.trim(),
      plan_id: plan?.id,
      preview_only: true,
    });
    setPreviewResult(result);
  };

  const handleApply = async () => {
    if (!content.trim()) return;
    setApplyResult(null);
    const result = await importPlan.mutateAsync({
      project_id: projectId,
      content: content.trim(),
      plan_id: plan?.id,
      preview_only: false,
    });
    setApplyResult(result);
    setPreviewResult(null);
    setContent(""); // Clear after successful apply
  };

  const hasChanges =
    previewResult?.diff &&
    (previewResult.diff.added.length > 0 ||
      previewResult.diff.changed.length > 0 ||
      previewResult.diff.removed.length > 0);

  return (
    <Card blur="md" transparency="light" size="sm" className="border-white/10 dark:border-white/[0.06]">
      <div className="flex items-center gap-2 mb-3">
        <FileText className="w-4 h-4 text-gray-500 dark:text-gray-400" />
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">Import / Sync Plan</h3>
        {plan && (
          <span className="text-[10px] text-gray-400 dark:text-gray-600 ml-auto">
            Re-import to: <span className="font-medium text-gray-600 dark:text-gray-400">{plan.title}</span>
          </span>
        )}
      </div>

      <p className="text-[11px] text-gray-500 dark:text-gray-400 mb-3">
        Paste the canonical plan markdown to preview changes or apply an import. Existing items are never silently
        deleted — removed items are flagged in metadata.
      </p>

      <textarea
        value={content}
        onChange={(e) => {
          setContent(e.target.value);
          setPreviewResult(null);
          setApplyResult(null);
        }}
        placeholder="Paste plan markdown here (phases, items, dependencies)…"
        rows={6}
        className={cn(
          "w-full text-xs rounded-lg p-3 resize-y",
          "bg-gray-50 dark:bg-gray-900/50",
          "border border-gray-200 dark:border-gray-700",
          "text-gray-700 dark:text-gray-300",
          "placeholder-gray-400 dark:placeholder-gray-600",
          "focus:outline-none focus:ring-1 focus:ring-cyan-500/50",
          "font-mono",
        )}
      />

      <div className="flex items-center gap-2 mt-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={handlePreview}
          disabled={!content.trim() || importPlan.isPending}
          className="text-xs h-7 gap-1"
        >
          {importPlan.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
          Preview diff
        </Button>

        <Button
          variant="ghost"
          size="sm"
          onClick={handleApply}
          disabled={!content.trim() || importPlan.isPending}
          className={cn(
            "text-xs h-7 gap-1",
            hasChanges ? "text-cyan-600 dark:text-cyan-400 border-cyan-500/30" : undefined,
          )}
        >
          {importPlan.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Upload className="w-3 h-3" />}
          Apply import
        </Button>
      </div>

      {importPlan.isError && (
        <p className="mt-2 text-xs text-red-500 dark:text-red-400">{(importPlan.error as Error).message}</p>
      )}

      {previewResult?.diff && <DiffPreview diff={previewResult.diff} />}
      {applyResult && !applyResult.diff && <ImportResult response={applyResult} />}
    </Card>
  );
}

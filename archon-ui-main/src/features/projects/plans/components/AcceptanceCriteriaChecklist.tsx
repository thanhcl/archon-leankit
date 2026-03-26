import { Check, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Button, Card, Input } from "../../../ui/primitives";
import { cn } from "../../../ui/primitives/styles";
import { useUpdateItemMetadata } from "../hooks/usePlanQueries";
import type { AcceptanceCriteria, ImplementationItem } from "../types";

interface AcceptanceCriteriaChecklistProps {
  item: ImplementationItem;
  readOnly?: boolean;
}

function getCriteria(item: ImplementationItem): AcceptanceCriteria[] {
  const raw = item.metadata?.acceptance_criteria;
  if (!Array.isArray(raw)) return [];
  return raw as AcceptanceCriteria[];
}

export function AcceptanceCriteriaChecklist({ item, readOnly = false }: AcceptanceCriteriaChecklistProps) {
  const [newText, setNewText] = useState("");
  const updateMetadata = useUpdateItemMetadata();

  const criteria = getCriteria(item);

  const handleToggle = (idx: number) => {
    if (readOnly) return;
    const updated = criteria.map((c, i) => (i === idx ? { ...c, checked: !c.checked } : c));
    updateMetadata.mutate({
      itemId: item.id,
      metadata: { ...(item.metadata ?? {}), acceptance_criteria: updated },
    });
  };

  const handleAdd = () => {
    const text = newText.trim();
    if (!text) return;
    const updated = [...criteria, { text, checked: false }];
    updateMetadata.mutate({
      itemId: item.id,
      metadata: { ...(item.metadata ?? {}), acceptance_criteria: updated },
    });
    setNewText("");
  };

  const handleDelete = (idx: number) => {
    const updated = criteria.filter((_, i) => i !== idx);
    updateMetadata.mutate({
      itemId: item.id,
      metadata: { ...(item.metadata ?? {}), acceptance_criteria: updated },
    });
  };

  const checkedCount = criteria.filter((c) => c.checked).length;

  return (
    <Card blur="md" transparency="light" size="sm" className="border-white/10 dark:border-white/[0.06]">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-200">Acceptance Criteria</h4>
          <p className="text-[10px] text-gray-500 dark:text-gray-400 mt-0.5">
            {item.item_key ? (
              <span className="font-mono text-cyan-500 dark:text-cyan-400 mr-1">{item.item_key}</span>
            ) : null}
            {item.title}
          </p>
        </div>
        {criteria.length > 0 && (
          <span className="text-xs text-gray-500 dark:text-gray-400">
            {checkedCount}/{criteria.length} checked
          </span>
        )}
      </div>

      {criteria.length === 0 && readOnly && (
        <p className="text-xs text-gray-500 dark:text-gray-400">No acceptance criteria defined.</p>
      )}

      <ul className="space-y-1.5 mb-3">
        {criteria.map((criterion, idx) => (
          // eslint-disable-next-line react/no-array-index-key
          <li key={`${criterion.text}-${idx}`} className="flex items-start gap-2 group">
            <button
              type="button"
              onClick={() => handleToggle(idx)}
              disabled={readOnly || updateMetadata.isPending}
              className={cn(
                "mt-0.5 flex-shrink-0 w-4 h-4 rounded border transition-colors",
                criterion.checked
                  ? "bg-green-500 dark:bg-green-600 border-green-500 dark:border-green-600"
                  : "border-gray-300 dark:border-gray-600 hover:border-green-400 dark:hover:border-green-500",
                readOnly && "cursor-default",
              )}
              aria-label={criterion.checked ? "Uncheck criterion" : "Check criterion"}
            >
              {criterion.checked && <Check className="w-3 h-3 text-white m-auto" />}
            </button>

            <span
              className={cn(
                "flex-1 text-xs leading-relaxed",
                criterion.checked
                  ? "line-through text-gray-400 dark:text-gray-600"
                  : "text-gray-700 dark:text-gray-300",
              )}
            >
              {criterion.text}
            </span>

            {!readOnly && (
              <button
                type="button"
                onClick={() => handleDelete(idx)}
                disabled={updateMetadata.isPending}
                className="opacity-0 group-hover:opacity-100 transition-opacity text-gray-400 hover:text-red-500 dark:hover:text-red-400"
                aria-label="Delete criterion"
              >
                <Trash2 className="w-3 h-3" />
              </button>
            )}
          </li>
        ))}
      </ul>

      {!readOnly && (
        <div className="flex items-center gap-2">
          <Input
            value={newText}
            onChange={(e) => setNewText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleAdd();
            }}
            placeholder="Add acceptance criterion…"
            className="text-xs h-7 flex-1"
          />
          <Button
            variant="ghost"
            size="sm"
            onClick={handleAdd}
            disabled={!newText.trim() || updateMetadata.isPending}
            className="px-2 h-7"
            aria-label="Add criterion"
          >
            <Plus className="w-3.5 h-3.5" />
          </Button>
        </div>
      )}
    </Card>
  );
}

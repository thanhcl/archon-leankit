import { ChevronDown, ChevronRight, Save, Trash2, X } from "lucide-react";
import { useState } from "react";
import { Button } from "../../../ui/primitives";
import type { Rule, RuleSection, UpdateRuleRequest } from "../types";
import { RULE_SECTIONS } from "../types";

interface RuleRowProps {
  rule: Rule;
  onSave: (ruleId: string, updates: UpdateRuleRequest) => void;
  onDelete: (rule: Rule) => void;
  isSaving?: boolean;
}

export const RuleRow = ({ rule, onSave, onDelete, isSaving }: RuleRowProps) => {
  const [expanded, setExpanded] = useState(false);
  const [editedText, setEditedText] = useState(rule.rule_text);
  const [editedSection, setEditedSection] = useState<RuleSection>(rule.section);
  const [isDirty, setIsDirty] = useState(false);

  const handleTextChange = (value: string) => {
    setEditedText(value);
    setIsDirty(value !== rule.rule_text || editedSection !== rule.section);
  };

  const handleSectionChange = (value: RuleSection) => {
    setEditedSection(value);
    setIsDirty(editedText !== rule.rule_text || value !== rule.section);
  };

  const handleSave = () => {
    const updates: UpdateRuleRequest = {};
    if (editedText !== rule.rule_text) updates.rule_text = editedText;
    if (editedSection !== rule.section) updates.section = editedSection;
    onSave(rule.id, updates);
    setIsDirty(false);
  };

  const handleCancel = () => {
    setEditedText(rule.rule_text);
    setEditedSection(rule.section);
    setIsDirty(false);
  };

  const preview = rule.rule_text.length > 80 ? `${rule.rule_text.substring(0, 80)}...` : rule.rule_text;
  const updatedDate = new Date(rule.updated_at).toLocaleDateString();

  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
      {/* Collapsed row */}
      <button
        type="button"
        className="flex items-center gap-3 px-4 py-3 w-full text-left cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? (
          <ChevronDown className="w-4 h-4 text-gray-400 flex-shrink-0" />
        ) : (
          <ChevronRight className="w-4 h-4 text-gray-400 flex-shrink-0" />
        )}

        <span className="px-2 py-0.5 rounded text-xs font-medium bg-cyan-100 text-cyan-800 dark:bg-cyan-900/30 dark:text-cyan-300 flex-shrink-0">
          {rule.section}
        </span>

        <span className="text-sm text-gray-700 dark:text-gray-300 flex-1 truncate">{preview}</span>

        <span className="text-xs text-gray-400 flex-shrink-0">{updatedDate}</span>

        <Button
          variant="ghost"
          size="sm"
          onClick={(e) => {
            e.stopPropagation();
            onDelete(rule);
          }}
          className="text-red-500 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300 px-1.5"
          aria-label="Delete rule"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </Button>
      </button>

      {/* Expanded edit area */}
      {expanded && (
        <div className="border-t border-gray-200 dark:border-gray-700 p-4 space-y-3 bg-gray-50/50 dark:bg-gray-900/30">
          <div className="flex items-center gap-3">
            <label htmlFor={`rule-section-${rule.id}`} className="text-xs font-medium text-gray-500 dark:text-gray-400">
              Section
            </label>
            <select
              id={`rule-section-${rule.id}`}
              value={editedSection}
              onChange={(e) => handleSectionChange(e.target.value as RuleSection)}
              className="rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-cyan-500"
            >
              {RULE_SECTIONS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>

            <span className="text-xs text-gray-400 ml-auto">
              Source: {rule.source} | Priority: {rule.priority}
            </span>
          </div>

          <textarea
            value={editedText}
            onChange={(e) => handleTextChange(e.target.value)}
            rows={6}
            className="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-cyan-500 resize-y font-mono"
          />

          {isDirty && (
            <div className="flex items-center justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={handleCancel}>
                <X className="w-4 h-4 mr-1" />
                Cancel
              </Button>
              <Button size="sm" onClick={handleSave} disabled={isSaving}>
                <Save className="w-4 h-4 mr-1" />
                {isSaving ? "Saving..." : "Save"}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

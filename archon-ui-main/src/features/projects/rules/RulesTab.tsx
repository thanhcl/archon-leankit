import { Plus, ScrollText, Sparkles } from "lucide-react";
import { useState } from "react";
import { DeleteConfirmModal } from "../../ui/components/DeleteConfirmModal";
import { Button } from "../../ui/primitives";
import { NewRuleModal } from "./components/NewRuleModal";
import { OptimizeDiffModal } from "./components/OptimizeDiffModal";
import { RuleRow } from "./components/RuleRow";
import { useCreateRule, useDeleteRule, useOptimizeRules, useProjectRules, useUpdateRule } from "./hooks";
import type { OptimizeSuggestion, Rule, RuleSection, UpdateRuleRequest } from "./types";

interface RulesTabProps {
  projectId: string;
}

export const RulesTab = ({ projectId }: RulesTabProps) => {
  const { data: rules = [], isLoading } = useProjectRules(projectId);
  const createRuleMutation = useCreateRule(projectId);
  const updateRuleMutation = useUpdateRule(projectId);
  const deleteRuleMutation = useDeleteRule(projectId);
  const optimizeMutation = useOptimizeRules(projectId);

  const [showNewRuleModal, setShowNewRuleModal] = useState(false);
  const [ruleToDelete, setRuleToDelete] = useState<Rule | null>(null);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [showOptimizeModal, setShowOptimizeModal] = useState(false);
  const [suggestions, setSuggestions] = useState<OptimizeSuggestion[]>([]);

  const handleAddRule = async (section: RuleSection, ruleText: string) => {
    await createRuleMutation.mutateAsync({ section, rule_text: ruleText });
    setShowNewRuleModal(false);
  };

  const handleSaveRule = (ruleId: string, updates: UpdateRuleRequest) => {
    updateRuleMutation.mutate({ ruleId, updates });
  };

  const handleDeleteRule = (rule: Rule) => {
    setRuleToDelete(rule);
    setShowDeleteModal(true);
  };

  const confirmDelete = async () => {
    if (!ruleToDelete) return;
    await deleteRuleMutation.mutateAsync(ruleToDelete.id);
    setShowDeleteModal(false);
    setRuleToDelete(null);
  };

  const handleOptimize = async () => {
    const result = await optimizeMutation.mutateAsync();
    setSuggestions(result.suggestions);
    setShowOptimizeModal(true);
  };

  const handleApproveSuggestion = async (suggestion: OptimizeSuggestion) => {
    await createRuleMutation.mutateAsync({
      section: suggestion.section as RuleSection,
      rule_text: suggestion.rule_text,
      source: "auto-optimize",
    });
    setSuggestions((prev) => prev.filter((s) => s !== suggestion));
    if (suggestions.length <= 1) {
      setShowOptimizeModal(false);
    }
  };

  const handleRejectSuggestion = (index: number) => {
    setSuggestions((prev) => prev.filter((_, i) => i !== index));
    if (suggestions.length <= 1) {
      setShowOptimizeModal(false);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-cyan-500" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ScrollText className="w-5 h-5 text-gray-700 dark:text-gray-300" />
          <h3 className="text-lg font-semibold text-gray-800 dark:text-white">Rules</h3>
          <span className="text-xs text-gray-500 dark:text-gray-400">
            {rules.length} rule{rules.length !== 1 ? "s" : ""}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={handleOptimize}
            disabled={optimizeMutation.isPending}
            className="text-purple-600 dark:text-purple-400 hover:bg-purple-50 dark:hover:bg-purple-900/20"
          >
            <Sparkles className="w-4 h-4 mr-1" />
            {optimizeMutation.isPending ? "Analyzing..." : "Auto-Optimize"}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowNewRuleModal(true)}
            className="text-cyan-600 dark:text-cyan-400 hover:bg-cyan-500/10"
            aria-label="Add new rule"
          >
            <Plus className="w-4 h-4" />
          </Button>
        </div>
      </div>

      {/* Rules list */}
      {rules.length === 0 ? (
        <div className="text-center py-12 text-gray-500 dark:text-gray-400">
          <ScrollText className="w-12 h-12 mx-auto mb-3 opacity-30" />
          <p className="text-sm">No rules configured for this project</p>
          <p className="text-xs mt-1">Add rules to customize AI behavior for this project</p>
        </div>
      ) : (
        <div className="space-y-2">
          {rules.map((rule) => (
            <RuleRow
              key={rule.id}
              rule={rule}
              onSave={handleSaveRule}
              onDelete={handleDeleteRule}
              isSaving={updateRuleMutation.isPending}
            />
          ))}
        </div>
      )}

      {/* Modals */}
      <NewRuleModal
        open={showNewRuleModal}
        onOpenChange={setShowNewRuleModal}
        onAdd={handleAddRule}
        isLoading={createRuleMutation.isPending}
      />

      <DeleteConfirmModal
        open={showDeleteModal}
        onOpenChange={(open) => {
          setShowDeleteModal(open);
          if (!open) setRuleToDelete(null);
        }}
        itemName={ruleToDelete ? `${ruleToDelete.section} rule` : ""}
        onConfirm={confirmDelete}
        onCancel={() => {
          setShowDeleteModal(false);
          setRuleToDelete(null);
        }}
        type="rule"
      />

      <OptimizeDiffModal
        open={showOptimizeModal}
        onOpenChange={setShowOptimizeModal}
        suggestions={suggestions}
        onApprove={handleApproveSuggestion}
        onReject={handleRejectSuggestion}
        isApplying={createRuleMutation.isPending}
      />
    </div>
  );
};

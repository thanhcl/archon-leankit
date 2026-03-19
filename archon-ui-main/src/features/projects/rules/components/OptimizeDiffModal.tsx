import { Check, Sparkles, X } from "lucide-react";
import { Button } from "../../../ui/primitives";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "../../../ui/primitives/dialog";
import type { OptimizeSuggestion, RuleSuggestion } from "../types";

interface OptimizeDiffModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  suggestions: OptimizeSuggestion[];
  autoSuggestions?: RuleSuggestion[];
  onApprove: (suggestion: OptimizeSuggestion) => void;
  onReject: (index: number) => void;
  onApproveAuto?: (suggestion: RuleSuggestion) => void;
  onRejectAuto?: (suggestionId: string) => void;
  isApplying?: boolean;
}

const ACTION_COLORS = {
  add: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
  modify: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300",
  remove: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300",
};

export const OptimizeDiffModal = ({
  open,
  onOpenChange,
  suggestions,
  autoSuggestions = [],
  onApprove,
  onReject,
  onApproveAuto,
  onRejectAuto,
  isApplying,
}: OptimizeDiffModalProps) => {
  const totalCount = suggestions.length + autoSuggestions.length;

  if (totalCount === 0) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Optimization Suggestions</DialogTitle>
          </DialogHeader>
          <div className="py-8 text-center text-gray-500 dark:text-gray-400">
            <p className="text-sm">No optimization suggestions available.</p>
            <p className="text-xs mt-1">The project may not have enough task history for analysis.</p>
          </div>
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Optimization Suggestions ({totalCount})</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          {autoSuggestions.length > 0 && (
            <div className="space-y-4">
              <div className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
                <Sparkles className="w-3 h-3" />
                <span>Auto-generated from recurring learnings</span>
              </div>
              {autoSuggestions.map((suggestion) => (
                <div
                  key={suggestion.id}
                  className="border border-amber-200 dark:border-amber-800 rounded-lg p-4 space-y-3"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="px-2 py-0.5 rounded text-xs font-medium bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300">
                        AUTO
                      </span>
                      <span className="text-xs text-gray-500 dark:text-gray-400">{suggestion.section}</span>
                    </div>
                    <span className="text-xs text-gray-500 dark:text-gray-400">
                      {Math.round(suggestion.confidence * 100)}% confidence
                    </span>
                  </div>

                  <div className="bg-gray-50 dark:bg-gray-800/50 rounded p-3">
                    <pre className="text-sm text-gray-800 dark:text-gray-200 whitespace-pre-wrap font-mono">
                      {suggestion.rule_text}
                    </pre>
                  </div>

                  {suggestion.reason && <p className="text-xs text-gray-500 dark:text-gray-400">{suggestion.reason}</p>}

                  <div className="flex items-center justify-end gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => onRejectAuto?.(suggestion.id)}
                      disabled={isApplying}
                      className="text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20"
                    >
                      <X className="w-4 h-4 mr-1" />
                      Reject
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => onApproveAuto?.(suggestion)}
                      disabled={isApplying}
                      className="bg-green-600 hover:bg-green-700 text-white"
                    >
                      <Check className="w-4 h-4 mr-1" />
                      Approve
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {suggestions.length > 0 && (
            <div className="space-y-4">
              {autoSuggestions.length > 0 && (
                <div className="border-t border-gray-200 dark:border-gray-700 pt-4 text-xs text-gray-500 dark:text-gray-400">
                  From analysis
                </div>
              )}
              {suggestions.map((suggestion, index) => (
                <div
                  key={`${suggestion.section}-${suggestion.action}-${index}`}
                  className="border border-gray-200 dark:border-gray-700 rounded-lg p-4 space-y-3"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${ACTION_COLORS[suggestion.action]}`}>
                        {suggestion.action.toUpperCase()}
                      </span>
                      <span className="text-xs text-gray-500 dark:text-gray-400">{suggestion.section}</span>
                    </div>
                    <span className="text-xs text-gray-500 dark:text-gray-400">
                      {Math.round(suggestion.confidence * 100)}% confidence
                    </span>
                  </div>

                  <div className="bg-gray-50 dark:bg-gray-800/50 rounded p-3">
                    <pre className="text-sm text-gray-800 dark:text-gray-200 whitespace-pre-wrap font-mono">
                      {suggestion.rule_text}
                    </pre>
                  </div>

                  <p className="text-xs text-gray-500 dark:text-gray-400">{suggestion.reason}</p>

                  <div className="flex items-center justify-end gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => onReject(index)}
                      className="text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20"
                    >
                      <X className="w-4 h-4 mr-1" />
                      Reject
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => onApprove(suggestion)}
                      disabled={isApplying}
                      className="bg-green-600 hover:bg-green-700 text-white"
                    >
                      <Check className="w-4 h-4 mr-1" />
                      Approve
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};

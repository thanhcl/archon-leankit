import { useId, useState } from "react";
import { Button } from "../../../ui/primitives";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "../../../ui/primitives/dialog";
import type { RuleSection } from "../types";
import { RULE_SECTIONS } from "../types";

interface NewRuleModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAdd: (section: RuleSection, ruleText: string) => void;
  isLoading?: boolean;
}

export const NewRuleModal = ({ open, onOpenChange, onAdd, isLoading }: NewRuleModalProps) => {
  const id = useId();
  const [section, setSection] = useState<RuleSection>("coding-style");
  const [ruleText, setRuleText] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!ruleText.trim()) return;
    onAdd(section, ruleText.trim());
    setSection("coding-style");
    setRuleText("");
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add New Rule</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label
              htmlFor={`${id}-section`}
              className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
            >
              Section
            </label>
            <select
              id={`${id}-section`}
              value={section}
              onChange={(e) => setSection(e.target.value as RuleSection)}
              className="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-cyan-500"
            >
              {RULE_SECTIONS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label
              htmlFor={`${id}-content`}
              className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
            >
              Rule Content
            </label>
            <textarea
              id={`${id}-content`}
              value={ruleText}
              onChange={(e) => setRuleText(e.target.value)}
              placeholder="Enter the rule text..."
              rows={5}
              className="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-cyan-500 resize-y"
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!ruleText.trim() || isLoading}>
              {isLoading ? "Creating..." : "Create Rule"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};

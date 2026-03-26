import { Plus } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "../../../ui/primitives/styles";
import type { TaskBoardStatus, TaskPriority } from "../types";

interface InlineTaskFormProps {
  projectId: string;
  status: TaskBoardStatus;
  onSubmit: (data: { title: string; priority: TaskPriority; status: TaskBoardStatus }) => void;
  onCancel: () => void;
  isSubmitting?: boolean;
}

export const InlineTaskForm = ({ status, onSubmit, onCancel, isSubmitting }: InlineTaskFormProps) => {
  const [title, setTitle] = useState("");
  const [priority, setPriority] = useState<TaskPriority>("medium");
  const inputRef = useRef<HTMLInputElement>(null);
  const formRef = useRef<HTMLDivElement>(null);

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Close on click outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (formRef.current && !formRef.current.contains(e.target as Node)) {
        onCancel();
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [onCancel]);

  const handleSubmit = useCallback(() => {
    const trimmed = title.trim();
    if (!trimmed || isSubmitting) return;
    onSubmit({ title: trimmed, priority, status });
    setTitle("");
    setPriority("medium");
    inputRef.current?.focus();
  }, [title, priority, status, onSubmit, isSubmitting]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleSubmit();
      } else if (e.key === "Escape") {
        onCancel();
      }
    },
    [handleSubmit, onCancel],
  );

  return (
    <div
      ref={formRef}
      className="px-2 py-2 mx-2 mb-2 rounded-lg border border-cyan-500/30 bg-white/5 dark:bg-gray-900/50 backdrop-blur-md"
    >
      <input
        ref={inputRef}
        type="text"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Task title..."
        disabled={isSubmitting}
        className={cn(
          "w-full px-2 py-1.5 text-sm rounded-md border border-gray-300/30 dark:border-gray-600/30",
          "bg-white/10 dark:bg-gray-800/50 text-gray-900 dark:text-white",
          "placeholder-gray-500 dark:placeholder-gray-400",
          "focus:outline-none focus:ring-1 focus:ring-cyan-400/50 focus:border-cyan-400/50",
          isSubmitting && "opacity-50 cursor-not-allowed",
        )}
      />
      <div className="flex items-center justify-between mt-2 gap-2">
        <select
          value={priority}
          onChange={(e) => setPriority(e.target.value as TaskPriority)}
          disabled={isSubmitting}
          className={cn(
            "px-2 py-1 text-xs rounded-md border border-gray-300/30 dark:border-gray-600/30",
            "bg-white/10 dark:bg-gray-800/50 text-gray-900 dark:text-white",
            "focus:outline-none focus:ring-1 focus:ring-cyan-400/50",
            isSubmitting && "opacity-50 cursor-not-allowed",
          )}
        >
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
          <option value="critical">Critical</option>
        </select>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={!title.trim() || isSubmitting}
          className={cn(
            "flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-md",
            "bg-cyan-500/20 text-cyan-400 hover:bg-cyan-500/30",
            "disabled:opacity-40 disabled:cursor-not-allowed",
            "transition-colors duration-150",
          )}
        >
          <Plus className="w-3 h-3" />
          Add
        </button>
      </div>
    </div>
  );
};

/**
 * TaskFeedbackPanel
 *
 * Inline owner-feedback widget shown on done tasks.
 * Lets the owner submit a 1–5 star rating, optional notes, and improvement tags.
 * If feedback already exists it renders read-only by default with an edit toggle.
 */

import { MessageSquarePlus, Star } from "lucide-react";
import type React from "react";
import { useState } from "react";
import { cn } from "../../../ui/primitives/styles";
import { useSubmitFeedback } from "../hooks";
import type { Task } from "../types";

interface TaskFeedbackPanelProps {
  task: Task;
  projectId: string;
}

function StarRating({
  value,
  onChange,
  readonly,
}: {
  value: number;
  onChange?: (v: number) => void;
  readonly?: boolean;
}) {
  const [hover, setHover] = useState(0);
  const active = hover || value;

  return (
    <div className="flex items-center gap-0.5">
      {[1, 2, 3, 4, 5].map((star) => (
        <button
          key={star}
          type="button"
          disabled={readonly}
          onClick={() => onChange?.(star)}
          onMouseEnter={() => !readonly && setHover(star)}
          onMouseLeave={() => !readonly && setHover(0)}
          className={cn("p-0.5 transition-colors", readonly ? "cursor-default" : "cursor-pointer")}
          aria-label={`${star} star${star !== 1 ? "s" : ""}`}
        >
          <Star
            className={cn(
              "w-3.5 h-3.5 transition-colors",
              star <= active ? "fill-yellow-400 text-yellow-400" : "fill-transparent text-gray-400 dark:text-gray-600",
              !readonly && star <= (hover || 0) && "text-yellow-300",
            )}
          />
        </button>
      ))}
    </div>
  );
}

export const TaskFeedbackPanel: React.FC<TaskFeedbackPanelProps> = ({ task, projectId }) => {
  const hasFeedback = task.owner_rating != null;
  const [isEditing, setIsEditing] = useState(!hasFeedback);
  const [rating, setRating] = useState(task.owner_rating ?? 0);
  const [notes, setNotes] = useState(task.owner_notes ?? "");
  const [tagsInput, setTagsInput] = useState((task.improvement_tags ?? []).join(", "));

  const submitFeedback = useSubmitFeedback(projectId);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (rating === 0) return;

    const improvement_tags = tagsInput
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);

    submitFeedback.mutate(
      { taskId: task.id, feedback: { owner_rating: rating, owner_notes: notes || null, improvement_tags } },
      { onSuccess: () => setIsEditing(false) },
    );
  };

  if (task.status !== "done") return null;

  if (!isEditing && hasFeedback) {
    return (
      // biome-ignore lint/a11y/useKeyWithClickEvents: stop-propagation wrapper only
      // biome-ignore lint/a11y/noStaticElementInteractions: stop-propagation wrapper only
      <div
        className="border-t border-gray-200/20 dark:border-gray-700/30 pt-2 mt-2 pl-1.5 pr-3"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-2">
          <StarRating value={task.owner_rating ?? 0} readonly />
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setIsEditing(true);
            }}
            className="text-[10px] text-cyan-500 hover:text-cyan-400 transition-colors"
            aria-label="Edit feedback"
          >
            Edit
          </button>
        </div>
        {task.owner_notes && (
          <p className="text-[10px] text-gray-500 dark:text-gray-400 mt-1 line-clamp-2">{task.owner_notes}</p>
        )}
        {(task.improvement_tags ?? []).length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {task.improvement_tags?.map((tag) => (
              <span
                key={tag}
                className="px-1 py-0.5 rounded text-[9px] bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20"
              >
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: stop-propagation wrapper on form
    <form
      onSubmit={handleSubmit}
      onClick={(e) => e.stopPropagation()}
      className="border-t border-gray-200/20 dark:border-gray-700/30 pt-2 mt-2 pl-1.5 pr-3 space-y-1.5"
    >
      <div className="flex items-center gap-2">
        <MessageSquarePlus className="w-3 h-3 text-gray-400" />
        <span className="text-[10px] text-gray-400 font-medium">Owner feedback</span>
      </div>

      <StarRating value={rating} onChange={setRating} />

      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="Notes (optional)"
        rows={2}
        className={cn(
          "w-full text-[10px] bg-white/5 dark:bg-black/20",
          "border border-gray-200/20 dark:border-gray-700/30 rounded",
          "px-1.5 py-1 text-gray-700 dark:text-gray-300 placeholder-gray-400",
          "resize-none focus:outline-none focus:ring-1 focus:ring-cyan-500/50",
        )}
        onClick={(e) => e.stopPropagation()}
      />

      <input
        type="text"
        value={tagsInput}
        onChange={(e) => setTagsInput(e.target.value)}
        placeholder="Improvement tags (comma-separated)"
        className={cn(
          "w-full text-[10px] bg-white/5 dark:bg-black/20",
          "border border-gray-200/20 dark:border-gray-700/30 rounded",
          "px-1.5 py-1 text-gray-700 dark:text-gray-300 placeholder-gray-400",
          "focus:outline-none focus:ring-1 focus:ring-cyan-500/50",
        )}
        onClick={(e) => e.stopPropagation()}
      />

      <div className="flex items-center gap-1.5">
        <button
          type="submit"
          disabled={rating === 0 || submitFeedback.isPending}
          className={cn(
            "px-2 py-0.5 rounded text-[10px] font-medium transition-colors",
            "bg-cyan-500/20 text-cyan-400 border border-cyan-500/30",
            "hover:bg-cyan-500/30 disabled:opacity-50 disabled:cursor-not-allowed",
          )}
        >
          {submitFeedback.isPending ? "Saving…" : "Save"}
        </button>
        {hasFeedback && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setIsEditing(false);
            }}
            className="px-2 py-0.5 rounded text-[10px] text-gray-400 hover:text-gray-300 transition-colors"
          >
            Cancel
          </button>
        )}
      </div>
    </form>
  );
};

import { Check, Clipboard, Edit, RotateCcw, Trash2 } from "lucide-react";
import type React from "react";
import { useToast } from "@/features/shared/hooks/useToast";
import { cn, glassmorphism } from "../../../ui/primitives/styles";
import { SimpleTooltip } from "../../../ui/primitives/tooltip";

interface TaskCardActionsProps {
  taskId: string;
  taskTitle: string;
  taskStatus: string;
  onEdit: () => void;
  onDelete: () => void;
  onApprove?: () => void;
  onReject?: () => void;
  isDeleting?: boolean;
  isTransitioning?: boolean;
}

export const TaskCardActions: React.FC<TaskCardActionsProps> = ({
  taskId,
  taskTitle,
  taskStatus,
  onEdit,
  onDelete,
  onApprove,
  onReject,
  isDeleting = false,
  isTransitioning = false,
}) => {
  const { showToast } = useToast();

  const handleCopyId = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(taskId);
      showToast("Task ID copied to clipboard", "success");
    } catch {
      // Fallback for older browsers
      try {
        const ta = document.createElement("textarea");
        ta.value = taskId;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        showToast("Task ID copied to clipboard", "success");
      } catch {
        showToast("Failed to copy Task ID", "error");
      }
    }
  };

  const showReviewActions = taskStatus === "review" && onApprove && onReject;

  return (
    <div className="flex items-center gap-1.5">
      {/* Approve/Reject buttons — only visible when task is in review */}
      {showReviewActions && (
        <>
          <SimpleTooltip content={isTransitioning ? "Processing..." : "Approve task"}>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                if (!isTransitioning) onApprove();
              }}
              disabled={isTransitioning}
              className={cn(
                "w-5 h-5 rounded-full flex items-center justify-center",
                "transition-all duration-300",
                "bg-green-100/80 dark:bg-green-500/20",
                "text-green-600 dark:text-green-400",
                "hover:bg-green-200 dark:hover:bg-green-500/30",
                "hover:shadow-[0_0_10px_rgba(34,197,94,0.3)]",
                isTransitioning && "opacity-50 cursor-not-allowed",
              )}
              aria-label={`Approve ${taskTitle}`}
            >
              <Check className={cn("w-3 h-3", isTransitioning && "animate-pulse")} />
            </button>
          </SimpleTooltip>

          <SimpleTooltip content={isTransitioning ? "Processing..." : "Reject task"}>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                if (!isTransitioning) onReject();
              }}
              disabled={isTransitioning}
              className={cn(
                "w-5 h-5 rounded-full flex items-center justify-center",
                "transition-all duration-300",
                "bg-amber-100/80 dark:bg-amber-500/20",
                "text-amber-600 dark:text-amber-400",
                "hover:bg-amber-200 dark:hover:bg-amber-500/30",
                "hover:shadow-[0_0_10px_rgba(245,158,11,0.3)]",
                isTransitioning && "opacity-50 cursor-not-allowed",
              )}
              aria-label={`Reject ${taskTitle}`}
            >
              <RotateCcw className={cn("w-3 h-3", isTransitioning && "animate-pulse")} />
            </button>
          </SimpleTooltip>
        </>
      )}

      <SimpleTooltip content={isDeleting ? "Deleting..." : "Delete task"}>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            if (!isDeleting) onDelete();
          }}
          disabled={isDeleting}
          className={cn(
            "w-5 h-5 rounded-full flex items-center justify-center",
            "transition-all duration-300",
            glassmorphism.priority.critical.background,
            glassmorphism.priority.critical.text,
            glassmorphism.priority.critical.hover,
            glassmorphism.priority.critical.glow,
            isDeleting && "opacity-50 cursor-not-allowed",
          )}
          aria-label={isDeleting ? "Deleting task..." : `Delete ${taskTitle}`}
        >
          <Trash2 className={cn("w-3 h-3", isDeleting && "animate-pulse")} />
        </button>
      </SimpleTooltip>

      <SimpleTooltip content="Edit task">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onEdit();
          }}
          className={cn(
            "w-5 h-5 rounded-full flex items-center justify-center",
            "transition-all duration-300",
            "bg-cyan-100/80 dark:bg-cyan-500/20",
            "text-cyan-600 dark:text-cyan-400",
            "hover:bg-cyan-200 dark:hover:bg-cyan-500/30",
            "hover:shadow-[0_0_10px_rgba(34,211,238,0.3)]",
          )}
          aria-label={`Edit ${taskTitle}`}
        >
          <Edit className="w-3 h-3" />
        </button>
      </SimpleTooltip>

      <SimpleTooltip content="Copy Task ID">
        <button
          type="button"
          onClick={handleCopyId}
          className={cn(
            "w-5 h-5 rounded-full flex items-center justify-center",
            "transition-all duration-300",
            glassmorphism.priority.low.background,
            glassmorphism.priority.low.text,
            glassmorphism.priority.low.hover,
            glassmorphism.priority.low.glow,
          )}
          aria-label="Copy Task ID"
        >
          <Clipboard className="w-3 h-3" />
        </button>
      </SimpleTooltip>
    </div>
  );
};

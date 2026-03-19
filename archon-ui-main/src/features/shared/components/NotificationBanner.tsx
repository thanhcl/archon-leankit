import { Bell, X } from "lucide-react";
import { Button } from "../../ui/primitives";
import { cn, glassmorphism } from "../../ui/primitives/styles";

interface NotificationBannerProps {
  onEnable: () => void;
  onDismiss: () => void;
}

export const NotificationBanner = ({ onEnable, onDismiss }: NotificationBannerProps) => {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 px-4 py-2.5 rounded-lg mb-3",
        glassmorphism.background.subtle,
        glassmorphism.border.default,
      )}
    >
      <div className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
        <Bell className="w-4 h-4 text-cyan-500" />
        <span>Enable notifications to get alerted when tasks are ready for review</span>
      </div>
      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={onEnable} className="text-cyan-600 dark:text-cyan-400">
          Enable
        </Button>
        <button
          type="button"
          onClick={onDismiss}
          className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors"
          aria-label="Dismiss notification banner"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
};

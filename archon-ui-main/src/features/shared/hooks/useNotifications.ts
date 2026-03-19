import { useCallback, useEffect, useRef, useState } from "react";
import type { Task } from "../../projects/tasks/types";

export type NotificationPermissionState = "default" | "granted" | "denied";

/**
 * Hook for browser push notifications when tasks reach "review" status.
 * Uses the Web Notification API (no service worker required — tab must be open).
 */
const DISMISS_KEY = "archon:notification-banner-dismissed";

export function useNotifications(tasks: Task[]) {
  const [permission, setPermission] = useState<NotificationPermissionState>(() => {
    if (typeof Notification === "undefined") return "denied";
    // If user previously dismissed the banner, treat as denied for UI purposes
    if (Notification.permission === "default" && localStorage.getItem(DISMISS_KEY) === "1") {
      return "denied";
    }
    return Notification.permission as NotificationPermissionState;
  });

  // Track previous task statuses to detect transitions
  const prevStatusMapRef = useRef<Map<string, string>>(new Map());
  // Track whether initial load has happened (skip notifications on first load)
  const isInitialLoadRef = useRef(true);

  const requestPermission = useCallback(async () => {
    if (typeof Notification === "undefined") return "denied" as const;
    localStorage.removeItem(DISMISS_KEY);
    const result = await Notification.requestPermission();
    setPermission(result as NotificationPermissionState);
    return result;
  }, []);

  const dismissBanner = useCallback(() => {
    localStorage.setItem(DISMISS_KEY, "1");
    setPermission("denied");
  }, []);

  // Detect tasks transitioning to "review" and fire notifications
  useEffect(() => {
    if (tasks.length === 0) return;

    const prevMap = prevStatusMapRef.current;

    // On initial load, just populate the map without notifying
    if (isInitialLoadRef.current) {
      isInitialLoadRef.current = false;
      for (const task of tasks) {
        prevMap.set(task.id, task.status);
      }
      return;
    }

    // Find tasks that just transitioned to "review"
    const newReviewTasks: Task[] = [];
    for (const task of tasks) {
      const prevStatus = prevMap.get(task.id);
      if (task.status === "review" && prevStatus !== undefined && prevStatus !== "review") {
        newReviewTasks.push(task);
      }
    }

    // Update the map with current statuses
    prevMap.clear();
    for (const task of tasks) {
      prevMap.set(task.id, task.status);
    }

    // Fire notifications
    if (permission !== "granted" || newReviewTasks.length === 0) return;

    for (const task of newReviewTasks) {
      const confidence = getLatestConfidence(task);
      const body = confidence
        ? `${task.title} (confidence: ${confidence}) — Click to review`
        : `${task.title} — Click to review`;

      const notification = new Notification("Task Ready for Review", { body, tag: `task-review-${task.id}` });

      notification.onclick = () => {
        window.focus();
        notification.close();
      };
    }
  }, [tasks, permission]);

  return {
    permission,
    showBanner: permission === "default",
    requestPermission,
    dismissBanner,
  };
}

/** Extract the latest confidence score from reviewed_by entries */
function getLatestConfidence(task: Task): string | null {
  if (!task.reviewed_by || task.reviewed_by.length === 0) return null;
  const last = task.reviewed_by[task.reviewed_by.length - 1];
  if (last.confidence == null) return null;
  return `${Math.round(last.confidence * 100)}%`;
}

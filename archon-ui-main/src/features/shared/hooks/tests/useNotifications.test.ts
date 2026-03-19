import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Task } from "../../../projects/tasks/types";
import { useNotifications } from "../useNotifications";

// Mock localStorage
const mockStorage = new Map<string, string>();
const mockLocalStorage = {
  getItem: vi.fn((key: string) => mockStorage.get(key) ?? null),
  setItem: vi.fn((key: string, value: string) => mockStorage.set(key, value)),
  removeItem: vi.fn((key: string) => mockStorage.delete(key)),
  clear: vi.fn(() => mockStorage.clear()),
  get length() {
    return mockStorage.size;
  },
  key: vi.fn(() => null),
};

// Mock Notification API
const mockNotificationInstance = { onclick: null as (() => void) | null, close: vi.fn() };
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const MockNotification: any = vi.fn(() => mockNotificationInstance);
Object.defineProperty(MockNotification, "permission", { value: "default", writable: true, configurable: true });
MockNotification.requestPermission = vi.fn(async () => "granted" as NotificationPermission);

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "task-1",
    project_id: "proj-1",
    title: "Fix auth bug",
    description: "",
    status: "executing",
    assignee: "Coding Agent",
    task_order: 100,
    priority: "medium",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

describe("useNotifications", () => {
  beforeEach(() => {
    vi.stubGlobal("Notification", MockNotification);
    vi.stubGlobal("localStorage", mockLocalStorage);
    Object.defineProperty(MockNotification, "permission", { value: "default", writable: true, configurable: true });
    MockNotification.mockClear();
    MockNotification.requestPermission.mockClear();
    MockNotification.requestPermission.mockResolvedValue("granted" as NotificationPermission);
    mockNotificationInstance.close.mockClear();
    mockNotificationInstance.onclick = null;
    mockStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("should show banner when permission is default", () => {
    const { result } = renderHook(() => useNotifications([]));
    expect(result.current.showBanner).toBe(true);
    expect(result.current.permission).toBe("default");
  });

  it("should hide banner when permission is granted", () => {
    Object.defineProperty(MockNotification, "permission", { value: "granted", configurable: true });
    const { result } = renderHook(() => useNotifications([]));
    expect(result.current.showBanner).toBe(false);
    expect(result.current.permission).toBe("granted");
  });

  it("should hide banner when permission is denied", () => {
    Object.defineProperty(MockNotification, "permission", { value: "denied", configurable: true });
    const { result } = renderHook(() => useNotifications([]));
    expect(result.current.showBanner).toBe(false);
  });

  it("should request permission and update state", async () => {
    const { result } = renderHook(() => useNotifications([]));

    await act(async () => {
      await result.current.requestPermission();
    });

    expect(MockNotification.requestPermission).toHaveBeenCalled();
    expect(result.current.permission).toBe("granted");
    expect(result.current.showBanner).toBe(false);
  });

  it("should dismiss banner without requesting permission", () => {
    MockNotification.requestPermission.mockClear();
    const { result } = renderHook(() => useNotifications([]));
    expect(result.current.showBanner).toBe(true);

    act(() => {
      result.current.dismissBanner();
    });

    expect(result.current.showBanner).toBe(false);
    expect(MockNotification.requestPermission).not.toHaveBeenCalled();
    expect(localStorage.getItem("archon:notification-banner-dismissed")).toBe("1");
  });

  it("should persist dismiss across remounts", () => {
    const { result, unmount } = renderHook(() => useNotifications([]));
    expect(result.current.showBanner).toBe(true);

    act(() => {
      result.current.dismissBanner();
    });
    expect(result.current.showBanner).toBe(false);
    unmount();

    // Remount — banner should stay hidden
    const { result: result2 } = renderHook(() => useNotifications([]));
    expect(result2.current.showBanner).toBe(false);
  });

  it("should clear dismiss flag when requesting permission", async () => {
    localStorage.setItem("archon:notification-banner-dismissed", "1");
    const { result } = renderHook(() => useNotifications([]));
    expect(result.current.showBanner).toBe(false);

    await act(async () => {
      await result.current.requestPermission();
    });

    expect(localStorage.getItem("archon:notification-banner-dismissed")).toBeNull();
  });

  it("should not notify on initial task load", () => {
    Object.defineProperty(MockNotification, "permission", { value: "granted", configurable: true });
    const tasks = [makeTask({ status: "review" })];

    renderHook(() => useNotifications(tasks));

    // Should not fire notification on first render even if tasks are in review
    expect(MockNotification).not.toHaveBeenCalled();
  });

  it("should notify when task transitions to review", () => {
    Object.defineProperty(MockNotification, "permission", { value: "granted", configurable: true });
    const doingTask = makeTask({ status: "executing" });

    const { rerender } = renderHook(({ tasks }) => useNotifications(tasks), {
      initialProps: { tasks: [doingTask] },
    });

    // Transition to review
    const reviewTask = { ...doingTask, status: "review" as const };
    rerender({ tasks: [reviewTask] });

    expect(MockNotification).toHaveBeenCalledWith("Task Ready for Review", {
      body: "Fix auth bug — Click to review",
      tag: "task-review-task-1",
    });
  });

  it("should include confidence score in notification body", () => {
    Object.defineProperty(MockNotification, "permission", { value: "granted", configurable: true });
    const doingTask = makeTask({ status: "executing" });

    const { rerender } = renderHook(({ tasks }) => useNotifications(tasks), {
      initialProps: { tasks: [doingTask] },
    });

    const reviewTask = {
      ...doingTask,
      status: "review" as const,
      reviewed_by: [{ stage: "architect", agent: "ai", model: "gpt-4", actor: "system", confidence: 0.87 }],
    };
    rerender({ tasks: [reviewTask] });

    expect(MockNotification).toHaveBeenCalledWith("Task Ready for Review", {
      body: "Fix auth bug (confidence: 87%) — Click to review",
      tag: "task-review-task-1",
    });
  });

  it("should not notify when permission is not granted", () => {
    // permission stays "default"
    const doingTask = makeTask({ status: "executing" });

    const { rerender } = renderHook(({ tasks }) => useNotifications(tasks), {
      initialProps: { tasks: [doingTask] },
    });

    rerender({ tasks: [{ ...doingTask, status: "review" as const }] });

    expect(MockNotification).not.toHaveBeenCalled();
  });

  it("should not notify when task stays in same status", () => {
    Object.defineProperty(MockNotification, "permission", { value: "granted", configurable: true });
    const reviewTask = makeTask({ status: "review" });

    const { rerender } = renderHook(({ tasks }) => useNotifications(tasks), {
      initialProps: { tasks: [reviewTask] },
    });

    // Re-render with same status
    rerender({ tasks: [{ ...reviewTask, updated_at: new Date().toISOString() }] });

    expect(MockNotification).not.toHaveBeenCalled();
  });

  it("should focus window when notification is clicked", () => {
    Object.defineProperty(MockNotification, "permission", { value: "granted", configurable: true });
    const focusSpy = vi.spyOn(window, "focus").mockImplementation(() => {});

    const doingTask = makeTask({ status: "executing" });
    const { rerender } = renderHook(({ tasks }) => useNotifications(tasks), {
      initialProps: { tasks: [doingTask] },
    });

    rerender({ tasks: [{ ...doingTask, status: "review" as const }] });

    // Simulate click
    expect(mockNotificationInstance.onclick).not.toBeNull();
    mockNotificationInstance.onclick!();

    expect(focusSpy).toHaveBeenCalled();
    expect(mockNotificationInstance.close).toHaveBeenCalled();

    focusSpy.mockRestore();
  });

  it("should handle Notification API not available", () => {
    vi.unstubAllGlobals();
    // @ts-expect-error -- deliberately removing Notification
    delete globalThis.Notification;

    const { result } = renderHook(() => useNotifications([]));
    expect(result.current.permission).toBe("denied");
    expect(result.current.showBanner).toBe(false);
  });

  it("should handle multiple tasks transitioning to review", () => {
    Object.defineProperty(MockNotification, "permission", { value: "granted", configurable: true });
    const task1 = makeTask({ id: "t1", title: "Task One", status: "executing" });
    const task2 = makeTask({ id: "t2", title: "Task Two", status: "executing" });

    const { rerender } = renderHook(({ tasks }) => useNotifications(tasks), {
      initialProps: { tasks: [task1, task2] },
    });

    rerender({
      tasks: [
        { ...task1, status: "review" as const },
        { ...task2, status: "review" as const },
      ],
    });

    expect(MockNotification).toHaveBeenCalledTimes(2);
  });
});

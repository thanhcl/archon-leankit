import { AlertTriangle, Link } from "lucide-react";
import { Button, Card } from "../../../ui/primitives";
import { cn } from "../../../ui/primitives/styles";
import { useAutoLinkTasks } from "../hooks/usePlanQueries";

// The task type from generated API contracts has plan_item_id
interface TaskWithPlanItem {
  id: string;
  title: string;
  status: string;
  assignee?: string;
  plan_item_id?: string | null;
}

interface UnmappedTaskReportProps {
  tasks: TaskWithPlanItem[];
  projectId: string;
  isLoading?: boolean;
}

export function UnmappedTaskReport({ tasks, projectId, isLoading }: UnmappedTaskReportProps) {
  const autoLink = useAutoLinkTasks();

  const unmapped = tasks.filter((t) => !t.plan_item_id);
  const total = tasks.length;

  const handleAutoLink = () => {
    autoLink.mutate(projectId);
  };

  if (isLoading) {
    return (
      <Card blur="md" transparency="light" size="sm" className="border-white/10 dark:border-white/[0.06]">
        <div className="h-16 flex items-center text-xs text-gray-500 dark:text-gray-400">Loading task data…</div>
      </Card>
    );
  }

  return (
    <Card blur="md" transparency="light" size="sm" className="border-white/10 dark:border-white/[0.06]">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">Unmapped Tasks</h3>
          {unmapped.length > 0 && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-400">
              <AlertTriangle className="w-2.5 h-2.5" />
              {unmapped.length}
            </span>
          )}
        </div>

        {unmapped.length > 0 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={handleAutoLink}
            disabled={autoLink.isPending}
            className="text-xs h-7 gap-1"
          >
            <Link className="w-3 h-3" />
            Auto-link
          </Button>
        )}
      </div>

      <p className="text-[11px] text-gray-500 dark:text-gray-400 mb-3">
        {unmapped.length === 0
          ? `All ${total} tasks are mapped to plan items.`
          : `${unmapped.length} of ${total} tasks have no plan item (plan_item_id is null). Auto-link scans task descriptions for "Ref: <item_key>" patterns.`}
      </p>

      {unmapped.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-white/10 dark:border-white/[0.08]">
                {["Title", "Status", "Assignee"].map((h) => (
                  <th
                    key={h}
                    className="text-left pb-1.5 pr-4 text-[10px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {unmapped.slice(0, 20).map((task) => (
                <tr
                  key={task.id}
                  className={cn(
                    "border-b border-white/5 dark:border-white/[0.04]",
                    "hover:bg-white/5 dark:hover:bg-white/[0.03] transition-colors",
                  )}
                >
                  <td className="py-1.5 pr-4 text-gray-700 dark:text-gray-300 max-w-[200px]">
                    <span className="block truncate">{task.title}</span>
                  </td>
                  <td className="py-1.5 pr-4">
                    <span className="px-1.5 py-0.5 rounded text-[10px] bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400">
                      {task.status}
                    </span>
                  </td>
                  <td className="py-1.5 text-gray-500 dark:text-gray-400">{task.assignee ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {unmapped.length > 20 && (
            <p className="mt-1.5 text-[10px] text-gray-400 dark:text-gray-600">…and {unmapped.length - 20} more</p>
          )}
        </div>
      )}
    </Card>
  );
}

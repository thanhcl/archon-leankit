import { CheckCircle2, Loader2 } from "lucide-react";
import type React from "react";
import { useId, useState } from "react";
import { Button } from "../../ui/primitives/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../ui/primitives/dialog";
import { Input } from "../../ui/primitives/input";
import { cn } from "../../ui/primitives/styles";
import { useCreateProject } from "../hooks/useProjectQueries";
import { useProjectTemplates } from "../hooks/useProjectTemplateQueries";
import type { ProjectTemplate } from "../services/projectTemplateService";
import type { CreateProjectRequest } from "../types";

interface NewProjectModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess?: () => void;
}

export const NewProjectModal: React.FC<NewProjectModalProps> = ({ open, onOpenChange, onSuccess }) => {
  const projectNameId = useId();
  const projectDescriptionId = useId();

  const [formData, setFormData] = useState<CreateProjectRequest>({
    title: "",
    description: "",
  });
  const [selectedTemplate, setSelectedTemplate] = useState<ProjectTemplate | null>(null);

  const createProjectMutation = useCreateProject();
  const { data: templates = [], isLoading: isLoadingTemplates } = useProjectTemplates();

  const handleTemplateSelect = (template: ProjectTemplate | null) => {
    setSelectedTemplate(template);
    if (template) {
      setFormData((prev) => ({
        ...prev,
        bootstrap_template: template.id,
        project_type: template.project_type,
        bootstrap_policy: template.bootstrap_policy,
        bootstrap_architect_provider: template.bootstrap_architect_provider ?? undefined,
      }));
    } else {
      setFormData((prev) => ({
        ...prev,
        bootstrap_template: undefined,
        project_type: undefined,
        bootstrap_policy: undefined,
        bootstrap_architect_provider: undefined,
      }));
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!formData.title.trim()) return;

    createProjectMutation.mutate(formData, {
      onSuccess: () => {
        setFormData({ title: "", description: "" });
        setSelectedTemplate(null);
        onOpenChange(false);
        onSuccess?.();
      },
    });
  };

  const handleClose = () => {
    if (!createProjectMutation.isPending) {
      setFormData({ title: "", description: "" });
      setSelectedTemplate(null);
      onOpenChange(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle className="text-xl font-bold bg-gradient-to-r from-purple-400 to-fuchsia-500 text-transparent bg-clip-text">
              Create New Project
            </DialogTitle>
            <DialogDescription>Start a new project to organize your tasks and documents.</DialogDescription>
          </DialogHeader>

          <div className="space-y-4 my-6">
            <div>
              <label
                htmlFor={projectNameId}
                className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
              >
                Project Name
              </label>
              <Input
                id={projectNameId}
                type="text"
                placeholder="Enter project name..."
                value={formData.title}
                onChange={(e) => setFormData((prev) => ({ ...prev, title: e.target.value }))}
                disabled={createProjectMutation.isPending}
                className={cn("w-full", "focus:border-purple-400 focus:shadow-[0_0_10px_rgba(168,85,247,0.2)]")}
                autoFocus
              />
            </div>

            <div>
              <label
                htmlFor={projectDescriptionId}
                className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
              >
                Description
              </label>
              <textarea
                id={projectDescriptionId}
                placeholder="Enter project description..."
                rows={3}
                value={formData.description}
                onChange={(e) =>
                  setFormData((prev) => ({
                    ...prev,
                    description: e.target.value,
                  }))
                }
                disabled={createProjectMutation.isPending}
                className={cn(
                  "w-full resize-none",
                  "bg-white/50 dark:bg-black/70",
                  "border border-gray-300 dark:border-gray-700",
                  "text-gray-900 dark:text-white",
                  "rounded-md py-2 px-3",
                  "focus:outline-none focus:border-purple-400",
                  "focus:shadow-[0_0_10px_rgba(168,85,247,0.2)]",
                  "transition-all duration-300",
                  "disabled:opacity-50 disabled:cursor-not-allowed",
                )}
              />
            </div>

            {/* Template Selection */}
            <div>
              <p className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                Project Template <span className="text-gray-400 font-normal">(optional)</span>
              </p>
              {isLoadingTemplates ? (
                <div className="flex items-center gap-2 text-sm text-gray-500">
                  <Loader2 className="w-3 h-3 animate-spin" />
                  Loading templates...
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-2">
                  {/* No template option */}
                  <button
                    type="button"
                    onClick={() => handleTemplateSelect(null)}
                    disabled={createProjectMutation.isPending}
                    className={cn(
                      "flex items-start gap-3 p-3 rounded-md border text-left transition-all",
                      "disabled:opacity-50 disabled:cursor-not-allowed",
                      selectedTemplate === null
                        ? "border-purple-500 bg-purple-500/10 text-purple-300"
                        : "border-gray-700 bg-black/30 text-gray-400 hover:border-gray-500 hover:text-gray-300",
                    )}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">Blank Project</span>
                        {selectedTemplate === null && <CheckCircle2 className="w-3.5 h-3.5 text-purple-400 shrink-0" />}
                      </div>
                      <p className="text-xs mt-0.5 opacity-70">Start from scratch with no pre-configured tasks.</p>
                    </div>
                  </button>

                  {templates.map((template) => (
                    <button
                      key={template.id}
                      type="button"
                      onClick={() => handleTemplateSelect(template)}
                      disabled={createProjectMutation.isPending}
                      className={cn(
                        "flex items-start gap-3 p-3 rounded-md border text-left transition-all",
                        "disabled:opacity-50 disabled:cursor-not-allowed",
                        selectedTemplate?.id === template.id
                          ? "border-purple-500 bg-purple-500/10 text-purple-300"
                          : "border-gray-700 bg-black/30 text-gray-400 hover:border-gray-500 hover:text-gray-300",
                      )}
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-gray-200">{template.name}</span>
                          {selectedTemplate?.id === template.id && (
                            <CheckCircle2 className="w-3.5 h-3.5 text-purple-400 shrink-0" />
                          )}
                        </div>
                        <p className="text-xs mt-0.5 text-gray-500">{template.description}</p>
                        <div className="flex flex-wrap gap-1 mt-1.5">
                          <span className="text-xs px-1.5 py-0.5 rounded bg-gray-800 text-gray-400">
                            {template.project_type}
                          </span>
                          <span className="text-xs px-1.5 py-0.5 rounded bg-gray-800 text-gray-400">
                            {template.task_pack.length} extra tasks
                          </span>
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={handleClose} disabled={createProjectMutation.isPending}>
              Cancel
            </Button>
            <Button
              type="submit"
              variant="default"
              disabled={createProjectMutation.isPending || !formData.title.trim()}
              className="shadow-lg shadow-purple-500/20"
            >
              {createProjectMutation.isPending ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Creating...
                </>
              ) : (
                "Create Project"
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};

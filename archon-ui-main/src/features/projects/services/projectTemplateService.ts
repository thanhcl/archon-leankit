/**
 * Project Template Service
 * Fetches available project templates from the backend.
 */

import { callAPIWithETag } from "../../shared/api/apiClient";

export interface ProjectTemplateTask {
  key: string;
  title: string;
  description: string;
  task_type: string;
  priority: string;
  complexity: string;
  tags: string[];
  blocked_on_key: string | null;
}

export interface ProjectTemplate {
  id: string;
  name: string;
  description: string;
  project_type: string;
  bootstrap_policy: string;
  bootstrap_architect_provider: string | null;
  default_model_routing: Record<string, unknown>;
  default_review_policy: Record<string, unknown>;
  task_pack: ProjectTemplateTask[];
  icon: string;
  color: string;
}

export interface ProjectTemplateListResponse {
  templates: ProjectTemplate[];
}

export const projectTemplateService = {
  async listTemplates(): Promise<ProjectTemplate[]> {
    const response = await callAPIWithETag<ProjectTemplateListResponse>("/api/project-templates");
    return response.templates ?? [];
  },

  async getTemplate(templateId: string): Promise<ProjectTemplate> {
    return callAPIWithETag<ProjectTemplate>(`/api/project-templates/${templateId}`);
  },
};

import { useQuery } from "@tanstack/react-query";
import { STALE_TIMES } from "../../shared/config/queryPatterns";
import type { ProjectTemplate } from "../services/projectTemplateService";
import { projectTemplateService } from "../services/projectTemplateService";

export const templateKeys = {
  all: ["project-templates"] as const,
  lists: () => [...templateKeys.all, "list"] as const,
  detail: (id: string) => [...templateKeys.all, "detail", id] as const,
};

export function useProjectTemplates() {
  return useQuery({
    queryKey: templateKeys.lists(),
    queryFn: () => projectTemplateService.listTemplates(),
    staleTime: STALE_TIMES.rare,
  });
}

export type { ProjectTemplate };

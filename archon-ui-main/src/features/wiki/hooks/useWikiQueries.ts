import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { wikiService } from "../services/wikiService";

const WIKI_KEYS = {
  all: ["wiki"] as const,
  pages: (projectId: string) => [...WIKI_KEYS.all, "pages", projectId] as const,
  page: (pageId: string) => [...WIKI_KEYS.all, "page", pageId] as const,
  search: (projectId: string, query: string) =>
    [...WIKI_KEYS.all, "search", projectId, query] as const,
  graph: (projectId: string) => [...WIKI_KEYS.all, "graph", projectId] as const,
  communities: (projectId: string) =>
    [...WIKI_KEYS.all, "communities", projectId] as const,
  lint: (projectId: string) => [...WIKI_KEYS.all, "lint", projectId] as const,
};

export function useWikiPages(projectId: string | undefined) {
  return useQuery({
    queryKey: WIKI_KEYS.pages(projectId || ""),
    queryFn: () => wikiService.getPages(projectId!),
    enabled: !!projectId,
    staleTime: 30_000,
  });
}

export function useWikiPage(pageId: string | undefined) {
  return useQuery({
    queryKey: WIKI_KEYS.page(pageId || ""),
    queryFn: () => wikiService.getPage(pageId!),
    enabled: !!pageId,
    staleTime: 60_000,
  });
}

export function useWikiSearch(projectId: string | undefined, query: string) {
  return useQuery({
    queryKey: WIKI_KEYS.search(projectId || "", query),
    queryFn: () => wikiService.searchPages(query, projectId!),
    enabled: !!projectId && query.length >= 2,
    staleTime: 10_000,
  });
}

export function useWikiGraph(projectId: string | undefined) {
  return useQuery({
    queryKey: WIKI_KEYS.graph(projectId || ""),
    queryFn: () => wikiService.getGraph(projectId!),
    enabled: !!projectId,
    staleTime: 60_000,
  });
}

export function useWikiCommunities(projectId: string | undefined) {
  return useQuery({
    queryKey: WIKI_KEYS.communities(projectId || ""),
    queryFn: () => wikiService.getCommunities(projectId!),
    enabled: !!projectId,
    staleTime: 60_000,
  });
}

export function useWikiLint(projectId: string | undefined) {
  return useQuery({
    queryKey: WIKI_KEYS.lint(projectId || ""),
    queryFn: () => wikiService.runLint(projectId!),
    enabled: false, // Manual trigger only
    staleTime: 0,
  });
}

export function useCreateNote(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { text: string; linkToSlugs?: string[]; tags?: string[] }) =>
      wikiService.createNote(projectId, vars.text, vars.linkToSlugs, vars.tags),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: WIKI_KEYS.pages(projectId) });
    },
  });
}

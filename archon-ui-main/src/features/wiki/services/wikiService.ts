import { API_BASE_URL } from "../../../config/api";
import { callAPIWithETag } from "../../shared/api/apiClient";
import type {
  WikiPage,
  WikiGraphData,
  WikiCommunity,
  WikiLintReport,
} from "../types";

export const wikiService = {
  async getPages(
    projectId: string,
    limit = 100,
  ): Promise<{ pages: WikiPage[]; count: number }> {
    return callAPIWithETag(
      `${API_BASE_URL}/wiki/pages?project_id=${projectId}&limit=${limit}`,
    );
  },

  async getPage(pageId: string): Promise<WikiPage> {
    return callAPIWithETag(
      `${API_BASE_URL}/wiki/pages/${pageId}?include_links=true`,
    );
  },

  async searchPages(
    query: string,
    projectId: string,
  ): Promise<{ results: WikiPage[]; count: number }> {
    return callAPIWithETag(
      `${API_BASE_URL}/wiki/search?query=${encodeURIComponent(query)}&project_id=${projectId}`,
    );
  },

  async getGraph(projectId: string): Promise<WikiGraphData> {
    return callAPIWithETag(
      `${API_BASE_URL}/wiki/graph?project_id=${projectId}`,
    );
  },

  async getCommunities(
    projectId: string,
  ): Promise<{ communities: WikiCommunity[]; total_communities: number }> {
    return callAPIWithETag(
      `${API_BASE_URL}/wiki/communities/${projectId}`,
    );
  },

  async runLint(projectId: string): Promise<WikiLintReport> {
    const resp = await fetch(`${API_BASE_URL}/wiki/lint/${projectId}`, {
      method: "POST",
    });
    if (!resp.ok) throw new Error(`Lint failed: ${resp.status}`);
    return resp.json();
  },

  async createNote(
    projectId: string,
    text: string,
    linkToSlugs?: string[],
    tags?: string[],
  ): Promise<WikiPage> {
    const resp = await fetch(`${API_BASE_URL}/wiki/notes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: projectId,
        text,
        link_to_slugs: linkToSlugs,
        tags,
      }),
    });
    if (!resp.ok) throw new Error(`Create note failed: ${resp.status}`);
    return resp.json();
  },

  getExportUrl(projectId: string): string {
    return `${API_BASE_URL}/wiki/export/obsidian/${projectId}`;
  },
};

export interface WikiPage {
  id: string;
  slug: string;
  title: string;
  summary: string | null;
  page_type: "entity" | "concept" | "synthesis" | "source_summary";
  category: string | null;
  tags: string; // JSON stringified array
  status: string;
  quality_score: number;
  community: string | null;
  updated_at: string;
  content?: string;
  links?: WikiPageLinks;
}

export interface WikiPageLinks {
  outbound: WikiLink[];
  inbound: WikiLink[];
}

export interface WikiLink {
  id: string;
  to_page_id?: string;
  from_page_id?: string;
  link_type: string;
  context: string | null;
  strength: number;
  confidence: string;
  created_by: string;
}

export interface WikiGraphData {
  nodes: WikiGraphNode[];
  edges: WikiGraphEdge[];
}

export interface WikiGraphNode {
  id: string;
  slug: string;
  title: string;
  page_type: string;
  status: string;
}

export interface WikiGraphEdge {
  from: string;
  to: string;
  type: string;
  strength: number;
}

export interface WikiCommunity {
  name: string;
  page_count: number;
  pages: { id: string; slug: string; title: string }[];
}

export interface WikiLintReport {
  orphans: { id: string; slug: string; title: string; page_type: string }[];
  stale: { id: string; slug: string; title: string; days_stale?: number }[];
  contradictions: { page_a: string; page_b: string; context: string | null }[];
  god_nodes: { id: string; slug: string; title: string; degree: number }[];
  surprise_connections: { from_page_id: string; to_page_id: string; from_community: string; to_community: string }[];
  broken_sources: { page_id: string; page_slug: string; missing_source_id: string }[];
  low_quality: { id: string; slug: string; title: string; quality_score: number }[];
  stats: Record<string, number>;
  total_issues: number;
}

export function parseTags(tags: string | string[]): string[] {
  if (Array.isArray(tags)) return tags;
  try {
    const parsed = JSON.parse(tags);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

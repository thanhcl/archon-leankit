import ReactMarkdown from "react-markdown";
import { parseTags, type WikiPage, type WikiLink } from "../types";

interface Props {
  page: WikiPage;
  allPages: WikiPage[];
  onNavigate: (pageId: string) => void;
}

export function WikiPageDetail({ page, allPages, onNavigate }: Props) {
  const tags = parseTags(page.tags);
  const pageMap = new Map(allPages.map((p) => [p.id, p]));

  const resolveTitle = (link: WikiLink, direction: "outbound" | "inbound") => {
    const targetId = direction === "outbound" ? link.to_page_id : link.from_page_id;
    return pageMap.get(targetId || "")?.title || (targetId || "").slice(0, 8);
  };

  return (
    <div className="h-full overflow-y-auto p-6">
      <h1 className="text-2xl font-bold text-zinc-100 mb-1">{page.title}</h1>

      <div className="flex flex-wrap gap-2 mb-4 text-xs">
        <span className="px-2 py-0.5 rounded bg-zinc-800 text-zinc-400">{page.page_type}</span>
        {page.category && (
          <span className="px-2 py-0.5 rounded bg-zinc-800 text-zinc-400">{page.category}</span>
        )}
        {page.community && (
          <span className="px-2 py-0.5 rounded bg-blue-900/40 text-blue-400">{page.community}</span>
        )}
        {tags.map((t) => (
          <span key={t} className="px-2 py-0.5 rounded bg-zinc-800 text-zinc-500">#{t}</span>
        ))}
        <span className="px-2 py-0.5 rounded bg-zinc-800 text-zinc-500">
          Quality: {((page.quality_score || 0) * 100).toFixed(0)}%
        </span>
      </div>

      <div className="prose prose-invert prose-sm max-w-none mb-8">
        <ReactMarkdown>{page.content || ""}</ReactMarkdown>
      </div>

      {page.links && (page.links.outbound.length > 0 || page.links.inbound.length > 0) && (
        <div className="border-t border-zinc-800 pt-4">
          {page.links.outbound.length > 0 && (
            <>
              <h3 className="text-xs font-medium text-zinc-500 uppercase tracking-wide mb-2">
                Outbound Links ({page.links.outbound.length})
              </h3>
              <div className="space-y-1 mb-4">
                {page.links.outbound.map((link) => (
                  <button
                    key={link.id}
                    type="button"
                    onClick={() => link.to_page_id && onNavigate(link.to_page_id)}
                    className="w-full text-left flex items-center gap-2 px-2 py-1.5 rounded hover:bg-zinc-800 text-sm"
                  >
                    <span className="text-zinc-500">-&gt;</span>
                    <span className="text-blue-400 hover:underline">{resolveTitle(link, "outbound")}</span>
                    <span className="text-zinc-600 text-xs ml-auto">{link.link_type} ({link.confidence})</span>
                  </button>
                ))}
              </div>
            </>
          )}

          {page.links.inbound.length > 0 && (
            <>
              <h3 className="text-xs font-medium text-zinc-500 uppercase tracking-wide mb-2">
                Backlinks ({page.links.inbound.length})
              </h3>
              <div className="space-y-1">
                {page.links.inbound.map((link) => (
                  <button
                    key={link.id}
                    type="button"
                    onClick={() => link.from_page_id && onNavigate(link.from_page_id)}
                    className="w-full text-left flex items-center gap-2 px-2 py-1.5 rounded hover:bg-zinc-800 text-sm"
                  >
                    <span className="text-zinc-500">&lt;-</span>
                    <span className="text-blue-400 hover:underline">{resolveTitle(link, "inbound")}</span>
                    <span className="text-zinc-600 text-xs ml-auto">{link.link_type}</span>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

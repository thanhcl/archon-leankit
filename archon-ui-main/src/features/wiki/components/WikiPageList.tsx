import { parseTags, type WikiPage } from "../types";

interface Props {
  pages: WikiPage[];
  selectedId: string | null;
  onSelect: (page: WikiPage) => void;
  searchQuery: string;
  onSearchChange: (q: string) => void;
}

const TYPE_COLORS: Record<string, string> = {
  entity: "bg-green-900/50 text-green-400",
  concept: "bg-purple-900/50 text-purple-400",
  synthesis: "bg-pink-900/50 text-pink-400",
  source_summary: "bg-orange-900/50 text-orange-400",
};

export function WikiPageList({ pages, selectedId, onSelect, searchQuery, onSearchChange }: Props) {
  const filtered = searchQuery
    ? pages.filter(
        (p) =>
          p.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
          parseTags(p.tags).some((t) => t.toLowerCase().includes(searchQuery.toLowerCase())),
      )
    : pages;

  return (
    <div className="flex flex-col h-full">
      <input
        type="text"
        placeholder="Search pages..."
        value={searchQuery}
        onChange={(e) => onSearchChange(e.target.value)}
        className="mb-3 px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-lg text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-blue-500"
      />
      <div className="text-xs text-zinc-500 mb-2">{filtered.length} pages</div>
      <div className="flex-1 overflow-y-auto space-y-1">
        {filtered.map((page) => (
          <button
            key={page.id}
            type="button"
            onClick={() => onSelect(page)}
            className={`w-full text-left p-2.5 rounded-lg border transition-colors ${
              selectedId === page.id
                ? "bg-zinc-800 border-blue-500/50"
                : "border-transparent hover:bg-zinc-800/50 hover:border-zinc-700"
            }`}
          >
            <div className="text-sm font-medium text-zinc-100 truncate">{page.title}</div>
            <div className="flex items-center gap-1.5 mt-1">
              <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${TYPE_COLORS[page.page_type] || "bg-zinc-800 text-zinc-400"}`}>
                {page.page_type}
              </span>
              {page.community && (
                <span className="text-[10px] text-zinc-500 truncate">{page.community}</span>
              )}
            </div>
            <div className="mt-1 h-1 bg-zinc-800 rounded-full overflow-hidden">
              <div
                className="h-full bg-green-500 rounded-full"
                style={{ width: `${(page.quality_score || 0) * 100}%` }}
              />
            </div>
          </button>
        ))}
        {filtered.length === 0 && (
          <div className="text-center text-zinc-500 text-sm py-8">No pages found</div>
        )}
      </div>
    </div>
  );
}

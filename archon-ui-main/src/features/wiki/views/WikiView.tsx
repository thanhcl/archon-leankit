import { useState } from "react";
import { useWikiPages, useWikiPage, useWikiLint } from "../hooks/useWikiQueries";
import { WikiPageList } from "../components/WikiPageList";
import { WikiPageDetail } from "../components/WikiPageDetail";
import { WikiHealthPanel } from "../components/WikiHealthPanel";
import { wikiService } from "../services/wikiService";
import type { WikiPage, WikiLintReport } from "../types";

// Hardcoded for now — can be made dynamic via project selector
const PROJECT_ID = "524dfa20-2a47-4c38-8d9a-bb75e7caaece";

type Tab = "pages" | "health";

export function WikiView() {
  const [tab, setTab] = useState<Tab>("pages");
  const [selectedPageId, setSelectedPageId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [lintReport, setLintReport] = useState<WikiLintReport | null>(null);
  const [lintLoading, setLintLoading] = useState(false);

  const { data: pagesData, isLoading } = useWikiPages(PROJECT_ID);
  const { data: selectedPage } = useWikiPage(selectedPageId || undefined);

  const pages = pagesData?.pages || [];

  const handleSelectPage = (page: WikiPage) => {
    setSelectedPageId(page.id);
  };

  const handleNavigate = (pageId: string) => {
    setSelectedPageId(pageId);
    setTab("pages");
  };

  const handleRunLint = async () => {
    setLintLoading(true);
    try {
      const report = await wikiService.runLint(PROJECT_ID);
      setLintReport(report);
    } catch (e) {
      console.error("Lint failed:", e);
    } finally {
      setLintLoading(false);
    }
  };

  const handleExport = () => {
    window.open(wikiService.getExportUrl(PROJECT_ID), "_blank");
  };

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-zinc-800">
        <div className="flex items-center gap-4">
          <h1 className="text-lg font-semibold text-zinc-100">Wiki KB</h1>
          <div className="flex gap-1 bg-zinc-800/50 rounded-lg p-0.5">
            {(["pages", "health"] as Tab[]).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTab(t)}
                className={`px-3 py-1 text-sm rounded-md transition-colors ${
                  tab === t
                    ? "bg-blue-600 text-white"
                    : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {t === "pages" ? "Pages" : "Health"}
              </button>
            ))}
          </div>
          <span className="text-xs text-zinc-500">{pages.length} pages</span>
        </div>
        <div className="flex gap-2">
          <a
            href="/api/wiki/viewer"
            target="_blank"
            rel="noopener noreferrer"
            className="px-3 py-1.5 text-sm bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg border border-zinc-700"
          >
            Open Viewer
          </a>
          <button
            type="button"
            onClick={handleExport}
            className="px-3 py-1.5 text-sm bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg border border-zinc-700"
          >
            Export Obsidian
          </button>
        </div>
      </div>

      {/* Content */}
      {tab === "pages" && (
        <div className="flex-1 grid grid-cols-[280px_1fr] overflow-hidden">
          <div className="border-r border-zinc-800 p-3 overflow-hidden">
            <WikiPageList
              pages={pages}
              selectedId={selectedPageId}
              onSelect={handleSelectPage}
              searchQuery={searchQuery}
              onSearchChange={setSearchQuery}
            />
          </div>
          <div className="overflow-hidden">
            {selectedPage ? (
              <WikiPageDetail
                page={selectedPage}
                allPages={pages}
                onNavigate={handleNavigate}
              />
            ) : (
              <div className="flex items-center justify-center h-full text-zinc-500">
                {isLoading ? "Loading..." : "Select a page to view"}
              </div>
            )}
          </div>
        </div>
      )}

      {tab === "health" && (
        <div className="flex-1 overflow-y-auto">
          <WikiHealthPanel
            report={lintReport}
            loading={lintLoading}
            onRunLint={handleRunLint}
          />
        </div>
      )}
    </div>
  );
}

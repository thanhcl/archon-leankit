/**
 * Knowledge Base Tab for Project Detail View
 * Provides CRUD + semantic search for knowledge items linked to a project
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Book, Loader2, Search, Trash2, Upload } from "lucide-react";
import { useCallback, useId, useRef, useState } from "react";
import { useToast } from "@/features/shared/hooks/useToast";
import { knowledgeKeys } from "../../knowledge/hooks";
import { KnowledgeInspector } from "../../knowledge/inspector/components/KnowledgeInspector";
import { knowledgeService } from "../../knowledge/services";
import type {
  DocumentChunk,
  KnowledgeItem,
  KnowledgeItemsFilter,
  SearchResultsResponse,
  UploadMetadata,
} from "../../knowledge/types";
import { DISABLED_QUERY_KEY, STALE_TIMES } from "../../shared/config/queryPatterns";
import { DeleteConfirmModal } from "../../ui/components/DeleteConfirmModal";
import { Button, Input } from "../../ui/primitives";
import { cn, glassCard } from "../../ui/primitives/styles";

interface KBTabProps {
  projectId: string;
}

export const KBTab = ({ projectId: _projectId }: KBTabProps) => {
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const fileInputId = useId();

  // State
  const [searchQuery, setSearchQuery] = useState("");
  const [searchSubmitted, setSearchSubmitted] = useState("");
  const [isDragOver, setIsDragOver] = useState(false);
  const [inspectorItem, setInspectorItem] = useState<KnowledgeItem | null>(null);
  const [deleteItem, setDeleteItem] = useState<KnowledgeItem | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Fetch knowledge sources list
  const filter: KnowledgeItemsFilter = { page: 1, per_page: 100 };
  const { data: sourcesData, isLoading: isLoadingSources } = useQuery({
    queryKey: knowledgeKeys.summaries(filter),
    queryFn: () => knowledgeService.getKnowledgeSummaries(filter),
    staleTime: STALE_TIMES.normal,
  });

  const sources = sourcesData?.items ?? [];

  // Semantic search
  const {
    data: searchResults,
    isLoading: isSearching,
    isFetching: isSearchFetching,
  } = useQuery<SearchResultsResponse>({
    queryKey: searchSubmitted ? knowledgeKeys.search(searchSubmitted) : DISABLED_QUERY_KEY,
    queryFn: () => knowledgeService.searchKnowledgeBase({ query: searchSubmitted, limit: 20 }),
    enabled: !!searchSubmitted,
    staleTime: STALE_TIMES.normal,
  });

  // Upload mutation
  const uploadMutation = useMutation({
    mutationFn: ({ file, metadata }: { file: File; metadata: UploadMetadata }) =>
      knowledgeService.uploadDocument(file, metadata),
    onSuccess: () => {
      showToast(`Upload started for processing`, "success");
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.summariesPrefix() });
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : "Upload failed";
      showToast(message, "error");
    },
  });

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: (sourceId: string) => knowledgeService.deleteKnowledgeItem(sourceId),
    onSuccess: (data) => {
      showToast(data.message || "Item deleted", "success");
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.summariesPrefix() });
      setDeleteItem(null);
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : "Delete failed";
      showToast(message, "error");
    },
  });

  // Handlers
  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (searchQuery.trim()) {
      setSearchSubmitted(searchQuery.trim());
    }
  };

  const handleClearSearch = () => {
    setSearchQuery("");
    setSearchSubmitted("");
  };

  const handleFileDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file) {
        uploadMutation.mutate({ file, metadata: { knowledge_type: "technical" } });
      }
    },
    [uploadMutation],
  );

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) {
        uploadMutation.mutate({ file, metadata: { knowledge_type: "technical" } });
      }
      // Reset so the same file can be re-selected
      if (fileInputRef.current) fileInputRef.current.value = "";
    },
    [uploadMutation],
  );

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
  };

  const handleSourceClick = (item: KnowledgeItem) => {
    setInspectorItem(item);
  };

  const handleDeleteClick = (e: React.MouseEvent, item: KnowledgeItem) => {
    e.stopPropagation();
    setDeleteItem(item);
  };

  const confirmDelete = () => {
    if (deleteItem) {
      deleteMutation.mutate(deleteItem.source_id);
    }
  };

  // Status badge helper
  const statusColor = (status: string) => {
    switch (status) {
      case "completed":
      case "active":
        return "bg-green-500/20 text-green-400";
      case "processing":
        return "bg-yellow-500/20 text-yellow-400";
      case "error":
        return "bg-red-500/20 text-red-400";
      default:
        return "bg-gray-500/20 text-gray-400";
    }
  };

  return (
    <div className="space-y-6">
      {/* Upload Drop Zone */}
      <button
        type="button"
        onDrop={handleFileDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        className={cn(
          "w-full relative rounded-xl border-2 border-dashed transition-all duration-200",
          "flex flex-col items-center justify-center gap-2 p-6 text-center cursor-pointer",
          glassCard.blur.md,
          isDragOver
            ? "border-cyan-400 bg-cyan-500/10 shadow-[0_0_20px_rgba(6,182,212,0.15)]"
            : "border-gray-300/40 dark:border-gray-600/40 hover:border-cyan-400/50",
          uploadMutation.isPending && "opacity-50 pointer-events-none",
        )}
        onClick={() => fileInputRef.current?.click()}
        aria-label="Drop files here or click to upload"
      >
        <input
          ref={fileInputRef}
          id={fileInputId}
          type="file"
          accept=".txt,.md,.pdf,.doc,.docx,.html,.htm"
          onChange={handleFileSelect}
          className="hidden"
          aria-label="Select file to upload"
        />
        {uploadMutation.isPending ? (
          <Loader2 className="w-8 h-8 text-cyan-400 animate-spin" />
        ) : (
          <Upload className={cn("w-8 h-8", isDragOver ? "text-cyan-400" : "text-gray-400 dark:text-gray-500")} />
        )}
        <p className="text-sm text-gray-600 dark:text-gray-300">
          {uploadMutation.isPending ? "Uploading..." : "Drop files here or click to upload"}
        </p>
        <p className="text-xs text-gray-400 dark:text-gray-500">PDF, DOC, DOCX, TXT, MD, HTML</p>
      </button>

      {/* Semantic Search */}
      <form onSubmit={handleSearchSubmit} className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <Input
            type="text"
            placeholder="Search knowledge base..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9"
            aria-label="Search knowledge base"
          />
        </div>
        <Button type="submit" disabled={!searchQuery.trim() || isSearchFetching} variant="outline" size="default">
          {isSearchFetching ? <Loader2 className="w-4 h-4 animate-spin" /> : "Search"}
        </Button>
        {searchSubmitted && (
          <Button type="button" variant="ghost" size="default" onClick={handleClearSearch}>
            Clear
          </Button>
        )}
      </form>

      {/* Search Results */}
      {searchSubmitted && (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">
            Search Results for "{searchSubmitted}"
            {searchResults && <span className="ml-2 text-gray-400">({searchResults.total} chunks)</span>}
          </h3>

          {isSearching ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-6 h-6 text-cyan-400 animate-spin" />
            </div>
          ) : searchResults?.results.length === 0 ? (
            <p className="text-sm text-gray-500 dark:text-gray-400 py-4 text-center">No results found</p>
          ) : (
            <div className="space-y-2 max-h-[400px] overflow-y-auto">
              {searchResults?.results.map((chunk: DocumentChunk, idx: number) => (
                <SearchResultCard key={chunk.id || idx} chunk={chunk} query={searchSubmitted} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Sources List */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Book className="w-4 h-4 text-gray-500 dark:text-gray-400" />
            <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">
              Knowledge Sources ({sources.length})
            </h3>
          </div>
        </div>

        {isLoadingSources ? (
          <div className="flex justify-center py-8">
            <Loader2 className="w-6 h-6 text-cyan-400 animate-spin" />
          </div>
        ) : sources.length === 0 ? (
          <div className="text-center py-8 text-gray-500 dark:text-gray-400">
            <Book className="w-12 h-12 mx-auto mb-3 opacity-30" />
            <p className="text-sm">No knowledge sources yet. Upload a document to get started.</p>
          </div>
        ) : (
          <div className="space-y-2">
            {sources.map((item) => (
              <button
                type="button"
                key={item.source_id}
                onClick={() => handleSourceClick(item)}
                className={cn(
                  "w-full text-left flex items-center justify-between p-3 rounded-lg cursor-pointer transition-all",
                  "border border-gray-200/30 dark:border-gray-700/30",
                  "hover:border-cyan-400/40 hover:bg-cyan-500/5",
                  glassCard.blur.sm,
                )}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-medium text-gray-800 dark:text-white truncate">{item.title}</p>
                    <span
                      className={cn("text-[10px] font-medium px-1.5 py-0.5 rounded-full", statusColor(item.status))}
                    >
                      {item.status}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 mt-1 text-xs text-gray-500 dark:text-gray-400">
                    <span>{item.source_type === "file" ? "File" : "URL"}</span>
                    <span>{item.document_count} chunks</span>
                    {item.code_examples_count > 0 && <span>{item.code_examples_count} code examples</span>}
                    <span>{item.knowledge_type}</span>
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={(e) => handleDeleteClick(e, item)}
                  className="text-gray-400 hover:text-red-500 ml-2 flex-shrink-0"
                  aria-label={`Delete ${item.title}`}
                >
                  <Trash2 className="w-4 h-4" />
                </Button>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Inspector Modal */}
      {inspectorItem && (
        <KnowledgeInspector
          item={inspectorItem}
          open={!!inspectorItem}
          onOpenChange={(open) => {
            if (!open) setInspectorItem(null);
          }}
          initialTab="documents"
        />
      )}

      {/* Delete Confirmation */}
      <DeleteConfirmModal
        open={!!deleteItem}
        onOpenChange={(open) => {
          if (!open) setDeleteItem(null);
        }}
        itemName={deleteItem?.title ?? ""}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteItem(null)}
        type="knowledge"
      />
    </div>
  );
};

/** Renders a single search result chunk with keyword highlighting */
function SearchResultCard({ chunk, query }: { chunk: DocumentChunk; query: string }) {
  const title = chunk.title || chunk.metadata?.title || "Untitled";
  const score = chunk.metadata?.relevance_score;

  // Highlight query words in content
  const highlightContent = (text: string, q: string) => {
    if (!q) return text;
    const words = q
      .split(/\s+/)
      .filter(Boolean)
      .map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    if (words.length === 0) return text;
    const regex = new RegExp(`(${words.join("|")})`, "gi");
    const parts = text.split(regex);
    return parts.map((part, i) => {
      // Use part content + position as key since parts from split are position-stable
      const key = `${part}-${i}`;
      return regex.test(part) ? (
        <mark key={key} className="bg-cyan-400/30 text-cyan-200 rounded px-0.5">
          {part}
        </mark>
      ) : (
        <span key={key}>{part}</span>
      );
    });
  };

  // Truncate content for display
  const displayContent = chunk.content?.slice(0, 300) || "";

  return (
    <div
      className={cn("p-3 rounded-lg border border-gray-200/20 dark:border-gray-700/20", "bg-white/5 dark:bg-black/10")}
    >
      <div className="flex items-center justify-between mb-1">
        <p className="text-sm font-medium text-gray-800 dark:text-white/90 truncate">{title}</p>
        {score != null && (
          <span className="text-[10px] font-mono text-cyan-400 bg-cyan-400/10 px-1.5 py-0.5 rounded ml-2 flex-shrink-0">
            {(score * 100).toFixed(0)}%
          </span>
        )}
      </div>
      <p className="text-xs text-gray-600 dark:text-gray-300 leading-relaxed line-clamp-3">
        {highlightContent(displayContent, query)}
        {chunk.content && chunk.content.length > 300 && "..."}
      </p>
      {chunk.url && <p className="text-[10px] text-gray-400 mt-1 truncate">{chunk.url}</p>}
    </div>
  );
}

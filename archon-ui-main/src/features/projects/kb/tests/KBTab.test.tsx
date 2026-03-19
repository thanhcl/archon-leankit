import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "../../../testing/test-utils";
import { KBTab } from "../KBTab";

// Mock knowledge service
vi.mock("../../../knowledge/services", () => ({
  knowledgeService: {
    getKnowledgeSummaries: vi.fn().mockResolvedValue({
      items: [
        {
          id: "1",
          source_id: "src-1",
          title: "Test Source",
          url: "https://example.com",
          source_type: "url",
          knowledge_type: "technical",
          status: "completed",
          document_count: 5,
          code_examples_count: 2,
          metadata: {},
          created_at: "2024-01-01T00:00:00Z",
          updated_at: "2024-01-01T00:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      per_page: 100,
    }),
    searchKnowledgeBase: vi.fn().mockResolvedValue({
      results: [
        {
          id: "chunk-1",
          source_id: "src-1",
          content: "This is a test chunk with relevant content about authentication.",
          title: "Auth Docs",
          metadata: { relevance_score: 0.85 },
        },
      ],
      total: 1,
      query: "auth",
    }),
    uploadDocument: vi.fn().mockResolvedValue({
      success: true,
      progressId: "prog-1",
      message: "Upload started",
      filename: "test.pdf",
    }),
    deleteKnowledgeItem: vi.fn().mockResolvedValue({
      success: true,
      message: "Item deleted successfully",
    }),
  },
}));

// Mock knowledge hooks
vi.mock("../../../knowledge/hooks", () => ({
  knowledgeKeys: {
    all: ["knowledge"] as const,
    summaries: (filter: unknown) => ["knowledge", "summaries", filter] as const,
    summariesPrefix: () => ["knowledge", "summaries"] as const,
    search: (query: string) => ["knowledge", "search", query] as const,
  },
}));

// Mock shared patterns
vi.mock("../../../shared/config/queryPatterns", () => ({
  DISABLED_QUERY_KEY: ["disabled"] as const,
  STALE_TIMES: {
    instant: 0,
    realtime: 3_000,
    frequent: 5_000,
    normal: 30_000,
    rare: 300_000,
    static: Infinity,
  },
  createRetryLogic: () => () => false,
}));

// Mock toast - preserve createToastContext and other exports for ToastProvider
vi.mock("@/features/shared/hooks/useToast", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/features/shared/hooks/useToast")>();
  return {
    ...actual,
    useToast: () => ({
      showToast: vi.fn(),
    }),
  };
});

// Mock KnowledgeInspector to avoid deep rendering
vi.mock("../../../knowledge/inspector/components/KnowledgeInspector", () => ({
  KnowledgeInspector: ({ item, open }: { item: { title: string }; open: boolean }) =>
    open ? <div data-testid="knowledge-inspector">{item.title}</div> : null,
}));

describe("KBTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the upload drop zone", () => {
    render(<KBTab projectId="proj-1" />);
    expect(screen.getByText("Drop files here or click to upload")).toBeInTheDocument();
    expect(screen.getByText("PDF, DOC, DOCX, TXT, MD, HTML")).toBeInTheDocument();
  });

  it("renders the search input", () => {
    render(<KBTab projectId="proj-1" />);
    expect(screen.getByPlaceholderText("Search knowledge base...")).toBeInTheDocument();
  });

  it("renders knowledge sources after loading", async () => {
    render(<KBTab projectId="proj-1" />);

    await waitFor(() => {
      expect(screen.getByText("Test Source")).toBeInTheDocument();
    });

    expect(screen.getByText("completed")).toBeInTheDocument();
    expect(screen.getByText("5 chunks")).toBeInTheDocument();
  });

  it("opens inspector when clicking a source", async () => {
    render(<KBTab projectId="proj-1" />);

    await waitFor(() => {
      expect(screen.getByText("Test Source")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Test Source"));

    await waitFor(() => {
      expect(screen.getByTestId("knowledge-inspector")).toBeInTheDocument();
    });
  });

  it("shows delete confirmation modal", async () => {
    render(<KBTab projectId="proj-1" />);

    await waitFor(() => {
      expect(screen.getByText("Test Source")).toBeInTheDocument();
    });

    const deleteButton = screen.getByLabelText("Delete Test Source");
    fireEvent.click(deleteButton);

    await waitFor(() => {
      expect(screen.getByText("Delete Knowledge Item")).toBeInTheDocument();
    });
  });

  it("submits search query", async () => {
    render(<KBTab projectId="proj-1" />);

    const searchInput = screen.getByPlaceholderText("Search knowledge base...");
    fireEvent.change(searchInput, { target: { value: "auth" } });

    const searchButton = screen.getByRole("button", { name: "Search" });
    fireEvent.click(searchButton);

    await waitFor(() => {
      expect(screen.getByText(/Search Results for/)).toBeInTheDocument();
    });
  });
});

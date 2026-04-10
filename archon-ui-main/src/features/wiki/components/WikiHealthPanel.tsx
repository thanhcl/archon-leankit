import type { WikiLintReport } from "../types";

interface Props {
  report: WikiLintReport | null;
  loading: boolean;
  onRunLint: () => void;
}

export function WikiHealthPanel({ report, loading, onRunLint }: Props) {
  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-zinc-100">Knowledge Health</h2>
        <button
          type="button"
          onClick={onRunLint}
          disabled={loading}
          className="px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg"
        >
          {loading ? "Running..." : "Run Lint"}
        </button>
      </div>

      {!report && !loading && (
        <div className="text-zinc-500 text-sm">Click "Run Lint" to analyze wiki health.</div>
      )}

      {report && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {Object.entries(report.stats).map(([key, val]) => (
              <div key={key} className="bg-zinc-800/50 rounded-lg p-3 border border-zinc-700/50">
                <div className="text-2xl font-bold text-zinc-100">{val}</div>
                <div className="text-xs text-zinc-500">{key.replace(/_/g, " ")}</div>
              </div>
            ))}
          </div>

          <Section title="God Nodes" items={report.god_nodes} color="text-orange-400">
            {(n) => `${n.title} (degree: ${n.degree})`}
          </Section>

          <Section title="Orphan Pages" items={report.orphans} color="text-yellow-400">
            {(n) => `${n.title} (${n.page_type})`}
          </Section>

          <Section title="Stale Pages" items={report.stale} color="text-red-400">
            {(n) => `${n.title}${n.days_stale ? ` (${n.days_stale}d)` : ""}`}
          </Section>

          <Section title="Low Quality" items={report.low_quality} color="text-zinc-400">
            {(n) => `${n.title} (${((n.quality_score || 0) * 100).toFixed(0)}%)`}
          </Section>

          <Section title="Surprise Connections" items={report.surprise_connections} color="text-purple-400">
            {(n) => `${n.from_community} <-> ${n.to_community}`}
          </Section>

          <Section title="Broken Sources" items={report.broken_sources} color="text-red-400">
            {(n) => `${n.page_slug} -> ${n.missing_source_id}`}
          </Section>
        </>
      )}
    </div>
  );
}

function Section<T>({
  title,
  items,
  color,
  children,
}: {
  title: string;
  items: T[];
  color: string;
  children: (item: T) => string;
}) {
  if (!items.length) return null;
  return (
    <div>
      <h3 className="text-sm font-medium text-zinc-400 mb-2">
        {title} ({items.length})
      </h3>
      <div className="space-y-1">
        {items.map((item, i) => (
          <div key={i} className={`text-sm ${color} bg-zinc-800/30 px-3 py-1.5 rounded`}>
            {children(item)}
          </div>
        ))}
      </div>
    </div>
  );
}

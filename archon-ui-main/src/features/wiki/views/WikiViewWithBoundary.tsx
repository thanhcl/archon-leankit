import { ErrorBoundaryWithBugReport } from "../../../components/bug-report/ErrorBoundaryWithBugReport";
import { WikiView } from "./WikiView";

export function WikiViewWithBoundary() {
  return (
    <ErrorBoundaryWithBugReport feature="wiki">
      <WikiView />
    </ErrorBoundaryWithBugReport>
  );
}

import { Link } from "react-router-dom";

/**
 * The three states every `useProject`-backed screen renders instead of the
 * fixture's synchronous, always-present data (UI-01). Same `panel`/`dim`/
 * `btn` tokens the rest of the app already uses (Report.tsx's own inline
 * loading/error markup, before this plan, is the pattern these formalise).
 *
 * T-16-02: `ErrorState` never renders the underlying error's message — only
 * a generic statement. The raw `Error` is accepted (for a caller that wants
 * to log it) but deliberately never interpolated into the DOM, so a raw
 * backend error body/stack trace is never what a user sees.
 */

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <section className="panel" data-state="loading">
      <p className="dim">{label}</p>
    </section>
  );
}

export function ErrorState({
  error,
  onRetry,
}: {
  error?: Error | null;
  onRetry?: () => void;
}) {
  void error; // accepted for callers that want to log it — never rendered (T-16-02)
  return (
    <section className="panel" data-state="error">
      <p>Something went wrong loading this. Try again in a moment.</p>
      {onRetry && (
        <button className="btn" data-variant="ghost" onClick={onRetry}>
          Try again
        </button>
      )}
    </section>
  );
}

export function NotFound({ slug }: { slug?: string }) {
  return (
    <section className="panel" data-state="notfound">
      <p>
        No project found{slug ? ` for “${slug}”` : ""}. It may not exist, or nothing has been
        scanned yet.
      </p>
      <Link to="/examples" className="btn" data-variant="ghost">
        Browse the examples
      </Link>
    </section>
  );
}

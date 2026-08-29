import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getFixes, getGaps, getQuestions, getReport } from "../api/client";
import type { Example } from "../data/types";

interface ProjectState {
  data: Example | null;
  loading: boolean;
  error: Error | null;
  notFound: boolean;
}

export interface UseProjectResult extends ProjectState {
  /**
   * 01-17: re-runs the exact same load `useProject` does on mount, without
   * a full page refresh. Every write-path screen (Rules add/patch/delete,
   * Questions confirm, Surfaces regenerate/toggle) calls this after its
   * mutation resolves, so the UI always reflects the persisted result
   * (UI-02) instead of a hand-rolled local merge that could drift from
   * what the server actually has on file — most importantly, a newly
   * raised follow-up question round (FOLLOWUP-01) only ever appears this
   * way, since the mutation response itself only describes the answer
   * just given. A no-op while `slug` is unset.
   */
  refetch: () => Promise<void>;
}

const INITIAL: ProjectState = { data: null, loading: true, error: null, notFound: false };

/**
 * The one hook every `/e/:slug/*` screen reads from (UI-01). Composes the
 * report (rules, surfaces, breaks, scan meta) with the three endpoints the
 * report always leaves empty — questions, gaps, fixes — into one
 * `Example`-shaped object, so every existing screen keeps reading `ex.*`
 * exactly like it did against the fixture.
 *
 * A 404 on the report (no such project) is the only thing that resolves to
 * `notFound` — that's the fixture lookup's old silent fallback-to-retail
 * bug, fixed. Questions/gaps/fixes are fetched best-effort: a failure on
 * any one of them (e.g. a fixes proposal call 402ing on an unfunded, non-
 * seeded project) degrades to an empty list for that field rather than
 * failing the whole page — the report is still real, and still worth
 * showing.
 */
export function useProject(slug: string | undefined): UseProjectResult {
  const [result, setResult] = useState<ProjectState>(INITIAL);
  const slugRef = useRef(slug);
  slugRef.current = slug;

  const load = useCallback(async (currentSlug: string, opts?: { silent?: boolean }) => {
    if (!opts?.silent) setResult((r) => ({ ...r, loading: true, error: null, notFound: false }));
    try {
      const report = await getReport(currentSlug);
      const [questions, gaps, fixes] = await Promise.all([
        getQuestions(currentSlug).catch(() => []),
        getGaps(currentSlug).catch(() => []),
        getFixes(currentSlug).catch(() => []),
      ]);
      if (slugRef.current !== currentSlug) return;
      const data: Example = { ...report, questions, gaps, fixes };
      setResult({ data, loading: false, error: null, notFound: false });
    } catch (err) {
      if (slugRef.current !== currentSlug) return;
      if (err instanceof ApiError && err.status === 404) {
        setResult({ data: null, loading: false, error: null, notFound: true });
      } else {
        setResult({
          data: null,
          loading: false,
          error: err instanceof Error ? err : new Error(String(err)),
          notFound: false,
        });
      }
    }
  }, []);

  useEffect(() => {
    if (!slug) {
      setResult({ data: null, loading: false, error: null, notFound: true });
      return;
    }
    setResult({ data: null, loading: true, error: null, notFound: false });
    void load(slug);
  }, [slug, load]);

  const refetch = useCallback(async () => {
    const current = slugRef.current;
    if (!current) return;
    // `silent`: keeps the current data on screen while refreshing rather
    // than flashing back to the full `Loading` state — a mutation just
    // succeeded, there is already something correct to show.
    await load(current, { silent: true });
  }, [load]);

  return { ...result, refetch };
}

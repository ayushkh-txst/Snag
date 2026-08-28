import { useEffect, useState } from "react";
import { ApiError, getFixes, getGaps, getQuestions, getReport } from "../api/client";
import type { Example } from "../data/types";

export interface UseProjectResult {
  data: Example | null;
  loading: boolean;
  error: Error | null;
  notFound: boolean;
}

const INITIAL: UseProjectResult = { data: null, loading: true, error: null, notFound: false };

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
  const [result, setResult] = useState<UseProjectResult>(INITIAL);

  useEffect(() => {
    if (!slug) {
      setResult({ data: null, loading: false, error: null, notFound: true });
      return;
    }

    let cancelled = false;
    setResult({ data: null, loading: true, error: null, notFound: false });

    async function load(currentSlug: string) {
      try {
        const report = await getReport(currentSlug);
        const [questions, gaps, fixes] = await Promise.all([
          getQuestions(currentSlug).catch(() => []),
          getGaps(currentSlug).catch(() => []),
          getFixes(currentSlug).catch(() => []),
        ]);
        if (cancelled) return;
        const data: Example = { ...report, questions, gaps, fixes };
        setResult({ data, loading: false, error: null, notFound: false });
      } catch (err) {
        if (cancelled) return;
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
    }

    void load(slug);
    return () => {
      cancelled = true;
    };
  }, [slug]);

  return result;
}

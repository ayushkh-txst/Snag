import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listExamples, type ExampleSummary } from "../api/client";
import { Arrow, Coverage, Pill } from "../components/ui";
import { SnagMark } from "../components/SnagMark";
import { ErrorState, Loading } from "../components/States";

export function Gallery() {
  const [examples, setExamples] = useState<ExampleSummary[] | null>(null);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;
    setExamples(null);
    setError(null);
    listExamples()
      .then((data) => {
        if (!cancelled) setExamples(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err : new Error(String(err)));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="shell gal">
      <header className="gal__head">
        <p className="eyebrow">pre-run examples</p>
        <h1 className="gal__h">Six prompts, scanned once and kept.</h1>
        <p className="gal__lede">
          Each of these was written with a known hole, or written properly on purpose. The
          reports are real scans against a live model, stored read-only — no model call
          happens when you open one, and there is nothing to sign up for. Every screen of
          the product is reachable inside them.
        </p>
      </header>

      {error && <ErrorState error={error} />}
      {!error && !examples && <Loading label="Loading examples…" />}

      {examples && (
        <div className="gal__list">
          {examples.map((ex) => {
            const held = ex.headline === "No breaks found yet.";
            return (
              <article className="galrow" key={ex.slug}>
                <div className="galrow__n">
                  <span className="mono">{String(ex.n).padStart(2, "0")}</span>
                </div>
                <div className="galrow__body">
                  <div className="galrow__top">
                    <h2 className="galrow__title">
                      <Link to={`/e/${ex.slug}/report`}>{ex.title}</Link>
                    </h2>
                    <Pill verdict={held ? "held" : "snagged"}>
                      {held ? "no breaks" : "broke"}
                    </Pill>
                  </div>
                  <span className="markwrap galrow__mark">
                    <SnagMark verdict={held ? "held" : "snagged"} />
                  </span>
                  <p className="galrow__head">{ex.headline}</p>
                  <p className="galrow__blurb dim">{ex.blurb}</p>

                  <div className="galrow__meta">
                    <span className="mono dimmer">{ex.model}</span>
                    <span className="mono dimmer">
                      {ex.scan.mode} · {ex.scan.repeats}× · {ex.scan.calls.toLocaleString()} calls
                      · ${ex.scan.cost.toFixed(2)}
                    </span>
                  </div>

                  <div className="galrow__cov">
                    <Coverage
                      total={ex.coverage.total}
                      testable={ex.coverage.testable}
                      eyes={ex.coverage.eyes}
                      compact
                    />
                  </div>

                  <nav className="galrow__links">
                    <Link to={`/e/${ex.slug}/rules`}>Rules</Link>
                    <Link to={`/e/${ex.slug}/surfaces`}>Surfaces</Link>
                    <Link to={`/e/${ex.slug}/report`}>Report</Link>
                    <Link to={`/e/${ex.slug}/gaps`}>Gaps</Link>
                    <Link to={`/e/${ex.slug}/fixes`}>Fixes</Link>
                  </nav>
                </div>
                <div className="galrow__walk">
                  <p className="label">demonstrates</p>
                  <p className="galrow__walktext">{ex.demonstrates}</p>
                  <Link to={`/e/${ex.slug}/report`} className="galrow__cta">
                    Open the report <Arrow />
                  </Link>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}

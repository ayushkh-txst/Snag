import { useParams } from "react-router-dom";
import { NextBar, StepHead } from "../components/Shell";
import { ErrorState, Loading, NotFound } from "../components/States";
import { useProject } from "../hooks/useProject";

const CHECKLIST = [
  "What to do when a tool fails",
  "What to do when a tool returns nothing",
  "Requests outside the product's scope",
  "Guessing when unsure",
  "Personal data appearing mid-conversation",
  "Hostile or abusive users",
  "Conflicting instructions",
  "Situations the rules simply do not cover",
];

function isCovered(g: { verdict: string; covered?: boolean }) {
  return g.covered ?? (g.verdict.startsWith("No gap") || g.verdict.startsWith("Covered"));
}

export function Gaps() {
  const { slug } = useParams();
  const { data: ex, loading, error, notFound } = useProject(slug);

  if (loading) return <Loading label="Loading gaps…" />;
  if (notFound) return <NotFound slug={slug} />;
  if (error) return <ErrorState error={error} />;
  if (!ex) return <Loading label="Loading gaps…" />;

  const covered = ex.gaps.filter(isCovered);

  return (
    <>
      <StepHead
        n="—"
        title="What your prompt never says."
        lede="Places your prompt is silent, so the model decided for itself. Probed during the same scan, one call per area. Not scored — just what happened."
        aside={
          <div className="qstat">
            <div className="qstat__n mono">{ex.gaps.length - covered.length}</div>
            <div className="qstat__l">of {CHECKLIST.length} checklist items uncovered</div>
          </div>
        }
      />

      <div className="gaps">
        {ex.gaps.map((g, i) => {
          const ok = isCovered(g);
          return (
            <article className="gap" key={g.id} data-ok={ok || undefined}>
              <div className="gap__n mono">{String(i + 1).padStart(2, "0")}</div>
              <div className="gap__body">
                <h2 className="gap__h">{g.item}</h2>
                <div className="gap__probe">
                  <span className="label">probe</span>
                  <p className="mono">{g.probe}</p>
                </div>
                <div className="gap__obs">
                  <span className="label">what it did</span>
                  <p>{g.observed}</p>
                </div>
                <p className="gap__verdict">{g.verdict}</p>
              </div>
            </article>
          );
        })}
      </div>

      <section className="checklist">
        <p className="eyebrow">every area Snag probes</p>
        <ul className="checklist__grid">
          {CHECKLIST.map((c) => {
            const tail = c.toLowerCase().split(" ").slice(-3).join(" ");
            const hit = ex.gaps.some((g) => g.item.toLowerCase().includes(tail));
            return (
              <li key={c} data-hit={hit || undefined}>
                <span className="mono">{hit ? "probed" : "no probe run"}</span>
                <span>{c}</span>
              </li>
            );
          })}
        </ul>
      </section>

      <NextBar
        back={`/e/${ex.slug}/report`}
        backLabel="Report"
        next={ex.fixes.length ? `/e/${ex.slug}/fixes` : `/e/${ex.slug}/history`}
        nextLabel={ex.fixes.length ? "See the suggested edits" : "Compare with earlier scans"}
      />
    </>
  );
}

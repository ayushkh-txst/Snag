import { useState } from "react";
import { useParams } from "react-router-dom";
import { NextBar, StepHead } from "../components/Shell";
import { SnagMark } from "../components/SnagMark";
import { ErrorState, Loading, NotFound } from "../components/States";
import { useProject } from "../hooks/useProject";

export function Fixes() {
  const { slug } = useParams();
  const { data: ex, loading, error, notFound } = useProject(slug);
  const [applied, setApplied] = useState<string[]>([]);

  if (loading) return <Loading label="Loading fixes…" />;
  if (notFound) return <NotFound slug={slug} />;
  if (error) return <ErrorState error={error} />;
  if (!ex) return <Loading label="Loading fixes…" />;

  if (ex.fixes.length === 0) {
    return (
      <>
        <StepHead
          n="—"
          title="Nothing to suggest."
          lede="One rule still breaks and no edit would close it. A list of allowed topics written in prose always has an edge; closing it properly means a classifier in front of the model, which is a change to your system, not your prompt."
        />
        <div className="nofix">
          <p className="nofix__q">
            An edit that does not verify is advice, and this tool does not give advice.
          </p>
        </div>
        <NextBar
          back={`/e/${ex.slug}/report`}
          backLabel="Report"
          next={`/e/${ex.slug}/history`}
          nextLabel="Compare with earlier scans"
        />
      </>
    );
  }

  return (
    <>
      <StepHead
        n="—"
        title="Specific text, then proof it worked."
        lede="Actual text, not advice. Applying one reruns the attacks that worked against the edited prompt. You apply the diff yourself — Snag never touches your prompt."
        aside={
          <div className="qstat">
            <div className="qstat__n mono">{applied.length}<span className="dimmer"> / {ex.fixes.length}</span></div>
            <div className="qstat__l">applied</div>
          </div>
        }
      />

      <div className="fixes">
        {ex.fixes.map((f, i) => {
          const rule = ex.rules.find((r) => r.id === f.ruleId)!;
          const on = applied.includes(f.id);
          return (
            <article className="fix" key={f.id} data-on={on || undefined}>
              <header className="fix__head">
                <span className="fix__n mono">{String(i + 1).padStart(2, "0")}</span>
                <div>
                  <span className="markwrap">
                    <h2 className="fix__h">{rule.text}</h2>
                    <SnagMark verdict={on ? "held" : "snagged"} />
                  </span>
                </div>
                <button
                  className="btn"
                  data-variant={on ? "ghost" : "solid"}
                  onClick={() =>
                    setApplied((a) => (on ? a.filter((x) => x !== f.id) : [...a, f.id]))
                  }
                >
                  {on ? "Applied — undo" : "Apply and verify"}
                </button>
              </header>

              <div className="diff">
                {f.removed.map((l, k) => (
                  <div className="diff__line" data-k="del" key={`d${k}`}>
                    <span className="diff__sign mono">−</span>
                    <span className="mono">{l}</span>
                  </div>
                ))}
                {f.added.map((l, k) => (
                  <div className="diff__line" data-k="add" key={`a${k}`}>
                    <span className="diff__sign mono">+</span>
                    <span className="mono">{l}</span>
                  </div>
                ))}
              </div>

              <p className="fix__why">{f.rationale}</p>

              <div className="verify" data-on={on || undefined}>
                <div className="verify__side">
                  <span className="label">before</span>
                  <span className="verify__v mono" data-tone="snagged">{f.before}</span>
                </div>
                <div className="verify__arrow" aria-hidden="true">
                  <svg width="34" height="10" viewBox="0 0 34 10">
                    <path d="M0 5h30M26 1l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.3" />
                  </svg>
                </div>
                <div className="verify__side">
                  <span className="label">after the edit, rerun</span>
                  <span className="verify__v mono" data-tone={on ? "held" : undefined}>
                    {on ? f.after : "not run yet"}
                  </span>
                </div>
                <p className="verify__note dim">
                  {on
                    ? "Only the attacks that succeeded were rerun, against the edited prompt. Nothing else was retested."
                    : "Apply the edit to rerun the attacks that succeeded. Snag will not show you a number it has not measured."}
                </p>
              </div>
            </article>
          );
        })}
      </div>

      <NextBar
        back={`/e/${ex.slug}/report`}
        backLabel="Report"
        next={`/e/${ex.slug}/history`}
        nextLabel="Compare with earlier scans"
      />
    </>
  );
}

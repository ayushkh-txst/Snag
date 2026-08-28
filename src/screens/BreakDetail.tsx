import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Marked, Pill } from "../components/ui";
import { CATEGORY_LABEL, byslug, type Turn } from "../data";

const ROLE_WORD: Record<Turn["role"], string> = {
  system: "system",
  user: "attacker",
  assistant: "model",
  tool_call: "tool call",
  tool_result: "tool result",
};

export function BreakDetail() {
  const { slug, breakId } = useParams();
  const ex = byslug(slug);
  const b = ex.breaks.find((x) => x.id === breakId);
  const [fp, setFp] = useState(b?.falsePositive ?? false);

  if (!b) {
    return (
      <p className="dim">
        No such break. <Link to={`/e/${ex.slug}/report`}>Back to the report</Link>.
      </p>
    );
  }

  const rule = ex.rules.find((r) => r.id === b.ruleId)!;
  const surface = ex.surfaces.find((s) => s.id === b.surfaceId);

  return (
    <>
      <nav className="crumbs">
        <Link to={`/e/${ex.slug}/report`}>← Report</Link>
        <span className="dimmer mono">{b.techniqueId}</span>
      </nav>

      <header className="bd__head">
        <div>
          <div className="bd__chips">
            <Pill verdict={fp ? "dead" : "snagged"}>
              {fp ? "excluded — false positive" : `broke ${b.hits} of ${b.repeats} runs`}
            </Pill>
            <span className="chip">{CATEGORY_LABEL[rule.category]}</span>
            <span className="chip" data-dir={rule.direction}>
              {rule.direction === "positive" ? "must refuse" : "must not break"}
            </span>
          </div>
          <h1 className="bd__h">{rule.text}</h1>
          <p className="bd__src mono">
            <span className="dimmer">source · </span>{rule.sourceLine}
          </p>
        </div>
        <dl className="bd__meta">
          <div><dt>Technique</dt><dd className="mono">{b.techniqueId}</dd></div>
          <div><dt>Family</dt><dd className="mono">{b.family}</dd></div>
          <div><dt>Surface</dt><dd className="mono">{surface?.path ?? "user message"}</dd></div>
          <div><dt>Rate</dt><dd className="mono">{b.hits} of {b.repeats}</dd></div>
        </dl>
      </header>

      <div className="bd__grid">
        <section className="transcript">
          <header className="panel__head">
            <span className="label">full conversation</span>
            <div className="panel__aside">
              <span className="tkey"><i data-k="planted" /> planted by the attack</span>
              <span className="tkey"><i data-k="evidence" /> what the checker matched</span>
            </div>
          </header>
          {b.turns.map((t, i) => (
            <article className="turn" key={i} data-role={t.role}>
              <div className="turn__role">
                <span className="mono">{ROLE_WORD[t.role]}</span>
                {t.name && <span className="mono turn__name">{t.name}</span>}
              </div>
              <div className="turn__body source">
                {t.planted ? (
                  <Marked text={t.content} mark={t.planted} kind="planted" />
                ) : t.evidence ? (
                  <Marked text={t.content} mark={t.evidence} kind="evidence" />
                ) : (
                  t.content
                )}
              </div>
            </article>
          ))}
        </section>

        <aside className="bd__side">
          <div className="checkout">
            <p className="label">checker output</p>
            <pre className="checkout__pre mono">{b.checkerOutput}</pre>
          </div>

          <div className="fpbox" data-on={fp || undefined}>
            <p className="label">is this real?</p>
            <p className="fpbox__b">
              Checkers are text searches, and text searches over-match. If the model did
              the right thing here and the checker misread it, say so.
            </p>
            <label className="check">
              <input type="checkbox" checked={fp} onChange={(e) => setFp(e.target.checked)} />
              <span>
                <strong>Not a real break.</strong> Comes out of the rate, and out of every
                rescan.
              </span>
            </label>
          </div>

          <nav className="bd__near">
            <p className="label">other breaks on this rule</p>
            {ex.breaks.filter((x) => x.ruleId === b.ruleId && x.id !== b.id).length === 0 ? (
              <p className="dim">None — this technique is the only one that worked.</p>
            ) : (
              ex.breaks
                .filter((x) => x.ruleId === b.ruleId && x.id !== b.id)
                .map((x) => (
                  <Link key={x.id} to={`/e/${ex.slug}/report/${x.id}`} className="bd__nearlink mono">
                    {x.techniqueId} · {x.hits}/{x.repeats}
                  </Link>
                ))
            )}
          </nav>
        </aside>
      </div>
    </>
  );
}

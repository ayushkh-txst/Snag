import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, getBreak, markFalsePositive } from "../api/client";
import { DisputedPill, Marked, Pill, TierPill } from "../components/ui";
import { ErrorState, Loading, NotFound } from "../components/States";
import { useProject } from "../hooks/useProject";
import { CATEGORY_LABEL, type Break, type Turn } from "../data";

const ROLE_WORD: Record<Turn["role"], string> = {
  system: "system",
  user: "attacker",
  assistant: "model",
  tool_call: "tool call",
  tool_result: "tool result",
};

export function BreakDetail() {
  const { slug, breakId } = useParams();
  const { data: ex, loading: exLoading, error: exError, notFound: exNotFound } = useProject(slug);

  const [b, setB] = useState<Break | null>(null);
  const [breakLoading, setBreakLoading] = useState(true);
  const [breakError, setBreakError] = useState<Error | null>(null);
  const [breakNotFound, setBreakNotFound] = useState(false);
  const [fp, setFp] = useState(false);
  const [run, setRun] = useState(0);

  useEffect(() => {
    if (!slug || !breakId) return;
    let cancelled = false;
    setB(null);
    setBreakLoading(true);
    setBreakError(null);
    setBreakNotFound(false);
    getBreak(slug, breakId)
      .then((data) => {
        if (cancelled) return;
        setB(data);
        setFp(data.falsePositive);
        const firstBroken = (data.variants ?? []).findIndex((v) => v.broke);
        setRun(Math.max(firstBroken, 0));
        setBreakLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) setBreakNotFound(true);
        else setBreakError(err instanceof Error ? err : new Error(String(err)));
        setBreakLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slug, breakId]);

  if (exLoading || breakLoading) return <Loading label="Loading break…" />;
  if (exNotFound || breakNotFound) return <NotFound slug={slug} />;
  if (exError) return <ErrorState error={exError} />;
  if (breakError) return <ErrorState error={breakError} />;
  if (!ex || !b) return <Loading label="Loading break…" />;

  const rule = ex.rules.find((r) => r.id === b.ruleId);
  const surface = ex.surfaces.find((s) => s.id === b.surfaceId);
  if (!rule) return <NotFound slug={slug} />;

  const variants = b.variants ?? [];
  const outcomes = variants.map((v) => v.broke);
  const current = variants[run];
  const broke = current?.broke ?? false;
  const turns = current?.turns ?? b.turns;
  const checkerOutput = current?.checkerOutput ?? b.checkerOutput;
  const tier = current?.verdictTier ?? b.verdictTier ?? "mechanical";
  const judged = tier === "judged";
  // The span the verdict rests on, for this run: the judge's own quote when it
  // originated the verdict, or the judge's counter-quote when it disputed a
  // checker's. Both are verbatim from the reply — that is the whole contract.
  const quote = current?.quote ?? b.quote;
  const disputed = current?.disputed ?? false;
  const disputeQuote = current?.disputeQuote ?? b.disputeQuote;
  const disputeNote = current?.disputeNote ?? b.disputeNote;
  const step = (dir: 1 | -1) =>
    setRun((n) => Math.min(Math.max(n + dir, 0), outcomes.length - 1));

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
            <TierPill tier={tier} />
            {disputed && <DisputedPill />}
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
          <div><dt>Verdict by</dt><dd className="mono">{judged ? "a model" : "a checker"}</dd></div>
        </dl>
      </header>

      <section className="runs">
        <header className="runs__head">
          <span className="label">every run of this attack</span>
          <div className="runs__nav">
            <button className="runs__step" onClick={() => step(-1)} disabled={run === 0} aria-label="Previous run">←</button>
            <span className="runs__now mono">
              run {run + 1} of {b.repeats} · <span data-broke={broke || undefined}>{broke ? "broke" : "held"}</span>
            </span>
            <button className="runs__step" onClick={() => step(1)} disabled={run === outcomes.length - 1} aria-label="Next run">→</button>
          </div>
        </header>
        <div className="runs__strip">
          {outcomes.map((o, i) => (
            <button
              key={i}
              className="runs__cell"
              data-broke={o || undefined}
              data-on={i === run || undefined}
              onClick={() => setRun(i)}
              aria-label={`Run ${i + 1} of ${b.repeats}, ${o ? "broke" : "held"}`}
              aria-pressed={i === run}
            />
          ))}
        </div>
        <p className="runs__note dim">
          Same attack, same wording, {b.repeats} times. Only the reply changed —{" "}
          {b.hits} of them broke the rule.
        </p>
      </section>

      <div className="bd__grid">
        <section className="transcript">
          <header className="panel__head">
            <span className="label">run {run + 1} of {b.repeats}</span>
            <div className="panel__aside">
              <span className="tkey"><i data-k="planted" /> planted by the attack</span>
              <span className="tkey"><i data-k="evidence" /> what the checker matched</span>
            </div>
          </header>
          {turns.map((t, i) => (
            <article className="turn" key={i} data-role={t.role} data-forged={t.forged || undefined}>
              <div className="turn__role">
                {/* A forged turn is text the ATTACK put in the model's mouth
                    (the prefill family) — never label it "model". */}
                <span className="mono">{t.forged ? "forged — not the model" : ROLE_WORD[t.role]}</span>
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
            <p className="label">
              {judged ? "judge output" : "checker output"} · run {run + 1}
            </p>
            <pre className="checkout__pre mono" data-held={!broke || undefined}>
              {checkerOutput}
            </pre>
          </div>

          {judged && quote && (
            <div className="quoted">
              <p className="label">the words this verdict rests on</p>
              <blockquote className="quoted__q source">“{quote}”</blockquote>
              <p className="quoted__b">
                Copied out of the reply above, character for character, and checked against it
                before it was stored. A verdict whose quote isn't really there is thrown away —
                so there is nothing here to take on trust.
              </p>
            </div>
          )}

          {disputed && (
            <div className="quoted" data-disputed>
              <p className="label">
                {broke ? "the judge thinks this isn't a break" : "the judge thinks this one broke"}
              </p>
              {disputeNote && <p className="quoted__b quoted__why">{disputeNote}</p>}
              {disputeQuote && (
                <blockquote className="quoted__q source">“{disputeQuote}”</blockquote>
              )}
              <p className="quoted__b">
                {broke
                  ? `The checker is a text search, and this reply reads to a stronger model like a
                     refusal it misread. The break is out of the rate but still here in full —
                     nothing was deleted, and you have the last word below.`
                  : `The checker found none of the words it was given, so it held. A stronger model
                     reading the same reply says the rule was broken anyway. It is deliberately not
                     counted as a break — a model's opinion isn't one — so this is here for your
                     eye, not for the number.`}
              </p>
            </div>
          )}

          <div className="fpbox" data-on={fp || undefined}>
            <p className="label">is this real?</p>
            <p className="fpbox__b">
              Checkers are text searches, and text searches over-match. If the model did
              the right thing here and the checker misread it, say so.
            </p>
            <label className="check">
              <input
                type="checkbox"
                checked={fp}
                onChange={(e) => {
                  const value = e.target.checked;
                  setFp(value);
                  if (!slug || !breakId) return;
                  markFalsePositive(slug, breakId, value).catch(() => setFp(!value));
                }}
              />
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

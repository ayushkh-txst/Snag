import type { ReactNode } from "react";
import type { Risk, Verdict, VerdictTier } from "../data";

export const VERDICT_WORD: Record<Verdict, string> = {
  held: "held",
  snagged: "snagged",
  eyes: "needs your eyes",
  dead: "not tested",
};

export function Pill({ verdict, children }: { verdict: Verdict; children?: ReactNode }) {
  return (
    <span className="pill" data-verdict={verdict}>
      {children ?? VERDICT_WORD[verdict]}
    </span>
  );
}

/**
 * Says so when a model, not a checker, decided this. A mechanical verdict needs
 * no label — it's the default and the trust anchor — so this renders nothing
 * for one rather than adding a badge to every row on the page.
 */
export function TierPill({ tier }: { tier?: VerdictTier }) {
  if (tier !== "judged") return null;
  return (
    <span title="No checker fits this rule, so a stronger model judged it — and had to quote the reply.">
      <Pill verdict="eyes">judged</Pill>
    </span>
  );
}

/** A break a checker found and the judge, on review, thinks it misread. */
export function DisputedPill() {
  return (
    <span title="A checker flagged this, and the judge thinks it misread the reply. You decide.">
      <Pill verdict="dead">disputed</Pill>
    </span>
  );
}

export function RiskDot({ risk, withLabel = false }: { risk: Risk; withLabel?: boolean }) {
  return (
    <span className="riskdot" data-risk={risk}>
      <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
        {risk === "none" ? (
          <circle cx="5" cy="5" r="3.2" fill="none" stroke="currentColor" strokeWidth="1.2" />
        ) : risk === "low" ? (
          <circle cx="5" cy="5" r="3.2" fill="currentColor" opacity="0.45" />
        ) : (
          <circle cx="5" cy="5" r="3.6" fill="currentColor" />
        )}
      </svg>
      {withLabel && <span>{risk}</span>}
    </span>
  );
}

export function Arrow() {
  return (
    <svg className="arrow" width="16" height="10" viewBox="0 0 16 10" aria-hidden="true">
      <path d="M0 5h14M10 1l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}

/** The coverage statement. It leads every report, by design. */
export function Coverage({
  total,
  testable,
  eyes,
  compact = false,
}: {
  total: number;
  testable: number;
  eyes: number;
  compact?: boolean;
}) {
  return (
    <div className="coverage" data-compact={compact || undefined}>
      <p className="coverage__line">
        <strong>{total} rules found.</strong> {testable} testable automatically.{" "}
        {eyes === 0 ? "None need your eyes." : `${eyes} ${eyes === 1 ? "rule needs" : "need"} your eyes.`}
      </p>
      <div className="coverage__bar" role="img" aria-label={`${testable} of ${total} rules testable by code`}>
        <span className="coverage__seg" data-kind="testable" style={{ flexGrow: testable }} />
        <span className="coverage__seg" data-kind="eyes" style={{ flexGrow: eyes }} />
      </div>
    </div>
  );
}

/** Renders text with one substring marked — planted attack text, or a checker's evidence. */
export function Marked({
  text,
  mark,
  kind,
}: {
  text: string;
  mark?: string;
  kind: "planted" | "evidence";
}) {
  if (!mark || !text.includes(mark)) return <>{text}</>;
  const i = text.indexOf(mark);
  return (
    <>
      {text.slice(0, i)}
      <mark className="tmark" data-kind={kind}>
        {mark}
      </mark>
      {text.slice(i + mark.length)}
    </>
  );
}

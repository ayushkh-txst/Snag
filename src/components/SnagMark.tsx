import { useEffect, useRef, useState } from "react";
import type { Verdict } from "../data";

/**
 * The snag mark. A stroke drawn beneath a rule that says what happened to it.
 *
 *   held    ── a taut line, unbroken
 *   snagged ── the line catches and pulls a loop where the rule gave way
 *   eyes    ── dashes, because nothing was measured here
 *   dead     ─ a faint hairline
 *
 * The straight run and the end cap are separate elements so the loop keeps its
 * shape at any width instead of stretching with the text above it.
 */

const LOOP =
  "M0 5 H7 C11 5 13 1.5 10.5 1.2 C8 0.9 8.5 9 13.5 8 C17 7.3 19.5 5 26 5";

export function SnagMark({
  verdict,
  delay = 0,
  play = true,
}: {
  verdict: Verdict;
  delay?: number;
  play?: boolean;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const [drawn, setDrawn] = useState(false);

  useEffect(() => {
    if (!play) return;
    const t = window.setTimeout(() => setDrawn(true), delay);
    return () => window.clearTimeout(t);
  }, [play, delay]);

  const stroke =
    verdict === "held" ? "var(--held)"
    : verdict === "snagged" ? "var(--snag)"
    : verdict === "eyes" ? "var(--eyes)"
    : "var(--dead)";

  const dash =
    verdict === "eyes" ? "3.5 3.5"
    : verdict === "dead" ? "1.5 3"
    : undefined;

  return (
    <span
      ref={ref}
      className="snagmark"
      data-verdict={verdict}
      data-drawn={drawn || undefined}
      aria-hidden="true"
    >
      <svg className="snagmark__run" height="14" preserveAspectRatio="none" viewBox="0 0 100 14">
        <path
          d="M0 5 H100"
          fill="none"
          stroke={stroke}
          strokeWidth="1.6"
          strokeDasharray={dash}
          strokeLinecap="butt"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      {verdict === "snagged" && (
        <svg className="snagmark__cap" width="26" height="14" viewBox="0 0 26 14">
          <path
            d={LOOP}
            fill="none"
            stroke={stroke}
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
            pathLength={1}
          />
        </svg>
      )}
    </span>
  );
}

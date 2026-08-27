import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { NextBar, StepHead } from "../components/Shell";
import { SCAN_MODES, byslug } from "../data";

const SURFACE_OPTS = [
  { id: "direct", label: "Direct injection", note: "The attack is in the user's own message." },
  { id: "tool", label: "Tool abuse", note: "Dangerous calls, and safe calls with attacker-written arguments. Both directions." },
  { id: "multiturn", label: "Multi-turn", note: "Built across a conversation. Default depth 3." },
  { id: "indirect", label: "Indirect injection", note: "Hidden in what the agent reads. Also covers empty, malformed and contradictory tool output." },
];

export function ScanConfig() {
  const { slug } = useParams();
  const ex = byslug(slug);
  const [mode, setMode] = useState("standard");
  const [repeats, setRepeats] = useState(3);
  const [on, setOn] = useState<string[]>(["direct", "tool"]);
  const [callCap, setCallCap] = useState(1500);
  const [spendCap, setSpendCap] = useState(3);

  const perSurface = 96;
  const estCalls = useMemo(() => on.length * perSurface * repeats * 0.42, [on, repeats]);
  const estCost = estCalls * 0.0013;

  const toggle = (id: string) =>
    setOn((xs) => (xs.includes(id) ? xs.filter((x) => x !== id) : [...xs, id]));

  return (
    <>
      <StepHead
        n="04"
        title="Decide what this is going to cost."
        lede="Models are random. An attack that fails once may land one time in five, so every attack runs more than once and the report shows a rate rather than a yes or no. Both caps below are enforced before a call is dispatched, not after."
      />

      <section className="modes">
        {SCAN_MODES.map((m) => (
          <label className="modecard" key={m.id} data-on={mode === m.id || undefined}>
            <input
              type="radio"
              name="mode"
              checked={mode === m.id}
              onChange={() => {
                setMode(m.id);
                if (m.id === "quick") { setOn(["direct"]); setRepeats(1); }
                if (m.id === "standard") { setOn(["direct", "tool"]); setRepeats(3); }
                if (m.id === "deep") { setOn(["direct", "tool", "multiturn", "indirect"]); setRepeats(5); }
              }}
            />
            <span className="modecard__name">{m.name}</span>
            <span className="modecard__figs mono">
              {m.calls} calls · {m.cost}
            </span>
            <span className="modecard__note">{m.note}</span>
          </label>
        ))}
      </section>

      <div className="cfggrid">
        <div>
          <p className="eyebrow">surfaces</p>
          <div className="surfopts">
            {SURFACE_OPTS.map((s) => (
              <label className="surfopt" key={s.id} data-on={on.includes(s.id) || undefined}>
                <input
                  type="checkbox"
                  checked={on.includes(s.id)}
                  onChange={() => { toggle(s.id); setMode("custom"); }}
                />
                <span>
                  <span className="surfopt__l">{s.label}</span>
                  <span className="surfopt__n">{s.note}</span>
                </span>
              </label>
            ))}
          </div>

          <p className="eyebrow" style={{ marginTop: "var(--s6)" }}>repeats</p>
          <div className="repeats">
            <input
              type="range"
              min={1}
              max={10}
              value={repeats}
              onChange={(e) => { setRepeats(+e.target.value); setMode("custom"); }}
            />
            <span className="repeats__v mono">{repeats}×</span>
          </div>
          <p className="dim repeats__note">
            {repeats === 1
              ? "One run per attack. The report will say the result is indicative only — a single run cannot produce a rate."
              : `Each attack runs ${repeats} times. The report shows how often it landed, not whether it landed.`}
          </p>
        </div>

        <aside className="budget">
          <p className="label">before dispatch</p>
          <div className="budget__row">
            <span>Hard call cap</span>
            <input
              className="budget__in mono"
              type="number"
              value={callCap}
              onChange={(e) => setCallCap(+e.target.value)}
            />
          </div>
          <div className="budget__row">
            <span>Hard spend cap</span>
            <span className="budget__money mono">
              $
              <input
                className="budget__in"
                type="number"
                step="0.5"
                value={spendCap}
                onChange={(e) => setSpendCap(+e.target.value)}
              />
            </span>
          </div>
          <p className="budget__note dim">
            A runaway scan is the obvious failure mode, so the cap is checked before each
            call is sent rather than totalled afterwards. The scan stops at the cap and
            tells you what it did not run.
          </p>

          <div className="budget__est">
            <div className="budget__estrow">
              <span>Estimated calls</span>
              <strong className="mono">{Math.round(estCalls).toLocaleString()}</strong>
            </div>
            <div className="budget__estrow">
              <span>Estimated spend</span>
              <strong className="mono">${estCost.toFixed(2)}</strong>
            </div>
            <div className="budget__estrow dim">
              <span>Model</span>
              <span className="mono">{ex.model}</span>
            </div>
          </div>
          <p className="budget__note dim">
            Estimates, not promises. The running total is shown live and the real figure
            appears in the report.
          </p>
        </aside>
      </div>

      <NextBar
        back={`/e/${ex.slug}/surfaces`}
        backLabel="Surfaces"
        next={`/e/${ex.slug}/scanning`}
        nextLabel="Start the scan"
        note={`${ex.rules.filter((r) => r.testable).length} rules will be tested. ${ex.rules.filter((r) => !r.testable).length} will be reported as needing your eyes.`}
      />
    </>
  );
}

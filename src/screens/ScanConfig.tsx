import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { estimateScan, startScan, type ScanMode } from "../api/client";
import { NextBar, StepHead } from "../components/Shell";
import { ErrorState, Loading, NotFound } from "../components/States";
import { useProject } from "../hooks/useProject";
import { SCAN_MODES } from "../data";

const SURFACE_OPTS = [
  { id: "direct", label: "Direct injection", note: "The attack is in the user's own message." },
  { id: "tool", label: "Tool abuse", note: "Dangerous calls, and safe calls with attacker-written arguments. Both directions." },
  { id: "multiturn", label: "Multi-turn", note: "Built across a conversation. Default depth 3." },
  { id: "indirect", label: "Indirect injection", note: "Hidden in what the agent reads. Also covers empty, malformed and contradictory tool output." },
];

const LAST_SCAN_KEY = (slug: string) => `snag:lastScan:${slug}`;

export function ScanConfig() {
  const { slug } = useParams();
  const nav = useNavigate();
  const { data: ex, loading, error, notFound } = useProject(slug);
  const [mode, setMode] = useState("standard");
  const [repeats, setRepeats] = useState(3);
  const [on, setOn] = useState<string[]>(["direct", "tool"]);
  const [callCap, setCallCap] = useState(1500);
  const [spendCap, setSpendCap] = useState(3);
  const [estimate, setEstimate] = useState<{ calls: number; cost: number } | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  const perSurface = 96;
  const roughCalls = useMemo(() => on.length * perSurface * repeats * 0.42, [on, repeats]);
  const roughCost = roughCalls * 0.0013;

  useEffect(() => {
    if (!slug) return;
    let cancelled = false;
    const apiMode: ScanMode = (mode || "custom") as ScanMode;
    estimateScan(slug, { mode: apiMode, surfaces: on, repeats })
      .then((r) => {
        if (cancelled) return;
        setEstimate({ calls: r.estimatedCalls, cost: r.estimatedCostUsd });
      })
      .catch(() => {
        if (cancelled) return;
        setEstimate(null);
      });
    return () => {
      cancelled = true;
    };
  }, [slug, mode, on, repeats]);

  if (loading) return <Loading label="Loading scan config…" />;
  if (notFound) return <NotFound slug={slug} />;
  if (error) return <ErrorState error={error} />;
  if (!ex) return <Loading label="Loading scan config…" />;

  const toggle = (id: string) =>
    setOn((xs) => (xs.includes(id) ? xs.filter((x) => x !== id) : [...xs, id]));

  const handleStart = () => {
    if (!slug || starting) return;
    setStarting(true);
    setStartError(null);
    const apiMode: ScanMode = (mode || "custom") as ScanMode;
    startScan(slug, { mode: apiMode, surfaces: on, repeats, callCap, spendCap })
      .then((result) => {
        try {
          localStorage.setItem(LAST_SCAN_KEY(slug), String(result.scanId));
        } catch {
          // private mode — Scanning.tsx falls back to router state only
        }
        nav(`/e/${slug}/scanning`, { state: { scanId: result.scanId } });
      })
      .catch(() => {
        setStartError("Couldn't start the scan. Check your key and budget, then try again.");
        setStarting(false);
      });
  };

  const displayCalls = estimate ? estimate.calls : Math.round(roughCalls);
  const displayCost = estimate ? estimate.cost : roughCost;

  return (
    <>
      <StepHead
        n="04"
        title="Decide what this is going to cost."
        lede="Models are random, so every attack runs more than once and you get a rate rather than a yes or no. Both caps stop the scan before a call is sent, not after."
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
                  onChange={() => { toggle(s.id); setMode(""); }}
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
              onChange={(e) => { setRepeats(+e.target.value); setMode(""); }}
            />
            <span className="repeats__v mono">{repeats}×</span>
          </div>
          <p className="dim repeats__note">
            {repeats === 1
              ? "One try each. The report will say so — one run can't give you a rate."
              : `Each attack is tried ${repeats} times.`}
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
            Checked before each call is sent. The scan stops at the cap and tells you what
            it didn't get to.
          </p>

          <div className="budget__est">
            <div className="budget__estrow">
              <span>Estimated calls</span>
              <strong className="mono">{displayCalls.toLocaleString()}</strong>
            </div>
            <div className="budget__estrow">
              <span>Estimated spend</span>
              <strong className="mono">${displayCost.toFixed(2)}</strong>
            </div>
            <div className="budget__estrow dim">
              <span>Model</span>
              <span className="mono">{ex.model}</span>
            </div>
          </div>
          <p className="budget__note dim">Live, from the server's own pricing lookup.</p>
        </aside>
      </div>

      <NextBar
        back={`/e/${ex.slug}/surfaces`}
        backLabel="Surfaces"
        nextLabel="Start the scan"
        onNext={handleStart}
        nextDisabled={starting}
        nextBusy={starting}
        note={startError ?? undefined}
      />
    </>
  );
}

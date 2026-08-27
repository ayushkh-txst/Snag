import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Arrow } from "../components/ui";
import { byslug } from "../data";

type Event = {
  ruleIdx: number;
  surfIdx: number;
  ruleId: string;
  surface: string;
  technique: string;
  broke: boolean;
};

const FAMILIES = [
  "instruction_override", "roleplay", "encoding", "context_switch", "authority_claim",
  "translation", "debug_pretext", "continuation", "payload_splitting", "obfuscation",
];

/** Deterministic — same prompt, same config, same attacks. Never Math.random(). */
function buildQueue(rules: { id: string; testable: boolean; attacks: number; breaks: number }[], surfaces: { id: string; path: string; userControlled: boolean }[]): Event[] {
  const active = surfaces.filter((s) => s.userControlled);
  const out: Event[] = [];
  rules.filter((r) => r.testable).forEach((r, ri) => {
    const n = Math.max(6, Math.round(r.attacks / 3));
    let left = r.breaks;
    for (let i = 0; i < n; i++) {
      const si = (ri * 3 + i * 5) % active.length;
      const broke = left > 0 && (i * 7 + ri) % 5 === 0;
      if (broke) left -= 1;
      out.push({
        ruleIdx: ri,
        surfIdx: si,
        ruleId: r.id,
        surface: active[si]?.path ?? "user message",
        technique: `${FAMILIES[(ri * 3 + i) % FAMILIES.length]}.${String((i * 13 + ri) % 40).padStart(2, "0")}`,
        broke,
      });
    }
  });
  return out;
}

export function Scanning() {
  const { slug } = useParams();
  const ex = byslug(slug);
  const testable = useMemo(() => ex.rules.filter((r) => r.testable), [ex]);
  const activeSurfaces = useMemo(() => ex.surfaces.filter((s) => s.userControlled), [ex]);
  const queue = useMemo(() => buildQueue(ex.rules, ex.surfaces), [ex]);

  const [i, setI] = useState(0);
  const [running, setRunning] = useState(true);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!running || i >= queue.length) return;
    const t = window.setTimeout(() => setI((n) => Math.min(n + 1, queue.length)), 46);
    return () => window.clearTimeout(t);
  }, [i, running, queue.length]);

  const done = i >= queue.length;
  const seen = queue.slice(0, i);
  const breaks = seen.filter((e) => e.broke).length;
  const pct = queue.length ? (i / queue.length) * 100 : 0;
  const calls = Math.round((i / queue.length) * ex.scan.calls) || 0;
  const cost = (ex.scan.cost * (i / (queue.length || 1)));

  const cellState = (ri: number, si: number) => {
    const rel = queue.filter((e) => e.ruleIdx === ri && e.surfIdx === si);
    if (!rel.length) return "none";
    const seenRel = seen.filter((e) => e.ruleIdx === ri && e.surfIdx === si);
    if (!seenRel.length) return "ahead";
    if (seenRel.some((e) => e.broke)) return "snagged";
    if (seenRel.length === rel.length) return "held";
    return "running";
  };

  return (
    <>
      <header className="scanhead">
        <div>
          <p className="eyebrow">step 05 — scanning</p>
          <h1 className="scanhead__h">
            {done ? "Scan complete." : "Running."}
          </h1>
          <p className="scanhead__sub mono">
            {ex.model} · {ex.scan.mode} · {ex.scan.repeats} repeats · {activeSurfaces.length} surfaces
          </p>
        </div>
        <div className="scanhead__acts">
          {done ? (
            <Link className="btn" data-variant="solid" to={`/e/${ex.slug}/report`}>
              Open the report <Arrow />
            </Link>
          ) : (
            <>
              <button className="btn" data-variant="ghost" onClick={() => setRunning((r) => !r)}>
                {running ? "Pause" : "Resume"}
              </button>
              <button className="btn" data-variant="quiet" onClick={() => setI(queue.length)}>
                Cancel and keep what's done
              </button>
            </>
          )}
        </div>
      </header>

      <div className="prog">
        <div className="prog__bar">
          <span className="prog__fill" style={{ width: `${pct}%` }} />
          <span className="prog__breaks" style={{ width: `${(breaks / (queue.length || 1)) * 100}%` }} />
        </div>
        <div className="statrow prog__stats">
          <div className="stat"><div className="stat__n">{i}<span className="stat__unit">/ {queue.length}</span></div><div className="stat__label">attacks run</div></div>
          <div className="stat" data-tone="snagged"><div className="stat__n">{breaks}</div><div className="stat__label">breaks found</div></div>
          <div className="stat"><div className="stat__n">{calls.toLocaleString()}</div><div className="stat__label">model calls</div></div>
          <div className="stat"><div className="stat__n">${cost.toFixed(2)}</div><div className="stat__label">spent so far</div></div>
          <div className="stat"><div className="stat__n">$3.00</div><div className="stat__label">hard cap</div></div>
        </div>
      </div>

      <section className="matrix">
        <div className="matrix__head">
          <span className="label">rule × surface</span>
          <div className="matrix__key">
            <span data-k="held">held</span>
            <span data-k="snagged">snagged</span>
            <span data-k="running">running</span>
            <span data-k="ahead">queued</span>
          </div>
        </div>
        <div className="matrix__scroll">
          <div
            className="matrix__grid"
            style={{ gridTemplateColumns: `minmax(150px, 1fr) repeat(${activeSurfaces.length}, 26px)` }}
          >
            <div className="matrix__corner" />
            {activeSurfaces.map((s) => (
              <div className="matrix__col mono" key={s.id} title={s.path}>
                <span>{s.path.length > 26 ? `${s.path.slice(0, 25)}…` : s.path}</span>
              </div>
            ))}
            {testable.map((r, ri) => (
              <div className="matrix__rowgroup" key={r.id} style={{ display: "contents" }}>
                <div className="matrix__rowlabel">
                  <span className="mono dimmer">{String(ri + 1).padStart(2, "0")}</span>
                  <span>{r.text}</span>
                </div>
                {activeSurfaces.map((s, si) => (
                  <div key={s.id} className="matrix__cell" data-state={cellState(ri, si)} />
                ))}
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="log">
        <div className="log__head">
          <span className="label">attack log</span>
          <span className="mono dimmer">{done ? "finished" : running ? "live" : "paused"}</span>
        </div>
        <div className="log__body" ref={logRef}>
          {seen.slice(-11).reverse().map((e, k) => (
            <div className="log__row" key={`${i}-${k}`} data-broke={e.broke || undefined}>
              <span className="mono log__rule">rule {String(e.ruleIdx + 1).padStart(2, "0")}</span>
              <span className="mono log__surf">{e.surface}</span>
              <span className="mono log__tech">{e.technique}</span>
              <span className="mono log__res">{e.broke ? "broke" : "held"}</span>
            </div>
          ))}
          {seen.length === 0 && <div className="log__row dim">Queuing attacks…</div>}
        </div>
      </section>

      {done && (
        <div className="scandone">
          <p>
            {breaks === 0
              ? "Nothing broke. That result is only as good as the coverage statement at the top of the report — read that first."
              : `${breaks} breaks across ${new Set(seen.filter((e) => e.broke).map((e) => e.ruleId)).size} rules. Every one has a full transcript.`}
          </p>
          <Link className="btn" data-variant="solid" to={`/e/${ex.slug}/report`}>
            Open the report <Arrow />
          </Link>
        </div>
      )}
    </>
  );
}

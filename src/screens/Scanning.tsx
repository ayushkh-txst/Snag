import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { Arrow } from "../components/ui";
import { ErrorState, Loading, NotFound } from "../components/States";
import { useProject } from "../hooks/useProject";
import { useScanStream } from "../hooks/useScanStream";
import {
  estimateScan,
  getActiveScan,
  getScan,
  type ScanMode,
  type ScanRecord,
} from "../api/client";

const LAST_SCAN_KEY = (slug: string) => `snag:lastScan:${slug}`;

function readInitialScanId(slug: string | undefined, stateScanId: unknown): number | null {
  if (typeof stateScanId === "number") return stateScanId;
  if (!slug) return null;
  try {
    const stored = localStorage.getItem(LAST_SCAN_KEY(slug));
    return stored ? Number(stored) : null;
  } catch {
    return null;
  }
}

export function Scanning() {
  const { slug } = useParams();
  const location = useLocation();
  const { data: ex, loading, error, notFound } = useProject(slug);

  const [scanId, setScanId] = useState<number | null>(() =>
    readInitialScanId(slug, (location.state as { scanId?: number } | null)?.scanId),
  );
  // Router state and localStorage only know about a scan this browser
  // started. A typed URL, a second tab, or another device has neither, and
  // the screen used to render "no scan in progress" — or worse, a finished
  // run — while the worker was still going. The project row is the durable
  // answer, so ask the server whenever the local hints come up empty.
  const [resolvingScan, setResolvingScan] = useState(false);
  useEffect(() => {
    if (scanId != null || !slug) return;
    let cancelled = false;
    setResolvingScan(true);
    getActiveScan(slug)
      .then((active) => {
        if (cancelled || active.scanId == null) return;
        setScanId(active.scanId);
        try {
          localStorage.setItem(LAST_SCAN_KEY(slug), String(active.scanId));
        } catch {
          // private mode — the id still lives in state for this mount
        }
      })
      .catch(() => {
        // no active scan, or the lookup failed — fall through to the
        // "nothing in progress" panel below rather than blocking on it
      })
      .finally(() => {
        if (!cancelled) setResolvingScan(false);
      });
    return () => {
      cancelled = true;
    };
  }, [scanId, slug]);
  const [scan, setScan] = useState<ScanRecord | null>(null);
  const [estimatedCalls, setEstimatedCalls] = useState<number | null>(null);

  // PROGRESS-01: the real SSE client (`GET /api/scans/{id}/stream`),
  // replacing the old setTimeout-driven fake queue entirely. A mid-scan
  // refresh re-mounts this screen, re-reads `scanId` from localStorage
  // (above) and reconnects from wherever the stream's own `since_seq`
  // cursor resumes — nothing is replayed or lost.
  const stream = useScanStream(scanId);

  useEffect(() => {
    if (scanId == null || !slug) return;
    let cancelled = false;
    getScan(scanId)
      .then((record) => {
        if (cancelled) return;
        setScan(record);
        return estimateScan(slug, {
          mode: record.mode as ScanMode,
          surfaces: record.surfaces,
          repeats: record.repeats,
          model: record.models[0],
        });
      })
      .then((est) => {
        if (cancelled || !est) return;
        setEstimatedCalls(est.estimatedCalls);
      })
      .catch(() => {
        // scan meta/estimate are progress-bar denominators, not the
        // source of truth (the SSE events are) — degrade quietly.
      });
    return () => {
      cancelled = true;
    };
  }, [scanId, slug]);

  const testable = useMemo(() => ex?.rules.filter((r) => r.testable) ?? [], [ex]);
  const activeSurfaces = useMemo(() => ex?.surfaces.filter((s) => s.userControlled) ?? [], [ex]);

  if (loading) return <Loading label="Loading…" />;
  if (notFound) return <NotFound slug={slug} />;
  if (error) return <ErrorState error={error} />;
  if (!ex) return <Loading label="Loading…" />;

  if (scanId == null && resolvingScan) return <Loading label="Looking for a scan…" />;

  if (scanId == null) {
    return (
      <section className="panel" data-state="notfound">
        <p>No scan in progress for this project.</p>
        <Link className="btn" data-variant="ghost" to={`/e/${ex.slug}/config`}>
          Configure a scan <Arrow />
        </Link>
      </section>
    );
  }

  const done = stream.status === "done";
  const events = stream.events;
  const pct = estimatedCalls ? Math.min(100, (stream.attacksDone / estimatedCalls) * 100) : 0;
  const breaksPct = estimatedCalls
    ? Math.min(100, (stream.breaksFound / estimatedCalls) * 100)
    : 0;

  const cellState = (ruleId: string, surfaceId: string) => {
    const rel = events.filter((e) => e.ruleId === ruleId && e.surfaceId === surfaceId);
    if (rel.length === 0) return "ahead";
    if (rel.some((e) => e.broke)) return "snagged";
    return "running";
  };

  const recent = events.slice(-11).reverse();

  return (
    <>
      <header className="scanhead">
        <div>
          <p className="eyebrow">step 05 — scanning</p>
          <h1 className="scanhead__h">
            {done
              ? stream.doneStatus === "failed"
                ? "Scan failed."
                : stream.doneStatus === "stopped_at_cap"
                  ? "Stopped at the cap."
                  : "Scan complete."
              : "Running."}
          </h1>
          <p className="scanhead__sub mono">
            {(scan?.models[0] ?? ex.model)} · {scan?.mode ?? "…"} · {scan?.repeats ?? "…"} repeats
            · {activeSurfaces.length} surfaces
          </p>
        </div>
        <div className="scanhead__acts">
          {done && (
            <Link className="btn" data-variant="solid" to={`/e/${ex.slug}/report`}>
              Open the report <Arrow />
            </Link>
          )}
        </div>
      </header>

      <div className="prog">
        <div className="prog__bar">
          <span className="prog__fill" style={{ width: `${pct}%` }} />
          <span className="prog__breaks" style={{ width: `${breaksPct}%` }} />
        </div>
        <div className="statrow prog__stats">
          <div className="stat">
            <div className="stat__n">
              {stream.attacksDone}
              <span className="stat__unit">
                {estimatedCalls ? ` / ~${estimatedCalls}` : ""}
              </span>
            </div>
            <div className="stat__label">attacks run</div>
          </div>
          <div className="stat" data-tone="snagged"><div className="stat__n">{stream.breaksFound}</div><div className="stat__label">breaks found</div></div>
          <div className="stat"><div className="stat__n">${stream.cost.toFixed(2)}</div><div className="stat__label">spent so far</div></div>
          <div className="stat"><div className="stat__n">${(scan?.spendCap ?? 0).toFixed(2)}</div><div className="stat__label">hard cap</div></div>
        </div>
      </div>

      <section className="matrix">
        <div className="matrix__head">
          <span className="label">rule × surface</span>
          <div className="matrix__key">
            <span data-k="snagged">broke</span>
            <span data-k="running">held so far</span>
            <span data-k="ahead">not yet attacked</span>
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
            {testable.map((r) => (
              <div className="matrix__rowgroup" key={r.id} style={{ display: "contents" }}>
                <div className="matrix__rowlabel">
                  <span>{r.text}</span>
                </div>
                {activeSurfaces.map((s) => (
                  <div key={s.id} className="matrix__cell" data-state={cellState(r.id, s.id)} />
                ))}
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="log">
        <div className="log__head">
          <span className="label">attack log</span>
          <span className="mono dimmer">{done ? "finished" : "live"}</span>
        </div>
        <div className="log__body">
          {recent.map((e) => {
            const rule = ex.rules.find((r) => r.id === e.ruleId);
            const surface = ex.surfaces.find((s) => s.id === e.surfaceId);
            return (
              <div className="log__row" key={e.seq} data-broke={e.broke || undefined}>
                <span className="mono log__rule">{rule ? rule.text.slice(0, 28) : `rule ${e.ruleId}`}</span>
                <span className="mono log__surf">{surface?.path ?? "user message"}</span>
                <span className="mono log__tech">{e.techniqueId}</span>
                <span className="mono log__res">{e.broke ? "broke" : "held"}</span>
              </div>
            );
          })}
          {recent.length === 0 && <div className="log__row dim">Queuing attacks…</div>}
        </div>
      </section>

      {done && (
        <div className="scandone">
          <p>
            {stream.breaksFound === 0
              ? "Nothing broke. That result is only as good as the coverage statement at the top of the report — read that first."
              : `${stream.breaksFound} breaks found. Every one has a full transcript.`}
          </p>
          <Link className="btn" data-variant="solid" to={`/e/${ex.slug}/report`}>
            Open the report <Arrow />
          </Link>
        </div>
      )}
    </>
  );
}

import { useEffect, useRef, useState } from "react";

/**
 * PROGRESS-01: the real live-progress client over `GET /api/scans/{id}/stream`
 * (`snag.api.sse.scan_event_stream`, 01-11), replacing Scanning.tsx's old
 * `setTimeout`-driven fake queue.
 *
 * Every persisted row is one `event: phase` frame shaped
 * `{seq, kind: "attack", techniqueId, ruleId, surfaceId, broke, attacksDone,
 * cost}` (`snag.runner`'s one progress-write seam); the stream closes itself
 * with one `event: done` frame the moment the scan reaches a terminal
 * status. `since_seq` is how a reconnect (mid-scan refresh, or an
 * `EventSource` retry after a network blip) resumes instead of replaying
 * from the start — nothing lost, nothing replayed twice (T-11-01/03).
 *
 * The browser's own built-in `EventSource` retry re-opens the SAME url with
 * no memory of frames already seen (our frames carry no SSE `id:` field, so
 * there is no `Last-Event-ID` to piggy-back on) — this hook closes and
 * reopens itself on `onerror` instead, always with the latest `since_seq`
 * baked into the URL, so a transient drop resumes rather than replays.
 */

export interface ScanStreamEvent {
  seq: number;
  kind: string;
  techniqueId?: string;
  ruleId?: string;
  surfaceId?: string;
  broke?: boolean;
  attacksDone?: number;
  cost?: number;
}

export type ScanStreamStatus = "idle" | "connecting" | "running" | "done" | "error";

export interface UseScanStreamResult {
  events: ScanStreamEvent[];
  status: ScanStreamStatus;
  /** The terminal `scans.status` value from the `done` frame — `"completed"`,
   * `"stopped_at_cap"`, or `"failed"` (`snag.api.sse.TERMINAL_STATUSES`). */
  doneStatus: string | null;
  /** The latest `attacksDone` reported by any event so far (0 until the
   * first frame arrives). */
  attacksDone: number;
  /** The latest running spend reported by any event so far. */
  cost: number;
  /** Count of events so far whose attack broke the rule. */
  breaksFound: number;
}

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

const RECONNECT_DELAY_MS = 1000;

interface RawFrame {
  seq: number;
  kind: string;
  technique_id?: string;
  rule_id?: number | string;
  surface_id?: number | string;
  broke?: boolean;
  attacks_done?: number;
  cost?: number | string;
}

function normalizeFrame(raw: RawFrame): ScanStreamEvent {
  return {
    seq: raw.seq,
    kind: raw.kind,
    techniqueId: raw.technique_id,
    ruleId: raw.rule_id === undefined ? undefined : String(raw.rule_id),
    surfaceId: raw.surface_id === undefined ? undefined : String(raw.surface_id),
    broke: raw.broke,
    attacksDone: raw.attacks_done,
    cost: raw.cost === undefined ? undefined : Number(raw.cost),
  };
}

export function useScanStream(scanId: number | null): UseScanStreamResult {
  const [events, setEvents] = useState<ScanStreamEvent[]>([]);
  const [status, setStatus] = useState<ScanStreamStatus>("idle");
  const [doneStatus, setDoneStatus] = useState<string | null>(null);
  const lastSeqRef = useRef(0);

  useEffect(() => {
    if (scanId == null) {
      setStatus("idle");
      return;
    }

    let cancelled = false;
    let es: EventSource | null = null;
    let retryTimer: number | undefined;
    lastSeqRef.current = 0;
    setEvents([]);
    setDoneStatus(null);
    setStatus("connecting");

    function connect() {
      if (cancelled) return;
      es = new EventSource(
        `${API_BASE}/api/scans/${scanId}/stream?since_seq=${lastSeqRef.current}`,
      );

      es.addEventListener("phase", (ev) => {
        try {
          const raw = JSON.parse((ev as MessageEvent).data) as RawFrame;
          lastSeqRef.current = Math.max(lastSeqRef.current, raw.seq);
          const frame = normalizeFrame(raw);
          setEvents((prev) => [...prev, frame]);
          setStatus("running");
        } catch {
          // malformed frame (shouldn't happen — json.dumps never emits a
          // bare newline server-side) — skip it, keep listening.
        }
      });

      es.addEventListener("done", (ev) => {
        try {
          const data = JSON.parse((ev as MessageEvent).data) as { status: string };
          setDoneStatus(data.status);
        } finally {
          setStatus("done");
          es?.close();
        }
      });

      es.onerror = () => {
        es?.close();
        if (cancelled) return;
        setStatus("connecting");
        retryTimer = window.setTimeout(connect, RECONNECT_DELAY_MS);
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
      es?.close();
    };
  }, [scanId]);

  let attacksDone = 0;
  let cost = 0;
  let breaksFound = 0;
  for (const e of events) {
    if (e.attacksDone !== undefined) attacksDone = e.attacksDone;
    if (e.cost !== undefined) cost = e.cost;
    if (e.broke) breaksFound += 1;
  }

  return { events, status, doneStatus, attacksDone, cost, breaksFound };
}

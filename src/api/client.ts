/**
 * Typed fetch layer over the Snag API. `VITE_API_BASE` is unset in dev
 * (vite.config.ts proxies `/api` to the local backend) and set to the
 * deployed API origin in production, so every call below works unchanged
 * in both environments.
 *
 * Extended by 01-16 with the full read-endpoint set: rules, surfaces,
 * questions, a single break, gaps, fixes, history, and the examples list.
 * Each fn returns the exact UI type from `src/data/types.ts` (or the
 * closest adapter over it) so `useProject` and every screen can consume
 * the response without re-shaping it themselves.
 *
 * Extended by 01-17 with the full mutation set: creating a project,
 * editing rules, answering questions, confirming surfaces, starting/
 * estimating scans, marking false positives, and applying fixes. Every
 * mutation (and the two scan endpoints) attaches the BYOK `X-OpenRouter-Key`
 * header from `localStorage` when one has been entered — see `getStoredKey`/
 * `setStoredKey` below. Reads never attach it: they never fund a model call
 * on their own (the one exception, `getFixes`, is documented at its call
 * site — it degrades to an empty list rather than 402ing a key-free reader,
 * per `useProject`'s own doc comment).
 */
import type { Break, Example, Fix, Gap, HistoryRun, Question, Rule, Surface } from "../data/types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

/** Session-scoped BYOK key storage (T-17-01/T-17-02): held only in
 * `localStorage`, attached only to same-origin `/api` requests via
 * `API_BASE`, never placed in a URL/query string, never logged. Ephemeral
 * projects (PRIV-02) never cause the key itself to be written anywhere —
 * this module is a read/write seam over the key text only, independent of
 * a project's own ephemeral flag. */
const KEY_STORAGE_KEY = "snag:openrouter-key";

export function getStoredKey(): string {
  try {
    return localStorage.getItem(KEY_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setStoredKey(key: string): void {
  try {
    if (key) localStorage.setItem(KEY_STORAGE_KEY, key);
    else localStorage.removeItem(KEY_STORAGE_KEY);
  } catch {
    // localStorage unavailable (private mode) — BYOK just never attaches;
    // the owner-funded/key-free path (deps.resolve_key) still works.
  }
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

interface RequestOptions {
  /** Attach `X-OpenRouter-Key` from localStorage when present. Only set on
   * mutation/scan-funding endpoints (T-17-01) — never on a plain read. */
  withKey?: boolean;
}

async function request<T>(path: string, init?: RequestInit, opts?: RequestOptions): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (opts?.withKey) {
    const key = getStoredKey();
    if (key) headers["X-OpenRouter-Key"] = key;
  }
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      // response wasn't JSON — fall back to statusText
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface CreateProjectInput {
  systemPrompt: string;
  tools?: string;
  model?: string;
  ephemeral?: boolean;
}

export interface CreateProjectResult {
  slug: string;
}

export function createProject(input: CreateProjectInput): Promise<CreateProjectResult> {
  return request<CreateProjectResult>(
    "/api/projects",
    {
      method: "POST",
      body: JSON.stringify({
        system_prompt: input.systemPrompt,
        tools: input.tools || undefined,
        model: input.model,
        ephemeral: input.ephemeral ?? false,
      }),
    },
    { withKey: true },
  );
}

/** GET /api/models — KEY-03: the frontend's model picker is sourced from
 * this (the server's `ACCEPTED_MODELS` allowlist), never from a static
 * fixture. */
export function getModels(): Promise<{ models: string[] }> {
  return request<{ models: string[] }>("/api/models");
}

/** GET /api/projects/{slug}/report — the composed report (rules, surfaces,
 * breaks with variants, scan meta). `questions`/`gaps`/`fixes` always come
 * back empty here (those own endpoints below) — `useProject` merges them. */
export function getReport(slug: string): Promise<Example> {
  return request<Example>(`/api/projects/${encodeURIComponent(slug)}/report`);
}

/** GET /api/projects/{slug}/report/{breakId} — one break, `variants[]`
 * carrying the real transcript (`turns`) and `checkerOutput` for every
 * repeat in the scan it came from (BREAK-02). */
export function getBreak(slug: string, breakId: string): Promise<Break> {
  return request<Break>(
    `/api/projects/${encodeURIComponent(slug)}/report/${encodeURIComponent(breakId)}`,
  );
}

/** POST /api/projects/{slug}/report/{breakId}/false-positive */
export function markFalsePositive(slug: string, breakId: string, value: boolean): Promise<void> {
  return request<{ ok: boolean }>(
    `/api/projects/${encodeURIComponent(slug)}/report/${encodeURIComponent(breakId)}/false-positive`,
    { method: "POST", body: JSON.stringify({ value }) },
  ).then(() => undefined);
}

/** GET /api/projects/{slug}/rules */
export function getRules(slug: string): Promise<Rule[]> {
  return request<Rule[]>(`/api/projects/${encodeURIComponent(slug)}/rules`);
}

export interface RuleCreateInput {
  text: string;
  category?: string;
  direction?: string;
  checkerType?: string;
  checkerConfig?: Record<string, unknown>;
  confidence?: number;
}

/** POST /api/projects/{slug}/rules — EXTRACT-03: a rule the user typed in. */
export function addRule(slug: string, input: RuleCreateInput): Promise<Rule> {
  return request<Rule>(
    `/api/projects/${encodeURIComponent(slug)}/rules`,
    {
      method: "POST",
      body: JSON.stringify({
        text: input.text,
        category: input.category ?? "other",
        direction: input.direction ?? "negative",
        checker_type: input.checkerType ?? "none",
        checker_config: input.checkerConfig,
        confidence: input.confidence ?? 1.0,
      }),
    },
    { withKey: true },
  );
}

export interface RulePatchInput {
  text?: string;
  category?: string;
  direction?: string;
  checkerType?: string;
  checkerConfig?: Record<string, unknown>;
  testable?: boolean;
  confidence?: number;
}

/** PATCH /api/projects/{slug}/rules/{ruleId} — partial update. */
export function patchRule(slug: string, ruleId: string, patch: RulePatchInput): Promise<Rule> {
  const body: Record<string, unknown> = {};
  if (patch.text !== undefined) body.text = patch.text;
  if (patch.category !== undefined) body.category = patch.category;
  if (patch.direction !== undefined) body.direction = patch.direction;
  if (patch.checkerType !== undefined) body.checker_type = patch.checkerType;
  if (patch.checkerConfig !== undefined) body.checker_config = patch.checkerConfig;
  if (patch.testable !== undefined) body.testable = patch.testable;
  if (patch.confidence !== undefined) body.confidence = patch.confidence;
  return request<Rule>(
    `/api/projects/${encodeURIComponent(slug)}/rules/${encodeURIComponent(ruleId)}`,
    { method: "PATCH", body: JSON.stringify(body) },
    { withKey: true },
  );
}

/** DELETE /api/projects/{slug}/rules/{ruleId} */
export function deleteRule(slug: string, ruleId: string): Promise<void> {
  return request<void>(
    `/api/projects/${encodeURIComponent(slug)}/rules/${encodeURIComponent(ruleId)}`,
    { method: "DELETE" },
    { withKey: true },
  );
}

interface SurfacesApiResponse {
  surfaces: Surface[];
}

/** GET /api/projects/{slug}/surfaces — unwraps the `{surfaces: [...]}` envelope. */
export function getSurfaces(slug: string): Promise<Surface[]> {
  return request<SurfacesApiResponse>(`/api/projects/${encodeURIComponent(slug)}/surfaces`).then(
    (r) => r.surfaces,
  );
}

/** POST /api/projects/{slug}/surfaces — (re)generates the full surface map
 * from the latest prompt/tools. A brand new project only has the one
 * always-present `chat` row until this is called once (Surfaces.tsx calls
 * it on first visit to the confirm-surfaces step). */
export function generateSurfaces(slug: string): Promise<Surface[]> {
  return request<SurfacesApiResponse>(
    `/api/projects/${encodeURIComponent(slug)}/surfaces`,
    { method: "POST" },
    { withKey: true },
  ).then((r) => r.surfaces);
}

export interface SurfacePatchInput {
  userControlled?: boolean;
  confirmed?: boolean;
  tests?: number;
  note?: string;
}

/** PATCH /api/projects/{slug}/surfaces/{id} — SURFACE-03: a scan only ever
 * dispatches to a surface that is BOTH `confirmed` and `userControlled`. */
export function toggleSurface(
  slug: string,
  id: string,
  patch: SurfacePatchInput,
): Promise<Surface> {
  const body: Record<string, unknown> = {};
  if (patch.userControlled !== undefined) body.user_controlled = patch.userControlled;
  if (patch.confirmed !== undefined) body.confirmed = patch.confirmed;
  if (patch.tests !== undefined) body.tests = patch.tests;
  if (patch.note !== undefined) body.note = patch.note;
  return request<Surface>(
    `/api/projects/${encodeURIComponent(slug)}/surfaces/${encodeURIComponent(id)}`,
    { method: "PATCH", body: JSON.stringify(body) },
    { withKey: true },
  );
}

interface QuestionApiRow {
  id: string;
  ruleId: string;
  round: number;
  text: string;
  placeholder: string | null;
  answerRaw?: string | null;
  answerNormalized?: string | null;
  status: Question["status"];
  conflictNote?: string | null;
}

interface QuestionsApiResponse {
  round: number;
  rules: { ruleId: string; questions: QuestionApiRow[] }[];
}

function flattenQuestions(resp: QuestionsApiResponse): Question[] {
  return resp.rules
    .flatMap((r) => r.questions)
    .map((q) => ({
      id: q.id,
      ruleId: q.ruleId,
      round: q.round,
      text: q.text,
      placeholder: q.placeholder ?? "",
      answerRaw: q.answerRaw ?? undefined,
      answerNormalized: q.answerNormalized ?? undefined,
      status: q.status,
      conflictNote: q.conflictNote ?? undefined,
    }));
}

/** GET /api/projects/{slug}/questions — flattens the grouped-by-rule
 * envelope into a flat `Question[]`, matching `Example.questions`. */
export function getQuestions(slug: string): Promise<Question[]> {
  return request<QuestionsApiResponse>(
    `/api/projects/${encodeURIComponent(slug)}/questions`,
  ).then(flattenQuestions);
}

export interface AnswerInput {
  questionId: string;
  answerRaw: string;
}

export interface AnsweredQuestionResult {
  questionId: string;
  ruleId: string;
  status: Question["status"];
  checkerConfig: Record<string, unknown>;
  conflictNote?: string | null;
}

export interface AnswerQuestionsResult {
  answered: AnsweredQuestionResult[];
  round: number;
  openRemaining: number;
  roundsExhausted: boolean;
}

/** POST /api/projects/{slug}/questions/answers — FOLLOWUP-02: the response
 * always carries the literal `checkerConfig` that will be checked, shown
 * back before anything runs. May raise a new round of follow-ups, which is
 * why callers should refetch the full question list after this resolves. */
export function answerQuestions(
  slug: string,
  answers: AnswerInput[],
): Promise<AnswerQuestionsResult> {
  return request<AnswerQuestionsResult>(
    `/api/projects/${encodeURIComponent(slug)}/questions/answers`,
    {
      method: "POST",
      body: JSON.stringify({
        answers: answers.map((a) => ({
          question_id: Number(a.questionId),
          answer_raw: a.answerRaw,
        })),
      }),
    },
    { withKey: true },
  );
}

/** GET /api/projects/{slug}/gaps */
export function getGaps(slug: string): Promise<Gap[]> {
  return request<Gap[]>(`/api/projects/${encodeURIComponent(slug)}/gaps`);
}

/** GET /api/projects/{slug}/fixes — may itself dispatch a proposer call
 * server-side for a still-breaking rule with no fix on file yet; a seeded
 * example never actually dispatches one (proposed once at seed time). Sends
 * the BYOK key so a real (non-seeded, not owner-funded) project's proposer
 * call is actually funded, rather than silently degrading to an empty list
 * whenever no owner key is configured server-side. */
export function getFixes(slug: string): Promise<Fix[]> {
  return request<Fix[]>(
    `/api/projects/${encodeURIComponent(slug)}/fixes`,
    undefined,
    { withKey: true },
  );
}

export interface ApplyFixResult {
  verifyScanId: number;
  beforeBreaks: number;
  afterBreaks: number;
}

/** POST /api/projects/{slug}/fixes/{fixId}/apply — FIX-02: writes the fix's
 * edit as a new prompt version, reruns only the attacks that broke this
 * rule, and reports before/after break counts for that exact attack set. */
export function applyFix(slug: string, fixId: string): Promise<ApplyFixResult> {
  return request<{ verify_scan_id: number; before_breaks: number; after_breaks: number }>(
    `/api/projects/${encodeURIComponent(slug)}/fixes/${encodeURIComponent(fixId)}/apply`,
    { method: "POST" },
    { withKey: true },
  ).then((r) => ({
    verifyScanId: r.verify_scan_id,
    beforeBreaks: r.before_breaks,
    afterBreaks: r.after_breaks,
  }));
}

/** GET /api/projects/{slug}/history */
export function getHistory(slug: string): Promise<HistoryRun[]> {
  return request<HistoryRun[]>(`/api/projects/${encodeURIComponent(slug)}/history`);
}

export interface ExampleSummary {
  slug: string;
  n: number;
  title: string;
  blurb: string;
  demonstrates: string;
  headline: string;
  model: string;
  scan: { mode: string; repeats: number; calls: number; cost: number; duration?: string };
  coverage: {
    total: number;
    testable: number;
    eyes: number;
    toolSupportNote?: string | null;
    indicativeOnly?: boolean;
  };
}

/** GET /api/examples — the six seeded example projects, gallery-shaped
 * (no rules/breaks payload; open a report for the full detail). Never
 * attaches the BYOK key — these are read-only, key-free fixtures. */
export function listExamples(): Promise<ExampleSummary[]> {
  return request<ExampleSummary[]>("/api/examples");
}

export type ScanMode = "quick" | "standard" | "deep" | "custom";

export interface StartScanInput {
  mode: ScanMode;
  surfaces?: string[];
  repeats?: number;
  model?: string;
  callCap?: number;
  spendCap?: number;
}

export interface StartScanResult {
  scanId: number;
}

/** POST /api/scans — SCAN-02: enqueues the scan; a worker runs the real
 * attack matrix. Returns immediately with the scan id to stream progress
 * for (`useScanStream`). */
export function startScan(slug: string, config: StartScanInput): Promise<StartScanResult> {
  return request<{ scan_id: number }>(
    "/api/scans",
    {
      method: "POST",
      body: JSON.stringify({
        slug,
        mode: config.mode,
        surfaces: config.surfaces,
        repeats: config.repeats,
        model: config.model,
        call_cap: config.callCap,
        spend_cap: config.spendCap,
      }),
    },
    { withKey: true },
  ).then((r) => ({ scanId: r.scan_id }));
}

export interface EstimateScanResult {
  estimatedCostUsd: number;
  estimatedCalls: number;
  unknownPricing: boolean;
}

/** POST /api/scans/estimate — the live cost estimate ScanConfig.tsx shows
 * before dispatch, computed the same way the server itself bounds the
 * spend cap (never the client's own rough guess). */
export function estimateScan(
  slug: string,
  config: Omit<StartScanInput, "callCap" | "spendCap">,
): Promise<EstimateScanResult> {
  return request<{
    estimated_cost_usd: number | string;
    estimated_calls: number;
    unknown_pricing: boolean;
  }>(
    "/api/scans/estimate",
    {
      method: "POST",
      body: JSON.stringify({
        slug,
        mode: config.mode,
        surfaces: config.surfaces,
        repeats: config.repeats,
        model: config.model,
      }),
    },
    { withKey: true },
  ).then((r) => ({
    estimatedCostUsd: Number(r.estimated_cost_usd),
    estimatedCalls: r.estimated_calls,
    unknownPricing: r.unknown_pricing,
  }));
}

export interface ScanRecord {
  id: number;
  status: string;
  mode: string;
  repeats: number;
  surfaces: string[];
  models: string[];
  callCount: number;
  cost: number;
  callCap: number | null;
  spendCap: number | null;
  skippedCount: number;
  attacksDone: number;
  breaksFound: number;
  indicativeOnly: boolean;
}

/** GET /api/scans/{scanId} — the scan's own record (mode/repeats/model,
 * running counters), independent of the SSE stream — what Scanning.tsx
 * reads once up front to know what it's watching. */
/** Which scan this project has in flight, or null. The durable answer to
 * "is a run still going" — a typed URL or a second tab has no localStorage
 * to fall back on, and the scanning screen used to declare a live run
 * finished because of it. */
export async function getActiveScan(
  slug: string,
): Promise<{ scanId: number | null; status: string | null }> {
  const res = await request<{ scanId: number | null; status: string | null }>(
    `/api/projects/${encodeURIComponent(slug)}/active-scan`,
  );
  return { scanId: res.scanId ?? null, status: res.status ?? null };
}

export function getScan(scanId: number): Promise<ScanRecord> {
  return request<{
    id: number;
    status: string;
    mode: string;
    repeats: number;
    surfaces: string[];
    models: string[];
    call_count: number;
    cost: number | string;
    call_cap: number | null;
    spend_cap: number | string | null;
    skipped_count: number;
    attacks_done: number;
    breaks_found: number;
    indicative_only: boolean;
  }>(`/api/scans/${encodeURIComponent(String(scanId))}`).then((r) => ({
    id: r.id,
    status: r.status,
    mode: r.mode,
    repeats: r.repeats,
    surfaces: r.surfaces,
    models: r.models,
    callCount: r.call_count,
    cost: Number(r.cost),
    callCap: r.call_cap,
    spendCap: r.spend_cap === null ? null : Number(r.spend_cap),
    skippedCount: r.skipped_count,
    attacksDone: r.attacks_done,
    breaksFound: r.breaks_found,
    indicativeOnly: r.indicative_only,
  }));
}

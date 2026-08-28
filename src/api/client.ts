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
 */
import type { Break, Example, Fix, Gap, HistoryRun, Question, Rule, Surface } from "../data/types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
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
  return (await res.json()) as T;
}

export interface CreateProjectInput {
  systemPrompt: string;
  tools?: string;
  model?: string;
}

export interface CreateProjectResult {
  slug: string;
}

export function createProject(input: CreateProjectInput): Promise<CreateProjectResult> {
  return request<CreateProjectResult>("/api/projects", {
    method: "POST",
    body: JSON.stringify({
      system_prompt: input.systemPrompt,
      tools: input.tools,
      model: input.model,
    }),
  });
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

/** GET /api/projects/{slug}/rules */
export function getRules(slug: string): Promise<Rule[]> {
  return request<Rule[]>(`/api/projects/${encodeURIComponent(slug)}/rules`);
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

/** GET /api/projects/{slug}/gaps */
export function getGaps(slug: string): Promise<Gap[]> {
  return request<Gap[]>(`/api/projects/${encodeURIComponent(slug)}/gaps`);
}

/** GET /api/projects/{slug}/fixes — may itself dispatch a proposer call
 * server-side for a still-breaking rule with no fix on file yet; a seeded
 * example never actually dispatches one (proposed once at seed time). */
export function getFixes(slug: string): Promise<Fix[]> {
  return request<Fix[]>(`/api/projects/${encodeURIComponent(slug)}/fixes`);
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
 * (no rules/breaks payload; open a report for the full detail). */
export function listExamples(): Promise<ExampleSummary[]> {
  return request<ExampleSummary[]>("/api/examples");
}

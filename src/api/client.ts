/**
 * Typed fetch layer over the Snag API. `VITE_API_BASE` is unset in dev
 * (vite.config.ts proxies `/api` to the local backend) and set to the
 * deployed API origin in production, so every call below works unchanged
 * in both environments.
 *
 * Extended by 01-16 with the full endpoint set (scans, gaps, fixes,
 * history, models). This plan wires only what the tracer needs: create a
 * project and read its report.
 */
import type { Example } from "../data/types";

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

export function getReport(slug: string): Promise<Example> {
  return request<Example>(`/api/projects/${encodeURIComponent(slug)}/report`);
}

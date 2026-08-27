import type { Example, Rule, RuleCategory, Risk } from "./types";
import { retail } from "./ex-retail";
import { rag } from "./ex-rag";
import { coding, healthcare, hr, hardened } from "./ex-rest";

export * from "./types";

export const examples: Example[] = [retail, rag, coding, healthcare, hr, hardened];

export const byslug = (slug?: string): Example =>
  examples.find((e) => e.slug === slug) ?? retail;

export const CATEGORY_LABEL: Record<RuleCategory, string> = {
  content_prohibition: "content prohibition",
  content_requirement: "content requirement",
  secret_protection: "secret protection",
  format: "format",
  scope_boundary: "scope boundary",
  tool_authorization: "tool authorization",
  tool_limits: "tool limits",
  sequence: "sequence",
  escalation: "escalation",
  identity: "identity",
  data_handling: "data handling",
  refusal: "refusal",
  tone_style: "tone / style",
  other: "other",
};

export const RISK_LABEL: Record<Risk, string> = {
  high: "high",
  medium: "medium",
  low: "low",
  none: "none",
};

export function verdictOf(rule: Rule) {
  if (!rule.testable) return "eyes" as const;
  return rule.breaks > 0 ? ("snagged" as const) : ("held" as const);
}

export function coverage(ex: Example) {
  const total = ex.rules.length;
  const testable = ex.rules.filter((r) => r.testable).length;
  return { total, testable, eyes: total - testable };
}

export function tally(ex: Example) {
  const testable = ex.rules.filter((r) => r.testable);
  return {
    snagged: testable.filter((r) => r.breaks > 0).length,
    held: testable.filter((r) => r.breaks === 0).length,
    eyes: ex.rules.length - testable.length,
    attacks: ex.rules.reduce((n, r) => n + r.attacks, 0),
    breaks: ex.rules.reduce((n, r) => n + r.breaks, 0),
  };
}

/** The model catalogue shown on the paste screen. Static — no network. */
export const MODELS = [
  { id: "openai/gpt-4o-mini", name: "GPT-4o mini", vendor: "OpenAI", price: "$0.15 / $0.60", note: "Cheap and fast. Where prompts break most." },
  { id: "anthropic/claude-3-5-haiku", name: "Claude 3.5 Haiku", vendor: "Anthropic", price: "$0.80 / $4.00", note: "Strong instruction-following at small size." },
  { id: "google/gemini-2.0-flash", name: "Gemini 2.0 Flash", vendor: "Google", price: "$0.10 / $0.40", note: "Very cheap. Long context." },
  { id: "meta-llama/llama-3.3-70b", name: "Llama 3.3 70B", vendor: "Meta", price: "$0.12 / $0.30", note: "Open weights. Common self-host choice." },
  { id: "openai/gpt-4o", name: "GPT-4o", vendor: "OpenAI", price: "$2.50 / $10.00", note: "Compare against the cheap model to see what you traded." },
  { id: "mistralai/mistral-small", name: "Mistral Small", vendor: "Mistral", price: "$0.20 / $0.60", note: "European hosting available." },
];

export const SCAN_MODES = [
  { id: "quick", name: "Quick", surfaces: ["direct"], repeats: 1, calls: "~50", cost: "~$0.07", note: "Direct injection only, one repeat. Indicative — a rate needs more than one run." },
  { id: "standard", name: "Standard", surfaces: ["direct", "tool abuse"], repeats: 3, calls: "~300", cost: "~$0.40", note: "The default. Both directions on every tool-backed rule." },
  { id: "deep", name: "Deep", surfaces: ["direct", "tool abuse", "multi-turn", "indirect"], repeats: 5, calls: "~1,200", cost: "~$1.60", note: "Everything, five repeats. This is where multi-turn and poisoned data get tested." },
  { id: "custom", name: "Custom", surfaces: [], repeats: 3, calls: "shown before starting", cost: "shown before starting", note: "Pick surfaces, repeats and which rules to run." },
];

export type Verdict = "held" | "snagged" | "eyes" | "dead";
export type Direction = "negative" | "positive";
export type Risk = "high" | "medium" | "low" | "none";
export type SurfaceKind = "template_var" | "tool_param" | "tool_return" | "chat";

export type RuleCategory =
  | "content_prohibition"
  | "content_requirement"
  | "secret_protection"
  | "format"
  | "scope_boundary"
  | "tool_authorization"
  | "tool_limits"
  | "sequence"
  | "escalation"
  | "identity"
  | "data_handling"
  | "refusal"
  | "tone_style"
  | "other";

export interface Question {
  id: string;
  ruleId: string;
  round: number;
  text: string;
  placeholder: string;
  /** Pre-filled to show the answer style this question demonstrates. */
  answerRaw?: string;
  answerNormalized?: string;
  status: "open" | "answered" | "inferred" | "skipped" | "conflict";
  conflictNote?: string;
}

export interface Rule {
  id: string;
  text: string;
  category: RuleCategory;
  direction: Direction;
  sourceLine: string;
  checkerType: string;
  checkerConfig?: Record<string, unknown>;
  testable: boolean;
  confidence: number;
  attacks: number;
  breaks: number;
  /** Overrides the generic plain-English description of the checker. */
  plain?: string;
  /** Set when checkerType is 'none' — why code can't test it. */
  untestableReason?: string;
  /**
   * 01-17: whether the extractor actually found this rule in the pasted
   * prompt, vs. a rule the user typed in themselves (EXTRACT-03) — the real
   * source of Rules.tsx's "not in your prompt" chip, replacing a
   * client-only flag that a page refresh would silently lose.
   */
  inPrompt?: boolean;
}

export interface Surface {
  id: string;
  path: string;
  kind: SurfaceKind;
  source: string;
  risk: Risk;
  tests: number;
  userControlled: boolean;
  note: string;
}

export interface Turn {
  role: "system" | "user" | "assistant" | "tool_call" | "tool_result";
  name?: string;
  content: string;
  /** Substring to mark as attacker-planted text. */
  planted?: string;
  /** Substring to mark as the checker's evidence of a break. */
  evidence?: string;
}

export interface BreakVariant {
  broke: boolean;
  reply: string;
  evidence?: string;
  /**
   * 01-16: the real full transcript and checker output for this one repeat,
   * as returned by GET /report/{breakId} — additive over the narrow
   * {broke, reply, evidence} shape so BreakDetail can render each run's
   * actual conversation instead of reconstructing one.
   */
  turns?: Turn[];
  checkerOutput?: string;
  repeatIndex?: number;
  runId?: number;
}

export interface Break {
  id: string;
  ruleId: string;
  surfaceId: string;
  techniqueId: string;
  family: string;
  hits: number;
  repeats: number;
  turns: Turn[];
  checkerOutput: string;
  falsePositive: boolean;
  /**
   * Repeats of the same attack differ only in what the model replied, so runs are
   * stored as alternative final turns rather than whole duplicate conversations.
   */
  variants?: BreakVariant[];
}

export interface Gap {
  id: string;
  item: string;
  probe: string;
  observed: string;
  verdict: string;
  /** 01-16: the real boolean the API computes server-side — prefer this
   * over parsing `verdict`'s text when present. */
  covered?: boolean;
}

export interface Fix {
  id: string;
  ruleId: string;
  removed: string[];
  added: string[];
  rationale: string;
  before: string;
  after: string;
}

export interface HistoryRun {
  id: string;
  date: string;
  label: string;
  mode: string;
  breaks: number;
  fixed: number;
  added: number;
  unchanged: number;
  calls: number;
  cost: number;
}

export interface Example {
  slug: string;
  n: number;
  title: string;
  blurb: string;
  demonstrates: string;
  headline: string;
  model: string;
  systemPrompt: string;
  tools: string;
  rules: Rule[];
  surfaces: Surface[];
  questions: Question[];
  breaks: Break[];
  gaps: Gap[];
  fixes: Fix[];
  history: HistoryRun[];
  scan: { mode: string; repeats: number; calls: number; cost: number; duration: string };
  walkthrough: { intent: string; broke: string; why: string; fix: string };
}

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
  /** Set when checkerType is 'none' — why code can't test it. */
  untestableReason?: string;
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
}

export interface Gap {
  id: string;
  item: string;
  probe: string;
  observed: string;
  verdict: string;
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

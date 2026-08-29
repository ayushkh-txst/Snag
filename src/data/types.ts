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
  /** Which tier tested this rule: a checker, or the judge that covers the rest. */
  verdictTier?: VerdictTier;
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
  /**
   * True for an `assistant` turn the ATTACK wrote rather than the model —
   * the prefill technique family seeds a fabricated "I've already started
   * complying" opener into the request so the model continues from it. It
   * is kept in the transcript because the report must show what was
   * actually sent, and it must never be rendered as the model's own words.
   */
  forged?: boolean;
}

/**
 * Which kind of evidence decided a verdict. `mechanical` is a checker from the
 * registry reading the transcript — the trust anchor, and the default when the
 * field is absent. `judged` is a stronger model scoring a rule no checker could
 * express, and it always comes with the verbatim span it rests on.
 */
export type VerdictTier = "mechanical" | "judged";

export interface BreakVariant {
  broke: boolean;
  reply: string;
  evidence?: string;
  verdictTier?: VerdictTier;
  /** The span of this reply a judged verdict rests on, quoted verbatim. */
  quote?: string;
  /**
   * The judge read the same reply and reached the opposite verdict. On a run
   * that BROKE that is a suspected false positive; on one that HELD it is a
   * suspected miss the pattern never saw. Neither flips what the checker said —
   * the disagreement is recorded beside it, with the reasoning and a verbatim
   * quote, for a person to settle.
   */
  disputed?: boolean;
  disputeNote?: string;
  disputeQuote?: string;
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
  verdictTier?: VerdictTier;
  /** The span a judged verdict rests on, quoted verbatim from the reply. */
  quote?: string;
  /** How many of `hits` the judge disputed, and whether it disputed all of them. */
  disputedHits?: number;
  disputed?: boolean;
  /** Runs that HELD and the judge reads as violations — suspected misses. */
  flaggedHits?: number;
  disputeNote?: string;
  disputeQuote?: string;
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
  /**
   * 01-17: whether this fix was already applied in an earlier session —
   * without this, a page reload after applying a fix would show every fix
   * as "Apply and verify" again, contradicting what the server has on
   * file. `verifyScanId` is the narrowed rerun scan `apply` created; its
   * own `attacksDone`/`breaksFound` counters ARE this fix's before/after
   * break counts (the rerun set IS exactly what broke originally), so a
   * reload can recover the real verify numbers via `getScan` instead of
   * losing them.
   */
  applied?: boolean;
  verifyScanId?: number | null;
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
  /**
   * Suspected MISSES: groups nothing broke mechanically, where the judge read a
   * reply the checker held and called it a violation. Never counted as breaks —
   * a model's opinion is enough to be worth a look, not enough to make one.
   */
  flagged?: Break[];
  gaps: Gap[];
  fixes: Fix[];
  history: HistoryRun[];
  scan: { mode: string; repeats: number; calls: number; cost: number; duration: string };
  walkthrough: { intent: string; broke: string; why: string; fix: string };
}

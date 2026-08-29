# Snag

**Paste your system prompt and tool definitions, and find out what breaks them.**

*A snag is both the flaw and the thing that catches it.*

---

## Where this is

**Phase A — the whole site, static.** Every screen in the spec is built in React
against fixture data. There is no API, no database, and no model call anywhere in
this repository. Scans "run" against canned timings so the live-progress screen can
be judged properly.

Per §16 of the spec, this is the gate: the mockup is reviewed and approved before
Phase B starts. Nothing behind it gets built until the shape of the thing is agreed.

## Running it

```bash
npm install
npm run dev
```

Node 20+. The app is served at `http://localhost:5173`.

## What's here

| Route | Screen |
|---|---|
| `/` | Landing — the thesis, the live markup demo, the example gallery |
| `/paste` | Paste — prompt, tool schemas, model picker, storage policy |
| `/examples` | The six pre-run examples, fully browsable |
| `/e/:slug/rules` | Extracted rules, categories, testable split, edit |
| `/e/:slug/questions` | Multi-round follow-ups, all four answer styles, contradictions |
| `/e/:slug/surfaces` | Injection point map with risk and test counts |
| `/e/:slug/config` | Mode, surfaces, repeats, hard caps, cost estimate |
| `/e/:slug/scanning` | Live progress — rule × surface matrix, attack log, running cost |
| `/e/:slug/report` | Coverage statement, per-rule break rates, checker configs |
| `/e/:slug/report/:breakId` | Every run of one attack, its transcript, mark false positive |
| `/e/:slug/gaps` | Checklist misses and observed behaviour |
| `/e/:slug/fixes` | Suggested diffs, apply, verify |
| `/e/:slug/history` | Run comparison — fixed / new / unchanged |

The six examples are `retail-support-bot`, `rag-assistant`, `coding-agent`,
`healthcare-intake`, `hr-assistant`, `hardened-prompt`.

## Design direction

**The marked-up page.** Snag reads a document and marks where it fails, so the
interface is a document with margins rather than a security dashboard.

- **Palette** — bone paper and ink, with pen colours carrying the verdict: teal for
  a rule that held, magenta for one that snagged, amber for one that needs your
  eyes, graphite for one nothing measured. Diagnostic marks, not threat colours.
  Light and dark are both defined as tokens in `src/styles/tokens.css`.
- **Type** — Bricolage Grotesque for display, Inter Tight for UI, IBM Plex Mono for
  prompts, transcripts and checker configs. The mono face carries most of the
  product, so it was chosen first.
- **Structure** — a numbered spine for the pipeline. The numbering is load-bearing:
  the spec defines an ordered sequence, and these are those steps.
- **Signature** — the **snag mark** (`src/components/SnagMark.tsx`), an SVG stroke
  drawn beneath every rule. Held is a taut line. Snagged is a line that catches and
  pulls a loop where the rule gave way. Needs-your-eyes is dashes, because nothing
  was measured. On the landing page a scan sweeps down a real system prompt and
  marks it live.

Everything else stays quiet. No gradients, no glow, no ambient motion.

## What is deliberately not here

- No functionality. Nothing calls a model, nothing persists, nothing is scanned.
- Interactions that hold local state — ticking a rule, adding one, unticking a
  surface, confirming an answer, applying a fix, marking a false positive — are UI
  state only, so the flow can be judged.
- Numbers Snag has not measured are not shown. An unapplied fix reports "not run
  yet". An unconfirmed answer shows no check.
- Checker configuration is not shown on the rules screen, because at that point in
  the flow the blanks have not been filled in yet.

## Fixture invariants

Two things the fixtures must keep true, since the UI now leans on both:

- Every rule with `breaks > 0` has at least one stored attack run, because the report
  leads with the text that broke it.
- `sum(run.hits) === rule.breaks`, and `sum(rule.attacks) <= scan.calls`.
- Every stored run carries `variants`: at least one alternative reply that broke and
  one that held, since the break detail lets you step through all N repeats. Repeats
  of the same attack differ only in the reply, so runs are stored as alternative final
  turns rather than duplicate conversations. A run that held stops where the attack
  landed — the tool call after it only happened in the runs that broke.

## Layout

```
src/
  components/   SnagMark, shell (top bar, spine, step header), UI primitives
  data/         types + the six example fixtures
  screens/      one file per screen
  styles/       tokens · base · app chrome · screens
```

Fixtures are hand-written to match what real output would look like, including the
things that make a report believable: full transcripts rather than summaries, a
false positive the user has already excluded, an unresolved contradiction between two
rules, and one example that comes back nearly clean.

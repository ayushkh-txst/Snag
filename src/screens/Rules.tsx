import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { NextBar, StepHead } from "../components/Shell";
import { SnagMark } from "../components/SnagMark";
import { CheckerConfig, Coverage, Pill } from "../components/ui";
import { CATEGORY_LABEL, byslug, type Rule } from "../data";

function SourcePane({ prompt, active }: { prompt: string; active?: string }) {
  const lines = prompt.split("\n");
  return (
    <aside className="srcpane">
      <div className="srcpane__head">
        <span className="label">system prompt</span>
        <span className="mono dimmer">{lines.length} lines</span>
      </div>
      <div className="srcpane__body">
        {lines.map((l, i) => (
          <div
            className="srcline"
            key={i}
            data-on={active && l.trim() && active.includes(l.trim()) ? true : undefined}
          >
            <span className="srcline__n mono">{String(i + 1).padStart(2, "0")}</span>
            <span className="srcline__t mono">{l || " "}</span>
          </div>
        ))}
      </div>
    </aside>
  );
}

function RuleCard({
  rule,
  i,
  onToggle,
  onEdit,
  onDelete,
  onHover,
}: {
  rule: Rule;
  i: number;
  onToggle: () => void;
  onEdit: (t: string) => void;
  onDelete: () => void;
  onHover: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const verdict = rule.testable ? "held" : "eyes";

  return (
    <article className="rulecard" onMouseEnter={onHover} data-testable={rule.testable || undefined}>
      <header className="rulecard__head">
        <span className="rulecard__n mono">{String(i + 1).padStart(2, "0")}</span>
        <span className="chip">{CATEGORY_LABEL[rule.category]}</span>
        <span className="chip" data-dir={rule.direction}>
          {rule.direction === "positive" ? "must refuse" : "must not break"}
        </span>
        <div className="rulecard__acts">
          <button className="tinybtn" onClick={() => setEditing((v) => !v)}>
            {editing ? "done" : "edit"}
          </button>
          <button className="tinybtn" onClick={onDelete}>delete</button>
        </div>
      </header>

      {editing ? (
        <textarea
          className="rulecard__edit mono"
          value={rule.text}
          rows={2}
          onChange={(e) => onEdit(e.target.value)}
          autoFocus
        />
      ) : (
        <>
          <span className="markwrap">
            <p className="rulecard__text">{rule.text}</p>
            <SnagMark verdict={verdict} delay={i * 55} />
          </span>
        </>
      )}

      <p className="rulecard__src mono">
        <span className="dimmer">from </span>
        {rule.sourceLine}
      </p>

      {rule.testable ? (
        <CheckerConfig type={rule.checkerType} config={rule.checkerConfig} />
      ) : (
        <div className="untest">
          <Pill verdict="eyes" />
          <p>{rule.untestableReason}</p>
        </div>
      )}

      <footer className="rulecard__foot">
        <span className="mono dimmer">extractor confidence {rule.confidence.toFixed(2)}</span>
        <label className="switch">
          <input type="checkbox" checked={rule.testable} onChange={onToggle} />
          <span>test this rule with code</span>
        </label>
      </footer>
    </article>
  );
}

export function Rules() {
  const { slug } = useParams();
  const ex = byslug(slug);
  const [rules, setRules] = useState<Rule[]>(ex.rules);
  const [active, setActive] = useState<string | undefined>();

  const c = useMemo(() => {
    const testable = rules.filter((r) => r.testable).length;
    return { total: rules.length, testable, eyes: rules.length - testable };
  }, [rules]);

  const patch = (id: string, next: Partial<Rule>) =>
    setRules((rs) => rs.map((r) => (r.id === id ? { ...r, ...next } : r)));

  return (
    <>
      <StepHead
        n="01"
        title="Here is what your prompt actually says."
        lede="One model call read the prompt and your tool schemas and returned these rules, each with the checker that will verify it. Nothing has been attacked yet. Change anything you disagree with — this list is what gets tested."
        aside={<Coverage total={c.total} testable={c.testable} eyes={c.eyes} />}
      />

      <div className="doclayout">
        <SourcePane prompt={ex.systemPrompt} active={active} />
        <div className="doclayout__main">
          <div className="rulelist">
            {rules.map((r, i) => (
              <RuleCard
                key={r.id}
                rule={r}
                i={i}
                onHover={() => setActive(r.sourceLine)}
                onToggle={() => patch(r.id, { testable: !r.testable })}
                onEdit={(t) => patch(r.id, { text: t })}
                onDelete={() => setRules((rs) => rs.filter((x) => x.id !== r.id))}
              />
            ))}
          </div>
          <button
            className="addrule"
            onClick={() =>
              setRules((rs) => [
                ...rs,
                {
                  id: `custom-${rs.length}`,
                  text: "",
                  category: "other",
                  direction: "negative",
                  sourceLine: "added by you",
                  checkerType: "forbidden_text",
                  checkerConfig: { strings: [], case_sensitive: false },
                  testable: true,
                  confidence: 1,
                  attacks: 0,
                  breaks: 0,
                },
              ])
            }
          >
            + Add a rule the extractor missed
          </button>
        </div>
      </div>

      <NextBar
        back="/paste"
        backLabel="Paste"
        next={`/e/${ex.slug}/questions`}
        nextLabel="Answer the open questions"
        note={`${ex.questions.filter((q) => q.status === "open" || q.status === "conflict").length} of these rules still have blanks in their checker.`}
      />
    </>
  );
}

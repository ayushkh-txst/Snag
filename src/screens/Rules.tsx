import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { NextBar, StepHead } from "../components/Shell";
import { SnagMark } from "../components/SnagMark";
import { Coverage } from "../components/ui";
import { CATEGORY_LABEL, byslug, checkerPlain, type Rule } from "../data";

type EditableRule = Rule & { addedByUser?: boolean };

function SourcePane({ prompt, active }: { prompt: string; active?: string }) {
  const lines = prompt.split("\n");
  return (
    <aside className="srcpane">
      <div className="srcpane__head">
        <span className="label">your prompt</span>
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

export function Rules() {
  const { slug } = useParams();
  const ex = byslug(slug);
  const [rules, setRules] = useState<EditableRule[]>(ex.rules);
  const [active, setActive] = useState<string | undefined>();
  const [draft, setDraft] = useState("");

  const c = useMemo(() => {
    const testable = rules.filter((r) => r.testable).length;
    return { total: rules.length, testable, eyes: rules.length - testable };
  }, [rules]);

  const needsDetail = (id: string) => ex.questions.some((q) => q.ruleId === id);

  const addRule = () => {
    const text = draft.trim();
    if (!text) return;
    setRules((rs) => [
      ...rs,
      {
        id: `you-${Date.now()}`,
        text,
        category: "other",
        direction: "negative",
        sourceLine: "",
        checkerType: "forbidden_text",
        checkerConfig: {},
        testable: true,
        confidence: 1,
        attacks: 0,
        breaks: 0,
        addedByUser: true,
      },
    ]);
    setDraft("");
  };

  return (
    <>
      <StepHead
        n="01"
        title="Here is what your prompt says."
        lede="Untick anything you don't want tested. Add anything the extractor missed."
        aside={<Coverage total={c.total} testable={c.testable} eyes={c.eyes} />}
      />

      <div className="doclayout">
        <SourcePane prompt={ex.systemPrompt} active={active} />

        <div className="doclayout__main">
          <ul className="rulelist">
            {rules.map((r, i) => (
              <li key={r.id}>
                <label
                  className="rule"
                  data-off={!r.testable || undefined}
                  onMouseEnter={() => setActive(r.sourceLine || undefined)}
                >
                  <input
                    type="checkbox"
                    className="rule__box"
                    checked={r.testable}
                    onChange={() =>
                      setRules((rs) =>
                        rs.map((x) => (x.id === r.id ? { ...x, testable: !x.testable } : x)),
                      )
                    }
                  />
                  <span className="rule__body">
                    <span className="rule__n mono">{String(i + 1).padStart(2, "0")}</span>
                    <span className="markwrap">
                      <span className="rule__text">{r.text}</span>
                      <SnagMark verdict={r.testable ? "held" : "eyes"} delay={i * 45} />
                    </span>
                    <span className="rule__how">
                      {r.checkerType === "none"
                        ? r.untestableReason
                        : checkerPlain(r)}
                    </span>
                    <span className="rule__tags">
                      <span className="chip">{CATEGORY_LABEL[r.category]}</span>
                      {r.direction === "positive" && <span className="chip" data-dir="positive">must refuse</span>}
                      {r.checkerType === "none" && <span className="chip" data-tone="eyes">needs your eyes</span>}
                      {r.testable && needsDetail(r.id) && (
                        <span className="chip" data-tone="eyes">needs one detail</span>
                      )}
                      {r.addedByUser && <span className="chip" data-tone="eyes">not in your prompt</span>}
                    </span>
                  </span>
                  <button
                    className="rule__del"
                    aria-label={`Remove rule ${i + 1}`}
                    onClick={(e) => {
                      e.preventDefault();
                      setRules((rs) => rs.filter((x) => x.id !== r.id));
                    }}
                  >
                    ×
                  </button>
                </label>
              </li>
            ))}
          </ul>

          <form
            className="addrule"
            onSubmit={(e) => {
              e.preventDefault();
              addRule();
            }}
          >
            <input
              className="addrule__in"
              value={draft}
              placeholder="Add a rule the extractor missed"
              onChange={(e) => setDraft(e.target.value)}
            />
            <button className="btn" data-variant="ghost" disabled={!draft.trim()}>
              Add
            </button>
          </form>
          <p className="addrule__note dim">
            A rule you add won't be in your prompt. Snag tests the behaviour anyway and
            marks it, so you can see whether the model does the right thing by accident.
          </p>
        </div>
      </div>

      <NextBar
        back="/paste"
        backLabel="Paste"
        next={`/e/${ex.slug}/questions`}
        nextLabel="Next"
      />
    </>
  );
}

import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { addRule, deleteRule, patchRule } from "../api/client";
import { NextBar, StepHead } from "../components/Shell";
import { SnagMark } from "../components/SnagMark";
import { ErrorState, Loading, NotFound } from "../components/States";
import { Coverage } from "../components/ui";
import { useProject } from "../hooks/useProject";
import { CATEGORY_LABEL, checkerPlain, type Rule } from "../data";

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
  const { data: ex, loading, error, notFound, refetch } = useProject(slug);
  const [rules, setRules] = useState<Rule[]>([]);
  const [active, setActive] = useState<string | undefined>();
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (ex) setRules(ex.rules);
  }, [ex]);

  const c = useMemo(() => {
    const testable = rules.filter((r) => r.testable).length;
    return { total: rules.length, testable, eyes: rules.length - testable };
  }, [rules]);

  if (loading) return <Loading label="Loading rules…" />;
  if (notFound) return <NotFound slug={slug} />;
  if (error) return <ErrorState error={error} />;
  if (!ex) return <Loading label="Loading rules…" />;

  const needsDetail = (id: string) => ex.questions.some((q) => q.ruleId === id);

  const markPending = (id: string, on: boolean) =>
    setPending((p) => {
      const next = new Set(p);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  const toggleTestable = (r: Rule) => {
    if (!slug) return;
    const next = !r.testable;
    setRules((rs) => rs.map((x) => (x.id === r.id ? { ...x, testable: next } : x)));
    markPending(r.id, true);
    patchRule(slug, r.id, { testable: next })
      .then((updated) => {
        setRules((rs) => rs.map((x) => (x.id === r.id ? updated : x)));
      })
      .catch(() => {
        setRules((rs) => rs.map((x) => (x.id === r.id ? { ...x, testable: !next } : x)));
      })
      .finally(() => markPending(r.id, false));
  };

  const removeRule = (r: Rule) => {
    if (!slug) return;
    const prev = rules;
    setRules((rs) => rs.filter((x) => x.id !== r.id));
    deleteRule(slug, r.id).catch(() => {
      setRules(prev);
    });
  };

  const addRuleFromDraft = () => {
    if (!slug) return;
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    addRule(slug, { text })
      .then((created) => {
        setRules((rs) => [...rs, created]);
      })
      .catch(() => void refetch());
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
                    disabled={pending.has(r.id)}
                    onChange={() => toggleTestable(r)}
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
                      {r.inPrompt === false && <span className="chip" data-tone="eyes">not in your prompt</span>}
                    </span>
                  </span>
                  <button
                    className="rule__del"
                    aria-label={`Remove rule ${i + 1}`}
                    onClick={(e) => {
                      e.preventDefault();
                      removeRule(r);
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
              addRuleFromDraft();
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
            A rule you add won't be in your prompt. Snag marks it so you can see it wasn't
            extracted from your text, and add a checker for it yourself if it needs one.
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

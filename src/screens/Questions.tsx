import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { NextBar, StepHead } from "../components/Shell";
import { byslug, type Question } from "../data";

const STATUS_WORD: Record<Question["status"], string> = {
  open: "waiting on you",
  answered: "answered",
  inferred: "inferred from your prompt",
  skipped: "skipped — reported as untestable",
  conflict: "contradiction — needs a decision",
};

const STYLES = [
  { k: "A list", v: "Nike, Adidas, New Balance", d: "passed through as written" },
  { k: "A description", v: "mostly the big sportswear brands, and that place on 5th", d: "normalized into a concrete list" },
  { k: "Nothing", v: "you pick · figure it out · (blank)", d: "inferred from the prompt's own context" },
  { k: "A refusal", v: "skip this one", d: "rule marked untestable and reported that way" },
];

export function Questions() {
  const { slug } = useParams();
  const ex = byslug(slug);
  const [answers, setAnswers] = useState<Record<string, string>>(() =>
    Object.fromEntries(ex.questions.map((q) => [q.id, q.answerRaw ?? ""])),
  );

  const rounds = useMemo(() => {
    const map = new Map<number, Question[]>();
    ex.questions.forEach((q) => {
      map.set(q.round, [...(map.get(q.round) ?? []), q]);
    });
    return [...map.entries()].sort((a, b) => a[0] - b[0]);
  }, [ex]);

  const ruleText = (id: string) => ex.rules.find((r) => r.id === id)?.text ?? "";
  const openCount = ex.questions.filter((q) => q.status === "open" || q.status === "conflict").length;

  return (
    <>
      <StepHead
        n="02"
        title="Some checkers still have blanks."
        lede="A rule like never mention competitors needs a list before code can check it. Answer however you like — a list, a sentence, or nothing at all. Whatever you type comes back as the literal thing that will be checked, before anything runs."
        aside={
          <div className="qstat">
            <div className="qstat__n mono">{ex.questions.length}</div>
            <div className="qstat__l">questions across {rounds.length} rounds</div>
          </div>
        }
      />

      <div className="styleband">
        {STYLES.map((s) => (
          <div className="styleband__col" key={s.k}>
            <div className="label">{s.k}</div>
            <div className="styleband__v mono">{s.v}</div>
            <div className="styleband__d">{s.d}</div>
          </div>
        ))}
      </div>

      {rounds.map(([n, qs]) => (
        <section className="round" key={n}>
          <header className="round__head">
            <span className="round__n mono">round {n}</span>
            <span className="round__rule" />
            <span className="mono dimmer">
              {n === 1
                ? "asked after the first extraction pass"
                : `raised by what you answered in round ${n - 1}`}
            </span>
          </header>

          <div className="qlist">
            {qs.map((q) => (
              <article className="qcard" key={q.id} data-status={q.status}>
                <div className="qcard__l">
                  <p className="qcard__for label">for rule</p>
                  <p className="qcard__rule">{ruleText(q.ruleId)}</p>
                </div>
                <div className="qcard__r">
                  <p className="qcard__q">{q.text}</p>

                  {q.status === "conflict" ? (
                    <div className="conflict">
                      <p className="conflict__tag mono">contradiction</p>
                      <p>{q.conflictNote}</p>
                      <div className="conflict__acts">
                        <button className="btn" data-variant="ghost">Confidentiality wins</button>
                        <button className="btn" data-variant="ghost">Summaries are allowed</button>
                        <button className="btn" data-variant="quiet">Skip this rule</button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <input
                        className="qcard__in mono"
                        value={answers[q.id] ?? ""}
                        placeholder={q.placeholder}
                        onChange={(e) => setAnswers((a) => ({ ...a, [q.id]: e.target.value }))}
                      />
                      <p className="qcard__status mono" data-status={q.status}>
                        {STATUS_WORD[q.status]}
                      </p>
                      {q.answerNormalized && (
                        <div className="normalized">
                          <p className="label">what will actually be checked</p>
                          <p className="normalized__v mono">{q.answerNormalized}</p>
                        </div>
                      )}
                      {q.status === "skipped" && (
                        <div className="normalized" data-tone="eyes">
                          <p className="label">what happens instead</p>
                          <p className="normalized__v mono">
                            checker_type = none · rule appears in the report under "needs your eyes"
                          </p>
                        </div>
                      )}
                    </>
                  )}
                </div>
              </article>
            ))}
          </div>
        </section>
      ))}

      <div className="roundstop">
        <p>
          Rounds stop after three, or when nothing is left — whichever comes first.
          {openCount > 0
            ? ` ${openCount} question${openCount === 1 ? "" : "s"} still open; leaving them will mark those rules untestable rather than guessing.`
            : " Nothing is left open."}
        </p>
      </div>

      <NextBar
        back={`/e/${ex.slug}/rules`}
        backLabel="Rules"
        next={`/e/${ex.slug}/surfaces`}
        nextLabel="Map the injection points"
      />
    </>
  );
}

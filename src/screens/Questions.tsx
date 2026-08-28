import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { NextBar, StepHead } from "../components/Shell";
import { byslug, type Example, type Question } from "../data";

function QuestionCard({
  q,
  ex,
  followUp = false,
}: {
  q: Question;
  ex: Example;
  followUp?: boolean;
}) {
  const [answer, setAnswer] = useState(q.answerRaw ?? "");
  const [confirmed, setConfirmed] = useState(Boolean(q.answerNormalized) || q.status === "skipped");
  const rule = ex.rules.find((r) => r.id === q.ruleId);

  if (q.status === "conflict") {
    return (
      <article className="q" data-followup={followUp || undefined}>
        <p className="q__rule">{rule?.text}</p>
        <p className="q__text">{q.text}</p>
        <div className="conflict">
          <p className="conflict__tag mono">your prompt says both</p>
          <p>{q.conflictNote}</p>
          <div className="conflict__acts">
            <button className="btn" data-variant="ghost">Keep it confidential</button>
            <button className="btn" data-variant="ghost">Allow a summary</button>
            <button className="btn" data-variant="quiet">Don't test this rule</button>
          </div>
        </div>
      </article>
    );
  }

  const skipped = answer.trim().toLowerCase().startsWith("skip");

  return (
    <article className="q" data-followup={followUp || undefined}>
      {followUp && <p className="q__from mono">raised by your last answer</p>}
      <p className="q__rule">{rule?.text}</p>
      <p className="q__text">{q.text}</p>

      <div className="q__row">
        <input
          className="q__in"
          value={answer}
          placeholder={q.placeholder}
          onChange={(e) => {
            setAnswer(e.target.value);
            setConfirmed(false);
          }}
        />
        <button
          className="btn"
          data-variant={confirmed ? "ghost" : "solid"}
          disabled={confirmed}
          onClick={() => setConfirmed(true)}
        >
          {confirmed ? "Confirmed" : "Confirm"}
        </button>
      </div>

      {confirmed ? (
        skipped || q.status === "skipped" ? (
          <div className="answer" data-tone="eyes">
            <span className="label">so this rule</span>
            <p>Won't be tested. It appears in the report under "needs your eyes".</p>
          </div>
        ) : (
          <div className="answer">
            <span className="label">
              {q.status === "inferred" ? "worked out from your prompt — change it if it's wrong" : "what gets checked"}
            </span>
            <p className="mono">{q.answerNormalized}</p>
          </div>
        )
      ) : (
        <p className="q__pending dim">
          Confirm to see the exact thing that will be checked.
        </p>
      )}
    </article>
  );
}

export function Questions() {
  const { slug } = useParams();
  const ex = byslug(slug);

  const { top, follows } = useMemo(() => {
    const first = new Map<string, Question>();
    const later: Question[] = [];
    [...ex.questions]
      .sort((a, b) => a.round - b.round)
      .forEach((q) => {
        if (first.has(q.ruleId)) later.push(q);
        else first.set(q.ruleId, q);
      });
    return { top: [...first.values()], follows: later };
  }, [ex]);

  return (
    <>
      <StepHead
        n="02"
        title="A few rules need one more detail."
        lede="Answer however you like — a list, a sentence, or nothing at all. Confirming turns your answer into the exact thing Snag will check."
        aside={
          <div className="qstat">
            <div className="qstat__n mono">{ex.questions.length}</div>
            <div className="qstat__l">to answer</div>
          </div>
        }
      />

      <div className="qlist">
        {top.map((q) => (
          <div className="qthread" key={q.id}>
            <QuestionCard q={q} ex={ex} />
            {follows
              .filter((f) => f.ruleId === q.ruleId)
              .map((f) => (
                <QuestionCard key={f.id} q={f} ex={ex} followUp />
              ))}
          </div>
        ))}
      </div>

      <p className="qfoot dim">
        Confirming an answer is one model call. Answering can raise a follow-up, which
        appears under the answer that raised it. Nothing is attacked until you start the
        scan.
      </p>

      <NextBar
        back={`/e/${ex.slug}/rules`}
        backLabel="Rules"
        next={`/e/${ex.slug}/surfaces`}
        nextLabel="Next"
      />
    </>
  );
}

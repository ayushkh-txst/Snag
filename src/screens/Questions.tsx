import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { answerQuestions } from "../api/client";
import { NextBar, StepHead } from "../components/Shell";
import { ErrorState, Loading, NotFound } from "../components/States";
import { useProject } from "../hooks/useProject";
import type { Example, Question } from "../data";

type AnswerFn = (q: Question, raw: string) => Promise<void>;

function QuestionCard({
  q,
  ex,
  onAnswer,
  followUp = false,
}: {
  q: Question;
  ex: Example;
  onAnswer: AnswerFn;
  followUp?: boolean;
}) {
  const [answer, setAnswer] = useState(q.answerRaw ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(false);
  const rule = ex.rules.find((r) => r.id === q.ruleId);
  const confirmed = q.status !== "open";

  const submit = (raw: string) => {
    setSubmitting(true);
    setSubmitError(false);
    onAnswer(q, raw)
      .catch(() => setSubmitError(true))
      .finally(() => setSubmitting(false));
  };

  if (q.status === "conflict") {
    return (
      <article className="q" data-followup={followUp || undefined}>
        <p className="q__rule">{rule?.text}</p>
        <p className="q__text">{q.text}</p>
        <div className="conflict">
          <p className="conflict__tag mono">your prompt says both</p>
          <p>{q.conflictNote}</p>
          <div className="conflict__acts">
            <button
              className="btn"
              data-variant="ghost"
              disabled={submitting}
              onClick={() => submit("keep it confidential")}
            >
              Keep it confidential
            </button>
            <button
              className="btn"
              data-variant="ghost"
              disabled={submitting}
              onClick={() => submit("allow a summary")}
            >
              Allow a summary
            </button>
            <button
              className="btn"
              data-variant="quiet"
              disabled={submitting}
              onClick={() => submit("skip")}
            >
              Don't test this rule
            </button>
          </div>
          {submitError && <p className="dim">Couldn't send that — try again.</p>}
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
          disabled={submitting}
          onChange={(e) => setAnswer(e.target.value)}
        />
        <button
          className="btn"
          data-variant={confirmed ? "ghost" : "solid"}
          disabled={confirmed || submitting}
          onClick={() => submit(answer)}
        >
          {submitting ? "Confirming…" : confirmed ? "Confirmed" : "Confirm"}
        </button>
      </div>
      {submitError && <p className="dim">Couldn't send that — try again.</p>}

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
  const { data: ex, loading, error, notFound, refetch } = useProject(slug);

  const { top, follows } = useMemo(() => {
    const first = new Map<string, Question>();
    const later: Question[] = [];
    [...(ex?.questions ?? [])]
      .sort((a, b) => a.round - b.round)
      .forEach((q) => {
        if (first.has(q.ruleId)) later.push(q);
        else first.set(q.ruleId, q);
      });
    return { top: [...first.values()], follows: later };
  }, [ex]);

  if (loading) return <Loading label="Loading questions…" />;
  if (notFound) return <NotFound slug={slug} />;
  if (error) return <ErrorState error={error} />;
  if (!ex) return <Loading label="Loading questions…" />;

  const handleAnswer: AnswerFn = async (q, raw) => {
    if (!slug) return;
    await answerQuestions(slug, [{ questionId: q.id, answerRaw: raw }]);
    // FOLLOWUP-01: a new round may have just opened — only a refetch of the
    // full question list can surface it (the mutation response only
    // describes the answer just given).
    await refetch();
  };

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
            <QuestionCard q={q} ex={ex} onAnswer={handleAnswer} />
            {follows
              .filter((f) => f.ruleId === q.ruleId)
              .map((f) => (
                <QuestionCard key={f.id} q={f} ex={ex} onAnswer={handleAnswer} followUp />
              ))}
          </div>
        ))}
        {top.length === 0 && (
          <p className="dim">Nothing needs a follow-up — every testable rule is fully specified.</p>
        )}
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

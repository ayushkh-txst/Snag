"""GET/POST /api/projects/{slug}/questions[/answers]: the follow-up round
flow. Open questions are grouped by rule with round numbers (FOLLOWUP-01);
answering a batch runs each raw answer through `normalize_answer` and
writes the resulting literal `checker_config` onto the rule, always
returned in the response so it can be shown back before anything runs
(FOLLOWUP-02); rounds stop at `ROUND_CAP` or when nothing is open,
whichever comes first (FOLLOWUP-03, T-08-03).
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from snag.api.app import ctx
from snag.api.deps import get_completions, require_slug, validate_model
from snag.followups import Normalized, group_open_questions, normalize_answer
from substrate.llm import Completions

log = structlog.get_logger(__name__)
router = APIRouter()

ROUND_CAP = 3
"""T-08-03: a hard server-side cap on follow-up rounds, so a chain of
answers that keeps raising new questions cannot loop forever."""

MAX_ANSWERS_PER_BATCH = 100
MAX_ANSWER_CHARS = 5_000


class QuestionOut(BaseModel):
    id: str
    ruleId: str
    round: int
    text: str
    placeholder: str | None = None
    answerRaw: str | None = None
    answerNormalized: str | None = None
    status: str
    conflictNote: str | None = None


class RuleQuestionsOut(BaseModel):
    ruleId: str
    questions: list[QuestionOut]


class QuestionsResponse(BaseModel):
    round: int
    """The highest round number among currently open questions, or 0 when
    nothing is open."""
    rules: list[RuleQuestionsOut]


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: int
    answer_raw: str = Field(default="", max_length=MAX_ANSWER_CHARS)
    """Free text in ANY style — an explicit list, prose, "you pick", "skip",
    or blank. Untrusted (T-08-01): normalize_answer is the only place this
    travels into a model call, and always as data."""


class AnswersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: list[AnswerIn] = Field(min_length=1, max_length=MAX_ANSWERS_PER_BATCH)


class AnsweredQuestionOut(BaseModel):
    questionId: str
    ruleId: str
    status: str
    checkerConfig: dict[str, Any]
    """Always present, always the literal thing that will be checked — the
    shown-back guarantee of FOLLOWUP-02. Empty for skipped/conflict."""
    conflictNote: str | None = None


class AnswersResponse(BaseModel):
    answered: list[AnsweredQuestionOut]
    round: int
    """The highest round touched while processing this batch (including any
    new round just opened)."""
    openRemaining: int
    roundsExhausted: bool
    """True when the round cap (ROUND_CAP) was hit while a follow-up
    question still would have been asked — the flow stopped on the cap,
    not because everything was resolved (FOLLOWUP-03)."""


def _question_out(row: asyncpg.Record) -> QuestionOut:
    return QuestionOut(
        id=str(row["id"]),
        ruleId=str(row["rule_id"]),
        round=row["round"],
        text=row["text"],
        placeholder=row["placeholder"],
        answerRaw=row["answer_raw"],
        answerNormalized=row["answer_normalized"],
        status=row["status"],
        conflictNote=row["conflict_note"],
    )


@router.get("/projects/{slug}/questions", response_model=QuestionsResponse)
async def get_questions(slug: str, request: Request) -> QuestionsResponse:
    await require_slug(request, slug)
    state = ctx(request)

    async with state.db.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM questions WHERE project_id = $1 AND status = 'open'
               ORDER BY round, rule_id, id""",
            slug,
        )

    grouped = group_open_questions(rows)
    rules_out = [
        RuleQuestionsOut(ruleId=str(rule_id), questions=[_question_out(r) for r in rule_rows])
        for rule_id, rule_rows in grouped.items()
    ]
    current_round = max((r["round"] for r in rows), default=0)
    return QuestionsResponse(round=current_round, rules=rules_out)


def _answered_out_from_row(row: asyncpg.Record) -> AnsweredQuestionOut:
    """An already-resolved question, shown back as-is — answering it again
    is a no-op rather than an error or a re-spent model call."""
    checker_config: dict[str, Any] = {}
    raw = row["answer_normalized"]
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                checker_config = parsed
        except (json.JSONDecodeError, TypeError):
            checker_config = {}
    return AnsweredQuestionOut(
        questionId=str(row["id"]),
        ruleId=str(row["rule_id"]),
        status=row["status"],
        checkerConfig=checker_config,
        conflictNote=row["conflict_note"],
    )


async def _apply_normalized(
    conn: asyncpg.Connection[Any],
    *,
    question_id: int,
    rule_id: int,
    answer_raw: str,
    normalized: Normalized,
) -> None:
    """Persist the normalized answer onto the question row, and — only when
    the answer is usable — the literal config onto the rule (FOLLOWUP-02).
    A skip marks the rule untestable; a conflict touches neither the rule's
    checker_config nor its testable flag, since nothing was resolved
    (T-08-02: never silently pick a side)."""
    await conn.execute(
        """UPDATE questions
               SET answer_raw = $1, answer_normalized = $2, status = $3, conflict_note = $4
           WHERE id = $5""",
        answer_raw,
        json.dumps(normalized.checker_config),
        normalized.status,
        normalized.conflict_note,
        question_id,
    )
    if normalized.status in ("answered", "inferred"):
        await conn.execute(
            "UPDATE rules SET checker_config = $1 WHERE id = $2",
            normalized.checker_config,
            rule_id,
        )
    elif normalized.status == "skipped":
        await conn.execute("UPDATE rules SET testable = false WHERE id = $1", rule_id)


@router.post("/projects/{slug}/questions/answers", response_model=AnswersResponse)
async def answer_questions(
    slug: str,
    body: AnswersRequest,
    request: Request,
    completions: Completions = Depends(get_completions),  # noqa: B008 - FastAPI DI idiom
) -> AnswersResponse:
    project = await require_slug(request, slug)
    state = ctx(request)
    model = project["model"]
    validate_model(model)  # KEY-03: before any completions call, even on a re-answer

    async with state.db.acquire() as conn:
        prompt_version = await conn.fetchrow(
            """SELECT * FROM prompt_versions WHERE project_id = $1
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            slug,
        )
    system_text = prompt_version["full_text"] if prompt_version else ""

    answered_out: list[AnsweredQuestionOut] = []
    highest_round_seen = 0
    rounds_capped = False

    for answer in body.answers:
        async with state.db.acquire() as conn:
            question = await conn.fetchrow(
                "SELECT * FROM questions WHERE id = $1 AND project_id = $2",
                answer.question_id,
                slug,
            )
        if question is None:
            raise HTTPException(
                status_code=404, detail=f"no such question: {answer.question_id}"
            )

        if question["status"] != "open":
            # Already resolved elsewhere (or in an earlier item of this same
            # batch) — show back what's on file rather than re-normalizing.
            answered_out.append(_answered_out_from_row(question))
            highest_round_seen = max(highest_round_seen, question["round"])
            continue

        highest_round_seen = max(highest_round_seen, question["round"])

        normalized = await normalize_answer(
            completions,
            question=question["text"],
            answer_raw=answer.answer_raw,
            system=system_text,
            model=model,
            run_id=f"followup:{slug}:{question['id']}",
        )

        new_round = question["round"] + 1
        raise_follow_ups = bool(normalized.follow_up_questions) and normalized.status in (
            "answered",
            "inferred",
        )

        async with state.db.acquire() as conn, conn.transaction():
            await _apply_normalized(
                conn,
                question_id=question["id"],
                rule_id=question["rule_id"],
                answer_raw=answer.answer_raw,
                normalized=normalized,
            )
            if raise_follow_ups:
                if new_round <= ROUND_CAP:
                    for follow_up_text in normalized.follow_up_questions:
                        await conn.execute(
                            """INSERT INTO questions (rule_id, project_id, round, text, status)
                                   VALUES ($1, $2, $3, $4, 'open')""",
                            question["rule_id"],
                            slug,
                            new_round,
                            follow_up_text,
                        )
                    highest_round_seen = max(highest_round_seen, new_round)
                else:
                    rounds_capped = True

        answered_out.append(
            AnsweredQuestionOut(
                questionId=str(question["id"]),
                ruleId=str(question["rule_id"]),
                status=normalized.status,
                checkerConfig=normalized.checker_config,
                conflictNote=normalized.conflict_note,
            )
        )

    async with state.db.acquire() as conn:
        open_remaining = await conn.fetchval(
            "SELECT count(*) FROM questions WHERE project_id = $1 AND status = 'open'", slug
        )

    log.info(
        "questions.answered",
        slug=slug,
        answered=len(answered_out),
        round=highest_round_seen,
        open_remaining=open_remaining,
    )
    return AnswersResponse(
        answered=answered_out,
        round=highest_round_seen,
        openRemaining=int(open_remaining),
        roundsExhausted=rounds_capped,
    )

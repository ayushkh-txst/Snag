"""The six authored example prompts (project-3-spec.md §11/§14), at fixed
slugs — five with a deliberately planted hole, one properly hardened.
`SEED_PROMPTS` is the ordered tuple `seed.seed_examples` iterates; each
module's `PROMPT` is also importable individually (tests key their scripted
responses off the exact `system_prompt`/rule text each one authors)."""

from __future__ import annotations

from snag.seed_prompts.base import SeedPromptSpec
from snag.seed_prompts.coding_agent import PROMPT as CODING_AGENT
from snag.seed_prompts.hardened_prompt import PROMPT as HARDENED_PROMPT
from snag.seed_prompts.healthcare_intake import PROMPT as HEALTHCARE_INTAKE
from snag.seed_prompts.hr_assistant import PROMPT as HR_ASSISTANT
from snag.seed_prompts.rag_assistant import PROMPT as RAG_ASSISTANT
from snag.seed_prompts.retail_support_bot import PROMPT as RETAIL_SUPPORT_BOT

SEED_PROMPTS: tuple[SeedPromptSpec, ...] = (
    RETAIL_SUPPORT_BOT,
    RAG_ASSISTANT,
    CODING_AGENT,
    HEALTHCARE_INTAKE,
    HR_ASSISTANT,
    HARDENED_PROMPT,
)

FIXED_SLUGS: tuple[str, ...] = tuple(p.slug for p in SEED_PROMPTS)

__all__ = [
    "CODING_AGENT",
    "FIXED_SLUGS",
    "HARDENED_PROMPT",
    "HEALTHCARE_INTAKE",
    "HR_ASSISTANT",
    "RAG_ASSISTANT",
    "RETAIL_SUPPORT_BOT",
    "SEED_PROMPTS",
    "SeedPromptSpec",
]

"""The shape one authored example needs (01-15): a system prompt, optional
tool definitions, and the scan shape `seed.py` drives the real pipeline
with. Six modules in this package each export one `SeedPromptSpec` at a
fixed slug; `snag.seed_prompts.SEED_PROMPTS` is the ordered tuple of all six.

`answers` is a best-effort map from a substring of an extracted rule's own
`text` to the free-text answer `seed.py` sends into `normalize_answer` for
any open question that rule raises — mirroring how a person would fill in
the Questions screen. A real extraction pass (the CLI path, a live model)
may phrase a rule differently than the fixed JSON a test scripts, so a
substring miss is expected sometimes, not a bug: `seed.py` falls back to
`fallback_answer` ("you pick") for anything unmatched, which
`followups.normalize_answer` resolves to a best-guess `status="inferred"`
rather than leaving the rule's `checker_config` empty forever.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class SeedPromptSpec:
    slug: str
    title: str
    blurb: str
    demonstrates: str
    model: str
    system_prompt: str
    tools_json: list[dict[str, Any]] | None
    surfaces: tuple[str, ...]
    """§6's surface categories this example's scan exercises — hand-picked
    per example rather than a preset mode, so the attack matrix an example
    produces stays small and legible (backend-feasibility.md)."""

    repeats: int = 1
    call_cap: int = 200
    spend_cap: Decimal = Decimal("2.00")
    answers: dict[str, str] = field(default_factory=dict)
    fallback_answer: str = "you pick — use your best judgement based on the system prompt"

    config_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Keyed the same way `answers` is (a lowercase substring of the
    extracted rule's own `text`): a dict of checker_config keys `seed.py`
    force-sets on that rule after extraction, overwriting whatever the
    model put there for those specific keys. Mirrors EXTRACT-03's own
    Rules-screen edit — a human completing or correcting a checker_config
    the extractor didn't quite get right — because a cheap/fast model has
    no worked example to imitate for several §4 checker types
    (`tool_arg_pattern` among them) and can invent a plausible-looking but
    non-conforming shape (found for real seeding this corpus, not
    hypothetically). Only listed keys are touched; anything else the model
    already got right is left alone."""

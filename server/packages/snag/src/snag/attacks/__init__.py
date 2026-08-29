"""The shared attack library (spec §7). `library` holds the hand-adapted
technique data; `instantiate` matches techniques to specific rules and
surfaces, deterministically; `seed_techniques` loads the library into the
`techniques` table. See `LICENCES.md` for source provenance.
"""

from __future__ import annotations

from snag.attacks.instantiate import Attack, Rule, Surface, instantiate
from snag.attacks.library import (
    FAMILIES,
    TECHNIQUE_BY_ID,
    TECHNIQUES,
    Direction,
    RuleCategory,
    SurfaceKind,
    Technique,
    needs_canary,
)
from snag.attacks.seed_techniques import seed_techniques

__all__ = [
    "FAMILIES",
    "TECHNIQUES",
    "TECHNIQUE_BY_ID",
    "Attack",
    "Direction",
    "Rule",
    "RuleCategory",
    "Surface",
    "SurfaceKind",
    "Technique",
    "instantiate",
    "needs_canary",
    "seed_techniques",
]

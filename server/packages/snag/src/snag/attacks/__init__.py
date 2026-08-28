"""The shared attack library (spec §7). `library` holds the hand-adapted
technique data; `seed_techniques` loads the library into the `techniques`
table. See `LICENCES.md` for source provenance. (`instantiate` — matching
techniques to specific rules and surfaces, deterministically — is added by
01-04 Task 2.)
"""

from __future__ import annotations

from snag.attacks.library import (
    FAMILIES,
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
    "Direction",
    "RuleCategory",
    "SurfaceKind",
    "Technique",
    "needs_canary",
    "seed_techniques",
]

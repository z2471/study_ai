"""ASCII cat catalog + selection utilities.

Kept intentionally tiny and dependency-free.
"""

from __future__ import annotations

import random

# NOTE: Keep cats ASCII-only to avoid font issues across platforms.
CATS: list[str] = [
    r""" /\_/\\
( o.o )
> ^ <
""",
    r""" |\---/|
 | o_o |
  \_^_/
""",
    r"""  /\_/\
 ( •.• )
 / >o
""",
]


def get_random_cat(rng: random.Random | None = None) -> str:
    """Return one ASCII cat (no trailing newline)."""

    rng = rng or random
    return rng.choice(CATS).rstrip("\n")

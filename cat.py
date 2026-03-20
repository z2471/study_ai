#!/usr/bin/env python3
"""Render an ASCII cat.

Run:
  python3 cat.py
"""


def render_cat() -> str:
    """Return the ASCII cat art.

    Note: We keep the art stable for tests; a trailing newline is included.
    """
    art = r"""
        /\_/\\
       ( o.o )
        > ^ <
       (=^.^=)

    CAT!
    """.strip("\n")
    return art + "\n"


def main() -> None:
    print(render_cat(), end="")


if __name__ == "__main__":
    main()

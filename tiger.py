#!/usr/bin/env python3
"""Print an ASCII tiger.

Run:
  python3 tiger.py
"""


def get_tiger_art() -> str:
    """Return the ASCII tiger art."""
    return r"""
               /\_/\\
              ( o.o )
               > ^ <

        _     _
       ( \-../ )
        \     /
        /     \\
       (       )
        \__ __/
          ||
          ||
         (||)

    TIGER!
    """.strip("\n")


def main() -> None:
    print(get_tiger_art())


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Print an ASCII tiger.

Run:
  python3 tiger.py
"""


def main() -> None:
    tiger = r"""
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

    print(tiger)


if __name__ == "__main__":
    main()

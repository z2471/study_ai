"""output_cat_gui: A tiny Tkinter GUI that shows an ASCII cat.

Run:
  - GUI:        python3 python/output_cat_gui/app.py
  - Headless:   python3 python/output_cat_gui/app.py --print

Why --print exists:
  Some environments (CI/SSH/headless) cannot open a Tk window. The --print
  mode provides a quick runnable verification path.
"""

from __future__ import annotations

import argparse
import random
import tkinter as tk
from tkinter import font


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
 / >🍪   
""",
]


def get_random_cat(rng: random.Random | None = None) -> str:
    """Return one ASCII cat."""

    rng = rng or random
    return rng.choice(CATS).rstrip("\n")


class CatApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("ASCII Cat")
        self.minsize(360, 220)

        self._cat_text = tk.StringVar(value=get_random_cat())

        # Use a monospace-ish default.
        mono = font.nametofont("TkFixedFont")

        self.label = tk.Label(
            self,
            textvariable=self._cat_text,
            font=mono,
            justify="left",
            anchor="nw",
            padx=16,
            pady=12,
        )
        self.label.pack(fill="both", expand=True)

        btns = tk.Frame(self)
        btns.pack(fill="x", padx=12, pady=(0, 12))

        self.refresh_btn = tk.Button(btns, text="Refresh", command=self.refresh)
        self.refresh_btn.pack(side="left")

        self.copy_btn = tk.Button(btns, text="Copy", command=self.copy_to_clipboard)
        self.copy_btn.pack(side="left", padx=(8, 0))

        self.quit_btn = tk.Button(btns, text="Quit", command=self.destroy)
        self.quit_btn.pack(side="right")

    def refresh(self) -> None:
        self._cat_text.set(get_random_cat())

    def copy_to_clipboard(self) -> None:
        text = self._cat_text.get()
        # Clipboard operations may throw if the window isn't in a valid state.
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Show an ASCII cat in a Tkinter GUI")
    p.add_argument(
        "--print",
        action="store_true",
        help="Print a random ASCII cat and exit (no GUI).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.print:
        print(get_random_cat())
        return 0

    app = CatApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Tkinter main window for output_cat_gui."""

from __future__ import annotations

import tkinter as tk
from tkinter import font

from cats.catalog import get_random_cat
from clipboard.ops import copy_to_clipboard


class CatApp(tk.Tk):
    """A tiny GUI that displays an ASCII cat with basic actions."""

    def __init__(self) -> None:
        super().__init__()

        self.title("ASCII Cat")
        self.minsize(360, 220)

        self._cat_text = tk.StringVar(value=get_random_cat())

        # Use a fixed-width font so ASCII art aligns.
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

        self.copy_btn = tk.Button(btns, text="Copy", command=self.copy)
        self.copy_btn.pack(side="left", padx=(8, 0))

        self.quit_btn = tk.Button(btns, text="Quit", command=self.destroy)
        self.quit_btn.pack(side="right")

    def refresh(self) -> None:
        self._cat_text.set(get_random_cat())

    def copy(self) -> None:
        copy_to_clipboard(self, self._cat_text.get())

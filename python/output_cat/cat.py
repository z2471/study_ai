"""ASCII Cat (GUI + CLI).

Default: launch a small GUI window that shows an ASCII cat.

Run:
  python cat.py          # GUI
  python cat.py --cli    # print to terminal
"""

from __future__ import annotations

import argparse


CAT_ART = r""" /\_/\\
( o.o )
> ^ <
"""


def run_cli() -> None:
    print(CAT_ART)


def run_gui() -> None:
    # Tkinter is part of Python stdlib on most desktop installs.
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("ASCII Cat")
    root.resizable(False, False)

    frm = ttk.Frame(root, padding=16)
    frm.grid(sticky="nsew")

    lbl = ttk.Label(frm, text=CAT_ART, justify="left", font=("Courier", 14))
    lbl.grid(row=0, column=0, columnspan=2)

    def copy_to_clipboard() -> None:
        root.clipboard_clear()
        root.clipboard_append(CAT_ART)

    ttk.Button(frm, text="Copy", command=copy_to_clipboard).grid(row=1, column=0, pady=(12, 0), sticky="ew")
    ttk.Button(frm, text="Quit", command=root.destroy).grid(row=1, column=1, pady=(12, 0), sticky="ew")

    frm.columnconfigure(0, weight=1)
    frm.columnconfigure(1, weight=1)

    root.mainloop()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Show an ASCII cat (GUI by default).")
    parser.add_argument("--cli", action="store_true", help="print to terminal instead of launching GUI")
    args = parser.parse_args(argv)

    if args.cli:
        run_cli()
    else:
        run_gui()


if __name__ == "__main__":
    main()

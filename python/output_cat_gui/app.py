"""output_cat_gui: A tiny Tkinter GUI that shows an ASCII cat.

Run:
  - GUI:        python3 python/output_cat_gui/app.py
  - Headless:   python3 python/output_cat_gui/app.py --print

Why --print exists:
  Some environments (CI/SSH/headless) cannot open a Tk window. The --print
  mode provides a quick runnable verification path.

Project structure note:
  This entrypoint stays minimal; functionality is split into feature folders:
  - cats/       cat selection
  - ui/         tkinter window
  - clipboard/  clipboard operations
"""

from __future__ import annotations

import argparse

from cats.catalog import get_random_cat


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

    # Delay Tkinter/UI imports so `--print` can run in headless environments
    # without importing tkinter.
    from ui.main_window import CatApp

    app = CatApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

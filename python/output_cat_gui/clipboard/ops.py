"""Clipboard operations.

Tkinter provides clipboard APIs on any widget/root window. We isolate it here so
UI code stays focused on layout.
"""

from __future__ import annotations

import tkinter as tk


def copy_to_clipboard(root: tk.Misc, text: str) -> None:
    """Copy text into system clipboard using an existing Tk instance."""

    root.clipboard_clear()
    root.clipboard_append(text)
    # Ensure clipboard data is persisted promptly.
    root.update_idletasks()

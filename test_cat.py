from cat import render_cat


def test_cat_contains_face_marker() -> None:
    art = render_cat()
    assert "=^.^=" in art


def test_cat_has_at_least_five_lines() -> None:
    art = render_cat()
    # splitlines() ignores the trailing newline (if present)
    assert len(art.splitlines()) >= 5


def test_cat_has_trailing_newline() -> None:
    art = render_cat()
    assert art.endswith("\n")

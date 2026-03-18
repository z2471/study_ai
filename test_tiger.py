from tiger import get_tiger_art


def test_tiger_contains_keyword() -> None:
    art = get_tiger_art()
    assert "TIGER!" in art

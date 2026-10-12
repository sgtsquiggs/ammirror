from ammirror.models import natural_sort_key


def ordered(names: list[str]) -> list[str]:
    return sorted(names, key=natural_sort_key)


def test_numbers_sort_by_value() -> None:
    assert ordered(["Mix 10", "Mix 2", "Mix 1"]) == ["Mix 1", "Mix 2", "Mix 10"]


def test_case_and_quote_style_do_not_matter() -> None:
    assert ordered(["beta", "Alpha", "\u201cGamma\u201d"]) == ["Alpha", "beta", "\u201cGamma\u201d"]


def test_leading_punctuation_is_ignored() -> None:
    names = ["Ambient", "2025: Heavy Rotation", "\u201893", "'90s Metal"]
    assert ordered(names) == ["'90s Metal", "\u201893", "2025: Heavy Rotation", "Ambient"]


def test_ties_are_broken_deterministically() -> None:
    assert ordered(["mix", "Mix"]) == ordered(["Mix", "mix"])

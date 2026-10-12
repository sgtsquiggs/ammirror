from ammirror.models import artwork_key, natural_sort_key


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


def test_artwork_key_ignores_per_request_signatures() -> None:
    first = "https://blob.example/a/image?X-Amz-Date=20260101T000000Z&X-Amz-Signature=aaa&v=1"
    second = "https://blob.example/a/image?X-Amz-Date=20260102T000000Z&X-Amz-Signature=bbb&v=1"
    assert artwork_key(first) == artwork_key(second) == "https://blob.example/a/image?v=1"


def test_artwork_key_keeps_other_urls_verbatim() -> None:
    url = "https://img.example/x/1200x1200AM.jpg?c1=EEEEEE&t=U2xlZXA%3D&signature=abc"
    assert artwork_key(url) == url
    assert artwork_key(url.replace("U2xlZXA", "R3lt")) != artwork_key(url)

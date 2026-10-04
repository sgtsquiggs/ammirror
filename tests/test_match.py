import pytest

from ammirror.match import (
    THRESHOLD,
    artist_names,
    choose,
    normalize_title,
    score,
    search_query,
)
from ammirror.models import Matched, Unmatched, UnmatchedReason
from tests.factories import cand, track


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Bohemian Rhapsody - Remastered 2011", "bohemian rhapsody"),
        ("Bohemian Rhapsody (Remastered 2011)", "bohemian rhapsody"),
        ("Here Comes the Sun - 2019 Mix", "here comes the sun 2019 mix"),
        ("Come Together [2009 Remaster]", "come together"),
        ("Old Town Road (feat. Billy Ray Cyrus) [Remix]", "old town road remix"),
        ("Señorita", "senorita"),
        ("Rock & Roll", "rock and roll"),
        ("Song (Live)", "song live"),
        ("  Lots   of   Space ", "lots of space"),
    ],
)
def test_normalize_title(raw: str, expected: str) -> None:
    assert normalize_title(raw) == expected


def test_artist_names_splits_collaborations() -> None:
    assert artist_names("Lil Nas X & Billy Ray Cyrus") >= {"lil nas x", "billy ray cyrus"}
    assert artist_names("Calvin Harris, Dua Lipa") >= {"calvin harris", "dua lipa"}
    assert "florence the machine" in artist_names("Florence + the Machine")


QUEEN = track(
    1,
    title="Bohemian Rhapsody - Remastered 2011",
    artist="Queen",
    album="A Night at the Opera",
    duration_ms=355_000,
)


def test_exact_match_scores_high() -> None:
    c = cand("v1", "Bohemian Rhapsody", ("Queen",), album="A Night at the Opera", duration_s=355)
    assert score(QUEEN, c) >= 0.95


def test_cover_scores_below_threshold() -> None:
    c = cand(
        "v2", "Bohemian Rhapsody", ("Panic! At The Disco",), album="Suicide Squad", duration_s=360
    )
    assert score(QUEEN, c) < THRESHOLD


def test_cover_at_similar_duration_scores_below_threshold() -> None:
    c = cand(
        "v2b",
        "Bohemian Rhapsody",
        ("Panic! At The Disco",),
        album="Suicide Squad",
        duration_s=355,
    )
    assert score(QUEEN, c) < THRESHOLD


def test_live_version_scores_below_threshold() -> None:
    c = cand("v3", "Bohemian Rhapsody (Live Aid)", ("Queen",), album="Live Aid", duration_s=140)
    assert score(QUEEN, c) < THRESHOLD


def test_live_version_with_same_duration_scores_below_threshold() -> None:
    c = cand("v3b", "Bohemian Rhapsody (Live Aid)", ("Queen",), album="Live Aid", duration_s=355)
    assert score(QUEEN, c) < THRESHOLD


def test_live_version_with_unknown_duration_scores_below_threshold() -> None:
    c = cand("v3c", "Bohemian Rhapsody (Live Aid)", ("Queen",), album="Live Aid", duration_s=None)
    assert score(QUEEN, c) < THRESHOLD


def test_unknown_duration_can_still_match() -> None:
    c = cand("v4", "Bohemian Rhapsody", ("Queen",), album=None, duration_s=None)
    assert score(QUEEN, c) >= THRESHOLD


def test_choose_picks_best() -> None:
    results = [
        cand("cover", "Bohemian Rhapsody", ("Panic! At The Disco",), duration_s=360),
        cand("real", "Bohemian Rhapsody", ("Queen",), album="A Night at the Opera", duration_s=355),
    ]
    res = choose(QUEEN, results)
    assert isinstance(res, Matched)
    assert res.video_id == "real"
    assert res.method == "search"


def test_choose_same_song_on_two_albums_is_not_ambiguous() -> None:
    results = [
        cand(
            "album", "Bohemian Rhapsody", ("Queen",), album="A Night at the Opera", duration_s=355
        ),
        cand("hits", "Bohemian Rhapsody", ("Queen",), album="Greatest Hits", duration_s=355),
    ]
    res = choose(QUEEN, results)
    assert isinstance(res, Matched)
    assert res.video_id == "album"


def test_remix_vs_remix_not_ambiguous() -> None:
    t = track(4, title="Song (Remix)", artist="Artist", album="", duration_ms=200_000)
    results = [
        cand("v1", "Song (Remix)", ("Artist",), album=None, duration_s=200),
        cand("v2", "Song (Remix)", ("Artist",), album=None, duration_s=202),
    ]
    res = choose(t, results)
    assert isinstance(res, Matched)


def test_choose_ambiguous_when_close_scores_differ_in_title() -> None:
    from ammirror.match import MARGIN

    t = track(2, title="Intro", artist="The xx", album="", duration_ms=128_000)
    a = cand("a", "Intro", ("The xx",), album=None, duration_s=133)
    b = cand("b", "Intros", ("The xx",), album=None, duration_s=128)
    assert abs(score(t, a) - score(t, b)) < MARGIN
    res = choose(t, [a, b])
    assert isinstance(res, Unmatched)
    assert res.reason == UnmatchedReason.AMBIGUOUS


def test_choose_no_results() -> None:
    assert choose(QUEEN, []) == Unmatched(UnmatchedReason.NO_RESULTS)


def test_choose_low_score_keeps_candidate() -> None:
    c = cand("v2", "Something Else", ("Nobody",), duration_s=10)
    res = choose(QUEEN, [c])
    assert isinstance(res, Unmatched)
    assert res.reason == UnmatchedReason.LOW_SCORE
    assert res.candidate == c
    assert res.candidate_score is not None


def test_search_query_strips_feat() -> None:
    t = track(3, title="Old Town Road (feat. Billy Ray Cyrus) [Remix]", artist="Lil Nas X")
    assert search_query(t) == "Lil Nas X Old Town Road [Remix]"

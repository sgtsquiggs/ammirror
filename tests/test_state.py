import stat
from pathlib import Path

import pytest

from ammirror.models import Matched, PlaylistMapping, Unmatched, UnmatchedReason
from ammirror.state import State
from tests.factories import cand, track


@pytest.fixture
def state(tmp_path: Path) -> State:
    return State(tmp_path / "s" / "state.db")


def test_db_file_is_private(tmp_path: Path) -> None:
    db = tmp_path / "s" / "state.db"
    State(db).close()
    assert stat.S_IMODE(db.stat().st_mode) == 0o600


def test_reopen_keeps_data(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    s = State(db)
    s.put_match("1", "vid1", 0.9, "search")
    s.close()
    assert State(db).get_match("1") == Matched("vid1", 0.9, "search")


def test_match_roundtrip_and_pin_overrides(state: State) -> None:
    assert state.get_match("1") is None
    state.put_match("1", "vidA", 0.81, "search")
    assert state.get_match("1") == Matched("vidA", 0.81, "search")
    state.put_match("1", "vidB", 1.0, "pin")
    assert state.get_match("1") == Matched("vidB", 1.0, "pin")


def test_unmatched_roundtrip(state: State) -> None:
    t = track(7, title="Rare B-side", artist="Band")
    c = cand("vidX", "Rare B-side (Live)")
    state.put_unmatched(t, Unmatched(UnmatchedReason.LOW_SCORE, c, 0.5))
    assert state.get_unmatched("7") == Unmatched(UnmatchedReason.LOW_SCORE, c, 0.5)
    rows = state.list_unmatched()
    assert len(rows) == 1
    row = rows[0]
    assert (row.apple_id, row.title, row.artist, row.reason) == (
        "7",
        "Rare B-side",
        "Band",
        "low_score",
    )
    assert (row.candidate_video_id, row.candidate_title, row.candidate_score) == (
        "vidX",
        "Rare B-side (Live)",
        0.5,
    )
    state.clear_unmatched("7")
    assert state.get_unmatched("7") is None
    assert state.list_unmatched() == []


def test_unmatched_without_candidate(state: State) -> None:
    t = track(8, catalog=False)
    state.put_unmatched(t, Unmatched(UnmatchedReason.NO_CATALOG))
    assert state.get_unmatched(t.key) == Unmatched(UnmatchedReason.NO_CATALOG)


def test_playlist_mappings(state: State) -> None:
    state.put_playlist("p.1", "PLx", "Gym")
    state.put_playlist("p.1", "PLx", "Gym 2")
    state.put_playlist("p.2", "PLy", "Chill")
    assert state.playlist_mappings() == {
        "p.1": PlaylistMapping("p.1", "PLx", "Gym 2"),
        "p.2": PlaylistMapping("p.2", "PLy", "Chill"),
    }
    state.drop_playlist("p.1")
    assert set(state.playlist_mappings()) == {"p.2"}


def test_owned_likes(state: State) -> None:
    state.add_owned_like("v1", "1")
    state.add_owned_like("v2", "2")
    assert state.owned_likes() == {"v1": "1", "v2": "2"}
    state.remove_owned_like("v1")
    assert state.owned_likes() == {"v2": "2"}

from pathlib import Path

import pytest

from ammirror.config import SyncConfig
from ammirror.match import search_query
from ammirror.models import (
    AddItems,
    ApplePlaylist,
    CreatePlaylist,
    Like,
    Matched,
    Plan,
    Unmatched,
    UnmatchedReason,
    YtmPlaylist,
)
from ammirror.state import State
from ammirror.sync import apply_plan, resolve_tracks, run_sync
from tests.factories import cand, track
from tests.fakes import FakeApple, FakeYtm

GYM = ApplePlaylist("p.gym", "Gym")


@pytest.fixture
def state(tmp_path: Path) -> State:
    return State(tmp_path / "state.db")


def ytm_for(*tracks) -> FakeYtm:
    """FakeYtm whose search returns an exact match for each given track."""
    return FakeYtm(
        search_results={
            search_query(t): [cand(f"v{t.catalog_id}", t.title, (t.artist,), duration_s=200)]
            for t in tracks
        }
    )


def test_resolve_searches_once_then_uses_cache(state: State) -> None:
    t = track(1)
    ytm = ytm_for(t)
    res, warnings = resolve_tracks([t, t], state, ytm)
    got = res["1"]
    assert isinstance(got, Matched)
    assert res == {"1": Matched("v1", got.score, "search")}
    assert warnings == []
    assert [c[0] for c in ytm.calls] == ["search_songs"]
    resolve_tracks([t], state, ytm)
    assert [c[0] for c in ytm.calls] == ["search_songs"]


def test_resolve_pin_wins(state: State) -> None:
    t = track(1)
    state.put_match("1", "pinned", 1.0, "pin")
    res, _ = resolve_tracks([t], state, ytm_for(t))
    assert res["1"] == Matched("pinned", 1.0, "pin")


def test_resolve_no_catalog_is_unmatched_without_search(state: State) -> None:
    t = track(1, catalog=False)
    ytm = FakeYtm()
    res, _ = resolve_tracks([t], state, ytm)
    assert res[t.key] == Unmatched(UnmatchedReason.NO_CATALOG)
    assert ytm.calls == []
    assert state.get_unmatched(t.key) == Unmatched(UnmatchedReason.NO_CATALOG)


def test_resolve_unmatched_is_cached_unless_retry(state: State) -> None:
    t = track(1)
    ytm = FakeYtm()
    resolve_tracks([t], state, ytm)
    resolve_tracks([t], state, ytm)
    assert len(ytm.calls) == 1
    resolve_tracks([t], state, ytm, retry_unmatched=True)
    assert len(ytm.calls) == 2


def test_resolve_match_clears_unmatched(state: State) -> None:
    t = track(1)
    resolve_tracks([t], state, FakeYtm())
    resolve_tracks([t], state, ytm_for(t), retry_unmatched=True)
    assert state.get_unmatched("1") is None


def test_resolve_search_failure_is_a_warning(state: State) -> None:
    t = track(1)
    res, warnings = resolve_tracks([t], state, FakeYtm(fail_on={"search_songs"}))
    assert res == {}
    assert len(warnings) == 1


def test_apply_create_records_mapping_and_adds(state: State) -> None:
    ytm = FakeYtm()
    result = apply_plan(Plan((CreatePlaylist("p.gym", "Gym", "Gym", ("v1", "v2")),)), ytm, state)
    assert result.applied == 1 and result.failures == []
    pid = state.playlist_mappings()["p.gym"].ytm_playlist_id
    assert [i.video_id for i in ytm.playlists[pid].items] == ["v1", "v2"]


def test_apply_like_records_ownership(state: State) -> None:
    ytm = FakeYtm()
    apply_plan(Plan((Like("v1", "1"),)), ytm, state)
    assert ytm.liked == {"v1"}
    assert state.owned_likes() == {"v1": "1"}


def test_apply_failure_continues(state: State) -> None:
    ytm = FakeYtm(fail_on={"add_items"})
    ytm.playlists["PL"] = YtmPlaylist("PL", "Gym", ())
    plan = Plan((AddItems("PL", "Gym", ("v1",)), Like("v2", "2")))
    result = apply_plan(plan, ytm, state)
    assert result.applied == 1
    assert len(result.failures) == 1
    assert ytm.liked == {"v2"}


def test_run_sync_end_to_end_and_idempotent(state: State) -> None:
    t1, t2, fav = track(1), track(2), track(3)
    apple = FakeApple(playlists=[(GYM, [t1, t2])], favorites=[fav])
    ytm = ytm_for(t1, t2, fav)
    cfg = SyncConfig()

    report = run_sync(cfg, apple, ytm, state)
    assert report.result is not None and report.result.failures == []
    pid = state.playlist_mappings()["p.gym"].ytm_playlist_id
    assert [i.video_id for i in ytm.playlists[pid].items] == ["v1", "v2"]
    assert ytm.liked == {"v3"}

    again = run_sync(cfg, apple, ytm, state)
    assert again.plan.ops == ()


def test_run_sync_dry_run_changes_nothing_on_ytm(state: State) -> None:
    t1 = track(1)
    ytm = ytm_for(t1)
    report = run_sync(SyncConfig(), FakeApple(playlists=[(GYM, [t1])]), ytm, state, dry_run=True)
    assert report.result is None
    assert len(report.plan.ops) == 1
    assert ytm.playlists == {}
    assert state.playlist_mappings() == {}


def test_run_sync_removal_and_missing_names(state: State) -> None:
    t1, t2 = track(1), track(2)
    ytm = ytm_for(t1, t2)
    apple = FakeApple(playlists=[(GYM, [t1, t2])])
    cfg = SyncConfig(playlists=("Gym", "Nope"), likes=False)
    run_sync(cfg, apple, ytm, state)
    apple.playlists = [(GYM, [t1])]
    report = run_sync(cfg, apple, ytm, state)
    pid = state.playlist_mappings()["p.gym"].ytm_playlist_id
    assert [i.video_id for i in ytm.playlists[pid].items] == ["v1"]
    assert any("Nope" in w for w in report.warnings)


def test_run_sync_skips_failing_playlist_without_forgetting(state: State) -> None:
    t1 = track(1)
    ytm = ytm_for(t1)
    apple = FakeApple(playlists=[(GYM, [t1])])
    run_sync(SyncConfig(likes=False), apple, ytm, state)
    apple.failing_playlists = {"p.gym"}
    report = run_sync(SyncConfig(likes=False), apple, ytm, state)
    assert "p.gym" in state.playlist_mappings()
    assert any("Gym" in w for w in report.warnings)


def test_run_sync_ytm_playlist_read_failure_skips_playlist(state: State) -> None:
    t1 = track(1)
    ytm = ytm_for(t1)
    apple = FakeApple(playlists=[(GYM, [t1])])
    run_sync(SyncConfig(likes=False), apple, ytm, state)
    ytm.fail_on = {"get_playlist"}
    report = run_sync(SyncConfig(likes=False), apple, ytm, state)
    assert report.plan.ops == ()
    assert any("Gym" in w for w in report.warnings)


def test_run_sync_counts_unmatched(state: State) -> None:
    t1 = track(1)
    report = run_sync(SyncConfig(likes=False), FakeApple(playlists=[(GYM, [t1])]), FakeYtm(), state)
    assert report.unmatched == 1


def test_run_sync_unselected_playlist_keeps_mapping_and_reuses_mirror(state: State) -> None:
    t1 = track(1)
    ytm = ytm_for(t1)
    other = ApplePlaylist("p.other", "Other")
    apple = FakeApple(playlists=[(GYM, [t1]), (other, [])])
    run_sync(SyncConfig(playlists=("Gym",), likes=False), apple, ytm, state)
    pid = state.playlist_mappings()["p.gym"].ytm_playlist_id

    unselected = run_sync(SyncConfig(playlists=("Other",), likes=False), apple, ytm, state)
    assert all(
        not isinstance(op, CreatePlaylist) or op.apple_playlist_id != "p.gym"
        for op in unselected.plan.ops
    )
    assert state.playlist_mappings()["p.gym"].ytm_playlist_id == pid

    again = run_sync(SyncConfig(playlists=("Gym",), likes=False), apple, ytm, state)
    assert again.plan.ops == ()
    assert [c for c in ytm.calls if c[0] == "create_playlist"] == [
        ("create_playlist", "Gym"),
        ("create_playlist", "Other"),
    ]

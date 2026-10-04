import logging
from pathlib import Path

import pytest

from ammirror.config import LikesMode, SyncConfig
from ammirror.errors import AuthError
from ammirror.match import search_query
from ammirror.models import (
    AddItems,
    ApplePlaylist,
    CreatePlaylist,
    ForgetLike,
    Like,
    Matched,
    Plan,
    RenamePlaylist,
    Unlike,
    Unmatched,
    UnmatchedReason,
    YtmPlaylist,
    YtmPlaylistItem,
)
from ammirror.state import State
from ammirror.sync import LIKE_VERIFY_DELAY, apply_plan, resolve_tracks, run_sync
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


def ytm_for_library(t, video_id: str) -> FakeYtm:
    """FakeYtm that returns an exact match for one track, whatever its catalog id."""
    return FakeYtm(
        search_results={search_query(t): [cand(video_id, t.title, (t.artist,), duration_s=200)]}
    )


def test_resolve_no_catalog_is_searched_and_cached(state: State) -> None:
    t = track(1, catalog=False)
    ytm = ytm_for_library(t, "vlib")
    res, errors = resolve_tracks([t], state, ytm)
    got = res[t.key]
    assert isinstance(got, Matched)
    assert got.video_id == "vlib" and got.method == "search"
    assert errors == []
    assert state.get_match(t.library_id) == got
    resolve_tracks([t], state, ytm)
    assert [c[0] for c in ytm.calls] == ["search_songs"]


def test_resolve_no_catalog_without_results_is_no_results(state: State) -> None:
    t = track(1, catalog=False)
    ytm = FakeYtm()
    res, _ = resolve_tracks([t], state, ytm)
    assert res[t.key] == Unmatched(UnmatchedReason.NO_RESULTS)
    assert state.get_unmatched(t.library_id) == Unmatched(UnmatchedReason.NO_RESULTS)
    resolve_tracks([t], state, ytm)
    assert [c[0] for c in ytm.calls] == ["search_songs"]


def test_resolve_stored_no_catalog_row_does_not_suppress_search(state: State) -> None:
    t = track(1, catalog=False)
    state.put_unmatched(t, Unmatched(UnmatchedReason.NO_CATALOG))
    ytm = FakeYtm()
    res, _ = resolve_tracks([t], state, ytm)
    assert [c[0] for c in ytm.calls] == ["search_songs"]
    assert res[t.key] == Unmatched(UnmatchedReason.NO_RESULTS)
    assert state.get_unmatched(t.library_id) == Unmatched(UnmatchedReason.NO_RESULTS)


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


def test_resolve_search_failure_is_an_error(state: State) -> None:
    t = track(1)
    res, errors = resolve_tracks([t], state, FakeYtm(fail_on={"search_songs"}))
    assert res == {}
    assert errors == ["search failed for Artist - Song 1: fake failure in search_songs"]


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


def test_apply_rename_updates_title_and_mapping(state: State) -> None:
    ytm = FakeYtm()
    ytm.playlists["PL"] = YtmPlaylist("PL", "Gym", ())
    state.put_playlist("p.gym", "PL", "Gym")
    result = apply_plan(
        Plan((RenamePlaylist("p.gym", "PL", "Lifting", "AM: Lifting"),)), ytm, state
    )
    assert result.applied == 1 and result.failures == []
    assert ytm.playlists["PL"].title == "AM: Lifting"
    assert state.playlist_mappings()["p.gym"].apple_name == "Lifting"


def test_apply_unlike_drops_ownership(state: State) -> None:
    ytm = FakeYtm(liked={"v1", "v2"})
    state.add_owned_like("v1", "1")
    result = apply_plan(Plan((Unlike("v1"),)), ytm, state)
    assert result.applied == 1
    assert ytm.liked == {"v2"}
    assert state.owned_likes() == {}


def test_apply_forget_like_touches_only_state(state: State) -> None:
    ytm = FakeYtm()
    state.add_owned_like("v1", "1")
    result = apply_plan(Plan((ForgetLike("v1"),)), ytm, state)
    assert result.applied == 1
    assert ytm.calls == []
    assert state.owned_likes() == {}


def test_apply_auth_error_aborts_instead_of_collecting(state: State) -> None:
    ytm = FakeYtm(auth_fail_on={"add_items"})
    ytm.playlists["PL"] = YtmPlaylist("PL", "Gym", (YtmPlaylistItem("v9", "set-v9"),))
    plan = Plan((AddItems("PL", "Gym", ("v1",)), Like("v2", "2")))
    with pytest.raises(AuthError):
        apply_plan(plan, ytm, state)
    assert ytm.liked == set()
    assert state.owned_likes() == {}


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


def test_run_sync_add_only_keeps_unfavorited_likes_until_mirror(
    state: State, caplog: pytest.LogCaptureFixture
) -> None:
    t1, t2 = track(1), track(2)
    apple = FakeApple(playlists=[(GYM, [])], favorites=[t1, t2])
    ytm = ytm_for(t1, t2)
    add_only = SyncConfig(likes=LikesMode.ADD_ONLY)
    run_sync(add_only, apple, ytm, state)
    assert ytm.liked == {"v1", "v2"}

    apple.favorites = [t1]
    with caplog.at_level(logging.DEBUG, logger="ammirror"):
        report = run_sync(add_only, apple, ytm, state)
    assert report.plan.ops == ()
    assert ytm.liked == {"v1", "v2"}
    assert state.owned_likes() == {"v1": "1", "v2": "2"}
    assert "likes: add-only" in caplog.text
    assert "1 unlike skipped (add-only)" in caplog.text

    report = run_sync(SyncConfig(), apple, ytm, state)
    assert report.plan.ops == (Unlike("v2"),)
    assert ytm.liked == {"v1"}
    assert state.owned_likes() == {"v1": "1"}


def test_run_sync_likes_favorite_without_catalog_id(state: State) -> None:
    fav = track(1, catalog=False)
    apple = FakeApple(playlists=[(GYM, [])], favorites=[fav])
    ytm = ytm_for_library(fav, "vlib")
    report = run_sync(SyncConfig(), apple, ytm, state)
    assert report.result is not None and report.result.failures == []
    assert ytm.liked == {"vlib"}
    assert state.owned_likes() == {"vlib": fav.library_id}


def test_run_sync_passes_favorites_playlist_name(state: State) -> None:
    apple = FakeApple(playlists=[(GYM, [])], favorites=[track(1)])
    run_sync(SyncConfig(favorites_playlist="Lieblingssongs"), apple, ytm_for(track(1)), state)
    assert apple.favorites_requests == ["Lieblingssongs"]


def test_run_sync_favorites_failure_is_an_error_and_skips_likes(state: State) -> None:
    t1 = track(1)
    apple = FakeApple(playlists=[(GYM, [t1])], favorites_error="no favorites playlist")
    ytm = ytm_for(t1)
    ytm.liked = {"vOLD"}
    state.add_owned_like("vOLD", "99")
    report = run_sync(SyncConfig(), apple, ytm, state)
    assert report.errors == ["no favorites playlist"]
    assert report.result is not None and report.result.failures == []
    pid = state.playlist_mappings()["p.gym"].ytm_playlist_id
    assert [i.video_id for i in ytm.playlists[pid].items] == ["v1"]
    assert not any(isinstance(op, Like | Unlike | ForgetLike) for op in report.plan.ops)
    assert ytm.liked == {"vOLD"}
    assert state.owned_likes() == {"vOLD": "99"}
    assert "liked_video_ids" not in [c[0] for c in ytm.calls]


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
    cfg = SyncConfig(playlists=("Gym", "Nope"), likes=LikesMode.OFF)
    run_sync(cfg, apple, ytm, state)
    apple.playlists = [(GYM, [t1])]
    report = run_sync(cfg, apple, ytm, state)
    pid = state.playlist_mappings()["p.gym"].ytm_playlist_id
    assert [i.video_id for i in ytm.playlists[pid].items] == ["v1"]
    assert report.warnings == ["no Apple Music playlist named 'Nope'"]
    assert report.errors == []


def test_run_sync_skips_failing_playlist_without_forgetting(state: State) -> None:
    t1 = track(1)
    ytm = ytm_for(t1)
    apple = FakeApple(playlists=[(GYM, [t1])])
    run_sync(SyncConfig(likes=LikesMode.OFF), apple, ytm, state)
    apple.failing_playlists = {"p.gym"}
    report = run_sync(SyncConfig(likes=LikesMode.OFF), apple, ytm, state)
    assert "p.gym" in state.playlist_mappings()
    assert report.errors == ["skipped 'Gym': fake failure for p.gym"]
    assert report.warnings == []


def test_run_sync_ytm_playlist_read_failure_skips_playlist(state: State) -> None:
    t1 = track(1)
    ytm = ytm_for(t1)
    apple = FakeApple(playlists=[(GYM, [t1])])
    run_sync(SyncConfig(likes=LikesMode.OFF), apple, ytm, state)
    ytm.fail_on = {"get_playlist"}
    report = run_sync(SyncConfig(likes=LikesMode.OFF), apple, ytm, state)
    assert report.plan.ops == ()
    assert report.errors == ["skipped 'Gym': fake failure in get_playlist"]
    assert report.warnings == []


def test_run_sync_search_failure_is_an_error(state: State) -> None:
    apple = FakeApple(playlists=[(GYM, [track(1)])])
    ytm = FakeYtm(fail_on={"search_songs"})
    report = run_sync(SyncConfig(likes=LikesMode.OFF), apple, ytm, state, dry_run=True)
    assert len(report.errors) == 1 and "search failed" in report.errors[0]
    assert report.warnings == []


def test_run_sync_counts_unmatched(state: State) -> None:
    t1 = track(1)
    report = run_sync(
        SyncConfig(likes=LikesMode.OFF), FakeApple(playlists=[(GYM, [t1])]), FakeYtm(), state
    )
    assert report.unmatched == 1


def test_run_sync_unselected_playlist_keeps_mapping_and_reuses_mirror(state: State) -> None:
    t1 = track(1)
    ytm = ytm_for(t1)
    other = ApplePlaylist("p.other", "Other")
    apple = FakeApple(playlists=[(GYM, [t1]), (other, [])])
    run_sync(SyncConfig(playlists=("Gym",), likes=LikesMode.OFF), apple, ytm, state)
    pid = state.playlist_mappings()["p.gym"].ytm_playlist_id

    unselected = run_sync(SyncConfig(playlists=("Other",), likes=LikesMode.OFF), apple, ytm, state)
    assert all(
        not isinstance(op, CreatePlaylist) or op.apple_playlist_id != "p.gym"
        for op in unselected.plan.ops
    )
    assert state.playlist_mappings()["p.gym"].ytm_playlist_id == pid

    again = run_sync(SyncConfig(playlists=("Gym",), likes=LikesMode.OFF), apple, ytm, state)
    assert again.plan.ops == ()
    assert [c for c in ytm.calls if c[0] == "create_playlist"] == [
        ("create_playlist", "Gym"),
        ("create_playlist", "Other"),
    ]


def _names(ytm: FakeYtm) -> list[str]:
    return [c[0] for c in ytm.calls]


def _like_setup(n: int = 3) -> tuple[list, FakeApple, FakeYtm]:
    favs = [track(i) for i in range(1, n + 1)]
    return favs, FakeApple(playlists=[(GYM, [])], favorites=favs), ytm_for(*favs)


def test_verify_likes_all_stuck(state: State) -> None:
    _, apple, ytm = _like_setup()
    sleeps: list[float] = []
    report = run_sync(SyncConfig(), apple, ytm, state, sleep=sleeps.append)
    assert report.errors == []
    assert sleeps == [LIKE_VERIFY_DELAY]
    assert _names(ytm).count("liked_video_ids") == 2
    assert "like_status" not in _names(ytm)
    assert _names(ytm)[-1] == "liked_video_ids"


def test_verify_likes_reports_dropped_and_keeps_ownership(state: State) -> None:
    favs, apple, ytm = _like_setup()
    ytm.drop_likes = {"v2"}
    report = run_sync(SyncConfig(), apple, ytm, state, sleep=lambda _s: None)
    assert report.errors == [
        f"like didn't stick on YouTube Music: {favs[1].artist} - {favs[1].title} (v2); "
        "run `ammirror sync` again"
    ]
    assert [c for c in ytm.calls if c[0] == "like_status"] == [("like_status", "v2")]
    assert state.owned_likes() == {"v1": "1", "v2": "2", "v3": "3"}
    ytm.drop_likes = set()
    again = run_sync(SyncConfig(), apple, ytm, state, sleep=lambda _s: None)
    assert again.plan.ops == (Like("v2", "2"),)
    assert again.errors == []


def test_verify_likes_missing_from_list_but_liked_is_not_flagged(state: State) -> None:
    _, apple, ytm = _like_setup()
    ytm.hidden_likes = {"v1"}
    report = run_sync(SyncConfig(), apple, ytm, state, sleep=lambda _s: None)
    assert report.errors == []
    assert [c for c in ytm.calls if c[0] == "like_status"] == [("like_status", "v1")]


def test_verify_likes_unknown_status_is_not_flagged(state: State) -> None:
    _, apple, ytm = _like_setup()
    ytm.drop_likes = {"v1"}
    ytm.unknown_status = {"v1"}
    report = run_sync(SyncConfig(), apple, ytm, state, sleep=lambda _s: None)
    assert report.errors == []


def test_verify_likes_read_failure_is_one_error(state: State) -> None:
    _, apple, ytm = _like_setup()

    class FailsSecondRead(FakeYtm):
        def liked_video_ids(self) -> set[str]:
            if self.calls.count(("liked_video_ids",)) >= 1:
                self.fail_on = {"liked_video_ids"}
            return super().liked_video_ids()

    ytm2 = FailsSecondRead(search_results=ytm.search_results)
    report = run_sync(SyncConfig(), apple, ytm2, state, sleep=lambda _s: None)
    assert len(report.errors) == 1
    assert report.errors[0].startswith("could not verify likes:")
    assert "like_status" not in _names(ytm2)


def test_verify_likes_auth_error_propagates(state: State) -> None:
    _, apple, ytm = _like_setup()

    class AuthOnSecondRead(FakeYtm):
        def liked_video_ids(self) -> set[str]:
            if self.calls.count(("liked_video_ids",)) >= 1:
                self.auth_fail_on = {"liked_video_ids"}
            return super().liked_video_ids()

    with pytest.raises(AuthError):
        run_sync(
            SyncConfig(),
            apple,
            AuthOnSecondRead(search_results=ytm.search_results),
            state,
            sleep=lambda _s: None,
        )


def test_verify_likes_skipped_on_dry_run(state: State) -> None:
    _, apple, ytm = _like_setup()
    sleeps: list[float] = []
    run_sync(SyncConfig(), apple, ytm, state, dry_run=True, sleep=sleeps.append)
    assert sleeps == []
    assert _names(ytm).count("liked_video_ids") == 1
    assert "like_status" not in _names(ytm)


def test_verify_likes_skipped_without_applied_likes(state: State) -> None:
    t1 = track(1)
    apple = FakeApple(playlists=[(GYM, [t1])], favorites=[])
    ytm = ytm_for(t1)
    sleeps: list[float] = []
    run_sync(SyncConfig(), apple, ytm, state, sleep=sleeps.append)
    assert sleeps == []
    assert _names(ytm).count("liked_video_ids") == 1


def test_verify_likes_skipped_when_likes_off_or_like_failed(state: State) -> None:
    _, apple, ytm = _like_setup()
    sleeps: list[float] = []
    run_sync(SyncConfig(likes=LikesMode.OFF), apple, ytm, state, sleep=sleeps.append)
    assert sleeps == []
    ytm.fail_on = {"like"}
    report = run_sync(SyncConfig(), apple, ytm, state, sleep=sleeps.append)
    assert sleeps == []
    assert report.result is not None and len(report.result.failures) == 3
    assert report.errors == []

from ammirror.models import (
    AddItems,
    ApplePlaylist,
    CreatePlaylist,
    ForgetLike,
    Like,
    Matched,
    Plan,
    PlaylistMapping,
    RemoveItems,
    RenamePlaylist,
    Resolution,
    Unlike,
    Unmatched,
    UnmatchedReason,
    YtmPlaylist,
    YtmPlaylistItem,
)
from ammirror.sync import AppleSnapshot, YtmSnapshot, describe, plan_sync, select_playlists
from tests.factories import track

GYM = ApplePlaylist("p.gym", "Gym")
T1, T2, T3 = track(1), track(2), track(3)
RES = {
    "1": Matched("v1", 0.9, "search"),
    "2": Matched("v2", 0.9, "search"),
    "3": Matched("v3", 0.9, "search"),
}


def item(v: str) -> YtmPlaylistItem:
    return YtmPlaylistItem(v, f"set-{v}")


def plan(
    apple: AppleSnapshot,
    ytm: YtmSnapshot,
    mappings: dict[str, PlaylistMapping] | None = None,
    owned: dict[str, str] | None = None,
    res: dict[str, Resolution] | None = None,
    prefix: str = "",
) -> Plan:
    return plan_sync(
        apple,
        RES if res is None else res,
        ytm,
        mappings or {},
        owned or {},
        prefix=prefix,
    )


def test_select_playlists() -> None:
    a, b, c = ApplePlaylist("1", "A"), ApplePlaylist("2", "B"), ApplePlaylist("3", "A")
    assert select_playlists([a, b, c], ["*"]) == ([a, b, c], [])
    assert select_playlists([a, b, c], ["A", "Z"]) == ([a, c], ["Z"])


def test_select_playlists_dedupes_preserving_order() -> None:
    a, b = ApplePlaylist("1", "A"), ApplePlaylist("2", "B")
    assert select_playlists([a, b], ["A", "A"]) == ([a], [])
    assert select_playlists([a, b], ["B", "A", "B"]) == ([b, a], [])
    assert select_playlists([a, b], ["*", "A"]) == ([a, b], [])
    assert select_playlists([a, b, a], ["*"]) == ([a, b], [])


def test_new_playlist_is_created_with_matched_tracks_in_order() -> None:
    unmatched = {**RES, "2": Unmatched(UnmatchedReason.LOW_SCORE)}
    apple = AppleSnapshot(((GYM, (T3, T1, T2, T1)),), None)
    p = plan(apple, YtmSnapshot({}, frozenset()), res=unmatched, prefix="AM: ")
    assert p.ops == (CreatePlaylist("p.gym", "Gym", "AM: Gym", ("v3", "v1")),)


def test_existing_playlist_gets_adds_and_removes() -> None:
    apple = AppleSnapshot(((GYM, (T1, T2)),), None)
    ytm = YtmSnapshot({"PL": YtmPlaylist("PL", "Gym", (item("v1"), item("v9")))}, frozenset())
    p = plan(apple, ytm, {"p.gym": PlaylistMapping("p.gym", "PL", "Gym")})
    assert p.ops == (
        AddItems("PL", "Gym", ("v2",)),
        RemoveItems("PL", "Gym", (item("v9"),)),
    )


def test_in_sync_playlist_has_no_ops() -> None:
    apple = AppleSnapshot(((GYM, (T1,)),), None)
    ytm = YtmSnapshot({"PL": YtmPlaylist("PL", "Gym", (item("v1"),))}, frozenset())
    assert plan(apple, ytm, {"p.gym": PlaylistMapping("p.gym", "PL", "Gym")}).ops == ()


def test_rename_when_title_differs() -> None:
    apple = AppleSnapshot(((ApplePlaylist("p.gym", "Lifting"), (T1,)),), None)
    ytm = YtmSnapshot({"PL": YtmPlaylist("PL", "Gym", (item("v1"),))}, frozenset())
    p = plan(apple, ytm, {"p.gym": PlaylistMapping("p.gym", "PL", "Gym")})
    assert p.ops == (RenamePlaylist("p.gym", "PL", "Lifting", "Lifting"),)


def test_missing_mirror_is_recreated_with_warning() -> None:
    apple = AppleSnapshot(((GYM, (T1,)),), None)
    ytm = YtmSnapshot({"PL": None}, frozenset())
    p = plan(apple, ytm, {"p.gym": PlaylistMapping("p.gym", "PL", "Gym")})
    assert p.ops == (CreatePlaylist("p.gym", "Gym", "Gym", ("v1",)),)
    assert any("recreat" in w for w in p.warnings)


def test_mapping_of_playlist_not_in_snapshot_is_left_alone() -> None:
    apple = AppleSnapshot((), None)
    p = plan(apple, YtmSnapshot({}, frozenset()), {"p.old": PlaylistMapping("p.old", "PLo", "Old")})
    assert p.ops == ()


def test_likes_added_only_when_not_already_liked() -> None:
    apple = AppleSnapshot((), (T1, T2))
    p = plan(apple, YtmSnapshot({}, frozenset({"v2"})))
    assert p.ops == (Like("v1", "1"),)


def test_owned_like_removed_when_unfavorited() -> None:
    apple = AppleSnapshot((), (T1,))
    p = plan(apple, YtmSnapshot({}, frozenset({"v1", "v2", "v7"})), owned={"v1": "1", "v2": "2"})
    assert p.ops == (Unlike("v2"),)  # v7 is the user's own like: untouched


def test_owned_like_already_gone_is_forgotten() -> None:
    apple = AppleSnapshot((), ())
    p = plan(apple, YtmSnapshot({}, frozenset()), owned={"v1": "1"})
    assert p.ops == (ForgetLike("v1"),)


def test_likes_disabled_touches_nothing() -> None:
    apple = AppleSnapshot((), None)
    p = plan(apple, YtmSnapshot({}, frozenset({"v1"})), owned={"v1": "1"})
    assert p.ops == ()


def test_owned_like_whose_favorite_is_now_unmatched() -> None:
    apple = AppleSnapshot((), (T1,))
    unmatched: dict[str, Resolution] = {**RES, "1": Unmatched(UnmatchedReason.LOW_SCORE)}
    p = plan(apple, YtmSnapshot({}, frozenset()), owned={"v1": "1"}, res=unmatched)
    assert p.ops == ()


def test_owned_like_whose_favorite_is_rematched_to_different_video() -> None:
    apple = AppleSnapshot((), (T1,))
    rematched: dict[str, Resolution] = {**RES, "1": Matched("v3", 0.9, "pin")}
    p = plan(
        apple,
        YtmSnapshot({}, frozenset({"v1"})),
        owned={"v1": "1"},
        res=rematched,
    )
    assert p.ops == (Like("v3", "1"), Unlike("v1"))


def test_owned_like_rematched_but_old_video_not_on_ytm() -> None:
    apple = AppleSnapshot((), (T1,))
    rematched: dict[str, Resolution] = {**RES, "1": Matched("v3", 0.9, "pin")}
    p = plan(apple, YtmSnapshot({}, frozenset()), owned={"v1": "1"}, res=rematched)
    assert p.ops == (Like("v3", "1"), ForgetLike("v1"))


def test_owned_like_still_favorited_but_not_yet_liked() -> None:
    apple = AppleSnapshot((), (T1,))
    p = plan(apple, YtmSnapshot({}, frozenset()), owned={"v1": "1"})
    assert p.ops == (Like("v1", "1"),)


def test_prefix_on_existing_mirror_triggers_rename() -> None:
    apple = AppleSnapshot(((GYM, (T1,)),), None)
    ytm = YtmSnapshot({"PL": YtmPlaylist("PL", "Gym", (item("v1"),))}, frozenset())
    p = plan(apple, ytm, {"p.gym": PlaylistMapping("p.gym", "PL", "Gym")}, prefix="AM: ")
    assert p.ops == (RenamePlaylist("p.gym", "PL", "Gym", "AM: Gym"),)


def test_describe() -> None:
    cp = CreatePlaylist("p", "Gym", "Gym", ("a", "b"))
    assert describe(cp) == "create playlist 'Gym' with 2 tracks"
    ai = AddItems("PL", "Gym", ("a",))
    assert describe(ai) == "add 1 track to 'Gym'"
    ri = RemoveItems("PL", "Gym", (item("a"), item("b")))
    assert describe(ri) == "remove 2 tracks from 'Gym'"
    rp = RenamePlaylist("p", "PL", "New", "New")
    assert describe(rp) == "rename playlist PL to 'New'"
    lk = Like("v1", "1")
    assert describe(lk) == "like v1"
    uk = Unlike("v1")
    assert describe(uk) == "unlike v1"
    fl = ForgetLike("v1")
    assert describe(fl) == "forget like v1 (already removed on YouTube Music)"

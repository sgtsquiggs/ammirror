from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass

from ammirror.models import (
    AddItems,
    ApplePlaylist,
    AppleTrack,
    CreatePlaylist,
    ForgetLike,
    ForgetPlaylist,
    Like,
    Matched,
    Op,
    Plan,
    PlaylistMapping,
    RemoveItems,
    RenamePlaylist,
    Resolution,
    Unlike,
    YtmPlaylist,
)


@dataclass(frozen=True)
class AppleSnapshot:
    playlists: tuple[tuple[ApplePlaylist, tuple[AppleTrack, ...]], ...]
    favorites: tuple[AppleTrack, ...] | None  # None when likes are disabled


@dataclass(frozen=True)
class YtmSnapshot:
    playlists: Mapping[str, YtmPlaylist | None]  # by YTM playlist id; None = gone
    liked: frozenset[str]


def select_playlists(
    available: Sequence[ApplePlaylist], patterns: Sequence[str]
) -> tuple[list[ApplePlaylist], list[str]]:
    """Return (selected playlists, configured names that matched nothing)."""
    if "*" in patterns:
        return list(available), []
    selected: list[ApplePlaylist] = []
    missing: list[str] = []
    for name in patterns:
        hits = [p for p in available if p.name == name]
        if hits:
            selected.extend(hits)
        else:
            missing.append(name)
    return selected, missing


def _video_ids(
    tracks: Sequence[AppleTrack], resolutions: Mapping[str, Resolution]
) -> tuple[str, ...]:
    ids: dict[str, None] = {}
    for t in tracks:
        res = resolutions.get(t.key)
        if isinstance(res, Matched):
            ids.setdefault(res.video_id)
    return tuple(ids)


def plan_sync(
    apple: AppleSnapshot,
    resolutions: Mapping[str, Resolution],
    ytm: YtmSnapshot,
    mappings: Mapping[str, PlaylistMapping],
    owned_likes: Mapping[str, str],
    *,
    managed_ids: AbstractSet[str],
    prefix: str = "",
) -> Plan:
    ops: list[Op] = []
    warnings: list[str] = []

    for playlist, tracks in apple.playlists:
        title = prefix + playlist.name
        desired = _video_ids(tracks, resolutions)
        mapping = mappings.get(playlist.id)
        current = ytm.playlists[mapping.ytm_playlist_id] if mapping else None
        if current is None:
            if mapping:
                warnings.append(f"mirror of '{playlist.name}' is gone on YouTube Music; recreating")
            ops.append(CreatePlaylist(playlist.id, playlist.name, title, desired))
            continue
        if current.title != title:
            ops.append(RenamePlaylist(playlist.id, current.id, playlist.name, title))
        present = {i.video_id for i in current.items}
        wanted = set(desired)
        adds = tuple(v for v in desired if v not in present)
        removes = tuple(i for i in current.items if i.video_id not in wanted)
        if adds:
            ops.append(AddItems(current.id, title, adds))
        if removes:
            ops.append(RemoveItems(current.id, title, removes))

    for apple_id, mapping in mappings.items():
        if apple_id not in managed_ids:
            ops.append(ForgetPlaylist(apple_id, mapping.apple_name))

    if apple.favorites is not None:
        favorites: dict[str, str] = {}
        for t in apple.favorites:
            res = resolutions.get(t.key)
            if isinstance(res, Matched):
                favorites.setdefault(res.video_id, t.key)
        for video_id, apple_id in favorites.items():
            if video_id not in ytm.liked:
                ops.append(Like(video_id, apple_id))
        favorite_keys = {t.key for t in apple.favorites}
        for video_id, apple_id in owned_likes.items():
            if video_id in favorites:
                continue
            if apple_id in favorite_keys:
                res = resolutions.get(apple_id)
                if not isinstance(res, Matched) or res.video_id == video_id:
                    continue
                ops.append(Unlike(video_id))
                continue
            ops.append(Unlike(video_id) if video_id in ytm.liked else ForgetLike(video_id))

    return Plan(tuple(ops), tuple(warnings))


def _tracks(n: int) -> str:
    return f"{n} track{'s' if n != 1 else ''}"


def describe(op: Op) -> str:
    match op:
        case CreatePlaylist(title=title, video_ids=ids):
            return f"create playlist '{title}' with {_tracks(len(ids))}"
        case RenamePlaylist(ytm_playlist_id=pid, title=title):
            return f"rename playlist {pid} to '{title}'"
        case AddItems(title=title, video_ids=ids):
            return f"add {_tracks(len(ids))} to '{title}'"
        case RemoveItems(title=title, items=items):
            return f"remove {_tracks(len(items))} from '{title}'"
        case ForgetPlaylist(apple_name=name):
            return f"stop mirroring '{name}' (YouTube Music copy kept)"
        case Like(video_id=vid):
            return f"like {vid}"
        case Unlike(video_id=vid):
            return f"unlike {vid}"
        case ForgetLike(video_id=vid):
            return f"forget like {vid} (already removed on YouTube Music)"

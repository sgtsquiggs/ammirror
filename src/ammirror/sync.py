from collections.abc import Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field

from ammirror.apple.client import AppleLibrary
from ammirror.config import SyncConfig
from ammirror.errors import ServiceError
from ammirror.match import choose, search_query
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
    Unmatched,
    UnmatchedReason,
    YtmPlaylist,
)
from ammirror.state import State
from ammirror.ytm.client import YtmClient


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


def resolve_tracks(
    tracks: Iterable[AppleTrack],
    state: State,
    ytm: YtmClient,
    *,
    retry_unmatched: bool = False,
) -> tuple[dict[str, Resolution], list[str]]:
    """Map each track key to a resolution: pin/cache first, then search."""
    resolutions: dict[str, Resolution] = {}
    warnings: list[str] = []
    for t in tracks:
        if t.key in resolutions:
            continue
        cached = state.get_match(t.key)
        if cached:
            resolutions[t.key] = cached
            continue
        if t.catalog_id is None:
            result: Resolution = Unmatched(UnmatchedReason.NO_CATALOG)
        else:
            previous = None if retry_unmatched else state.get_unmatched(t.key)
            if previous is not None:
                resolutions[t.key] = previous
                continue
            try:
                result = choose(t, ytm.search_songs(search_query(t)))
            except ServiceError as e:
                warnings.append(f"search failed for {t.artist} - {t.title}: {e}")
                continue
        if isinstance(result, Matched):
            state.put_match(t.key, result.video_id, result.score, result.method)
            state.clear_unmatched(t.key)
        else:
            state.put_unmatched(t, result)
        resolutions[t.key] = result
    return resolutions, warnings


@dataclass
class ApplyResult:
    applied: int = 0
    failures: list[tuple[Op, str]] = field(default_factory=list)


def _apply_one(op: Op, ytm: YtmClient, state: State) -> None:
    match op:
        case CreatePlaylist(apple_playlist_id=aid, apple_name=name, title=title, video_ids=ids):
            pid = ytm.create_playlist(title)
            state.put_playlist(aid, pid, name)
            if ids:
                ytm.add_items(pid, ids)
        case RenamePlaylist(
            apple_playlist_id=aid, ytm_playlist_id=pid, apple_name=name, title=title
        ):
            ytm.rename_playlist(pid, title)
            state.put_playlist(aid, pid, name)
        case AddItems(ytm_playlist_id=pid, video_ids=ids):
            ytm.add_items(pid, ids)
        case RemoveItems(ytm_playlist_id=pid, items=items):
            ytm.remove_items(pid, items)
        case ForgetPlaylist(apple_playlist_id=aid):
            state.drop_playlist(aid)
        case Like(video_id=vid, apple_id=aid):
            ytm.like(vid)
            state.add_owned_like(vid, aid)
        case Unlike(video_id=vid):
            ytm.unlike(vid)
            state.remove_owned_like(vid)
        case ForgetLike(video_id=vid):
            state.remove_owned_like(vid)


def apply_plan(plan: Plan, ytm: YtmClient, state: State) -> ApplyResult:
    """Apply ops in order. Auth errors abort; other service errors are collected."""
    result = ApplyResult()
    for op in plan.ops:
        try:
            _apply_one(op, ytm, state)
        except ServiceError as e:
            result.failures.append((op, str(e)))
            continue
        result.applied += 1
    return result


@dataclass
class SyncReport:
    plan: Plan
    result: ApplyResult | None
    warnings: list[str]
    unmatched: int


def run_sync(
    cfg: SyncConfig,
    apple: AppleLibrary,
    ytm: YtmClient,
    state: State,
    *,
    dry_run: bool = False,
    retry_unmatched: bool = False,
) -> SyncReport:
    warnings: list[str] = []
    selected, missing = select_playlists(apple.library_playlists(), cfg.playlists)
    warnings += [f"no Apple Music playlist named '{name}'" for name in missing]

    mappings = state.playlist_mappings()
    fetched: list[tuple[ApplePlaylist, tuple[AppleTrack, ...]]] = []
    ytm_playlists: dict[str, YtmPlaylist | None] = {}
    for p in selected:
        try:
            tracks = tuple(apple.playlist_tracks(p.id))
        except ServiceError as e:
            warnings.append(f"skipped '{p.name}': {e}")
            continue
        mapping = mappings.get(p.id)
        if mapping:
            try:
                ytm_playlists[mapping.ytm_playlist_id] = ytm.get_playlist(mapping.ytm_playlist_id)
            except ServiceError as e:
                warnings.append(f"skipped '{p.name}': {e}")
                continue
        fetched.append((p, tracks))

    favorites = tuple(apple.favorite_songs()) if cfg.likes else None
    all_tracks = [t for _, ts in fetched for t in ts] + list(favorites or ())
    resolutions, search_warnings = resolve_tracks(
        all_tracks, state, ytm, retry_unmatched=retry_unmatched
    )
    warnings += search_warnings
    liked = frozenset(ytm.liked_video_ids()) if cfg.likes else frozenset()

    plan = plan_sync(
        AppleSnapshot(tuple(fetched), favorites),
        resolutions,
        YtmSnapshot(ytm_playlists, liked),
        mappings,
        state.owned_likes(),
        managed_ids={p.id for p in selected},
        prefix=cfg.mirror_prefix,
    )
    warnings += plan.warnings
    unmatched = sum(isinstance(r, Unmatched) for r in resolutions.values())
    if dry_run:
        return SyncReport(plan, None, warnings, unmatched)
    return SyncReport(plan, apply_plan(plan, ytm, state), warnings, unmatched)

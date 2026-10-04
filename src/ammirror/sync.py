import logging
from collections.abc import Iterable, Mapping, Sequence
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
    normalize_playlist_name,
)
from ammirror.state import State
from ammirror.ytm.client import YtmClient

log = logging.getLogger(__name__)


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
    """Return (selected playlists, configured names that matched nothing).

    Each playlist is selected at most once, in first-seen order.
    """
    hits: list[ApplePlaylist] = []
    missing: list[str] = []
    if "*" in patterns:
        hits = list(available)
    else:
        for name in patterns:
            wanted = normalize_playlist_name(name)
            named = [p for p in available if normalize_playlist_name(p.name) == wanted]
            if not named:
                missing.append(name)
            hits.extend(named)
    selected: dict[str, ApplePlaylist] = {}
    for p in hits:
        selected.setdefault(p.id, p)
    return list(selected.values()), missing


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
    prefix: str = "",
) -> Plan:
    """Plan the ops that bring YouTube Music in line with the Apple snapshot.

    Mappings for playlists not in the snapshot (unselected, or skipped this run) are
    left alone, so a playlist selected again later reuses its existing mirror.
    """
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
            log.debug(
                "plan '%s': desired %d, %s",
                playlist.name,
                len(desired),
                "mirror gone, recreate" if mapping else "no mirror yet, create",
            )
            ops.append(CreatePlaylist(playlist.id, playlist.name, title, desired))
            continue
        if current.title != title:
            ops.append(RenamePlaylist(playlist.id, current.id, playlist.name, title))
        present = {i.video_id for i in current.items}
        wanted = set(desired)
        adds = tuple(v for v in desired if v not in present)
        removes = tuple(i for i in current.items if i.video_id not in wanted)
        log.debug(
            "plan '%s': desired %d, present %d, add %d, remove %d",
            playlist.name,
            len(desired),
            len(present),
            len(adds),
            len(removes),
        )
        if adds:
            ops.append(AddItems(current.id, title, adds))
        if removes and not tracks:
            # An empty read wiping a whole mirror is far more likely an Apple glitch
            # than a playlist the user emptied; leave the mirror alone this run.
            warnings.append(f"'{playlist.name}' returned no tracks from Apple; skipping removals")
        elif removes:
            ops.append(RemoveItems(current.id, title, removes))

    if apple.favorites is not None:
        favorites: dict[str, str] = {}
        for t in apple.favorites:
            res = resolutions.get(t.key)
            if isinstance(res, Matched):
                favorites.setdefault(res.video_id, t.key)
        likes_before = len(ops)
        for video_id, apple_id in favorites.items():
            if video_id not in ytm.liked:
                ops.append(Like(video_id, apple_id))
        to_like = len(ops) - likes_before
        kept_unresolved = 0
        favorite_keys = {t.key for t in apple.favorites}
        if not apple.favorites and owned_likes:
            # Same guard as for playlists: never unlike everything on an empty read.
            warnings.append("Apple returned no favorites; skipping like removals")
            owned_likes = {}
        for video_id, apple_id in owned_likes.items():
            if video_id in favorites:
                continue
            if apple_id in favorite_keys:
                res = resolutions.get(apple_id)
                if not isinstance(res, Matched) or res.video_id == video_id:
                    kept_unresolved += 1
                    continue
            ops.append(Unlike(video_id) if video_id in ytm.liked else ForgetLike(video_id))
        log.debug(
            "plan likes: %d to like, %d to unlike, %d to forget, %d kept (still favorited)",
            to_like,
            sum(isinstance(o, Unlike) for o in ops),
            sum(isinstance(o, ForgetLike) for o in ops),
            kept_unresolved,
        )

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
    """Map each track key to a resolution: pin/cache first, then search.

    Returns the resolutions and an error message per failed search.
    """
    resolutions: dict[str, Resolution] = {}
    errors: list[str] = []
    cached_n = pinned_n = searched_n = 0
    for t in tracks:
        if t.key in resolutions:
            continue
        label = f"{t.artist} - {t.title}"
        cached = state.get_match(t.key)
        if cached:
            if cached.method == "pin":
                pinned_n += 1
                log.debug("pin: %s → %s", label, cached.video_id)
            else:
                cached_n += 1
                log.debug("cache hit: %s → %s (score %.2f)", label, cached.video_id, cached.score)
            resolutions[t.key] = cached
            continue
        if t.catalog_id is None:
            result: Resolution = Unmatched(UnmatchedReason.NO_CATALOG)
        else:
            previous = None if retry_unmatched else state.get_unmatched(t.key)
            if previous is not None:
                cached_n += 1
                log.debug("previously unmatched, skipping search: %s", label)
                resolutions[t.key] = previous
                continue
            query = search_query(t)
            log.debug("searching: %r for %s", query, label)
            try:
                result = choose(t, ytm.search_songs(query))
            except ServiceError as e:
                errors.append(f"search failed for {t.artist} - {t.title}: {e}")
                continue
            searched_n += 1
        if isinstance(result, Matched):
            log.debug("matched: %s → %s (score %.2f)", label, result.video_id, result.score)
            state.put_match(t.key, result.video_id, result.score, result.method)
            state.clear_unmatched(t.key)
        else:
            best = result.candidate
            log.debug(
                "unmatched: %s (%s)%s",
                label,
                result.reason,
                f"; best: {best.title!r} score {result.candidate_score:.2f}"
                if best and result.candidate_score is not None
                else "",
            )
            state.put_unmatched(t, result)
        resolutions[t.key] = result
    n_unmatched = sum(isinstance(r, Unmatched) for r in resolutions.values())
    log.info(
        "resolved %d tracks: %d cached, %d pinned, %d searched → %d matched, %d unmatched",
        len(resolutions),
        cached_n,
        pinned_n,
        searched_n,
        len(resolutions) - n_unmatched,
        n_unmatched,
    )
    return resolutions, errors


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
        log.info("→ %s", describe(op))
        try:
            _apply_one(op, ytm, state)
        except ServiceError as e:
            # INFO, not WARNING: the CLI already prints every failure to the user.
            log.info("  failed: %s", e)
            result.failures.append((op, str(e)))
            continue
        result.applied += 1
    return result


@dataclass
class SyncReport:
    plan: Plan
    result: ApplyResult | None
    warnings: list[str]  # informational; the run still succeeded
    unmatched: int
    errors: list[str] = field(default_factory=list)  # skipped playlists, failed searches


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
    errors: list[str] = []
    available = apple.library_playlists()
    selected, missing = select_playlists(available, cfg.playlists)
    log.info(
        "found %d Apple Music playlists; %d selected: %s",
        len(available),
        len(selected),
        ", ".join(f"'{p.name}'" for p in selected) or "none",
    )
    warnings += [f"no Apple Music playlist named '{name}'" for name in missing]

    mappings = state.playlist_mappings()
    fetched: list[tuple[ApplePlaylist, tuple[AppleTrack, ...]]] = []
    ytm_playlists: dict[str, YtmPlaylist | None] = {}
    for p in selected:
        try:
            tracks = tuple(apple.playlist_tracks(p.id))
        except ServiceError as e:
            errors.append(f"skipped '{p.name}': {e}")
            continue
        log.info("Fetching '%s'… %d tracks", p.name, len(tracks))
        mapping = mappings.get(p.id)
        if mapping:
            try:
                ytm_playlists[mapping.ytm_playlist_id] = ytm.get_playlist(mapping.ytm_playlist_id)
            except ServiceError as e:
                errors.append(f"skipped '{p.name}': {e}")
                continue
        fetched.append((p, tracks))

    likes = cfg.likes
    favorites: tuple[AppleTrack, ...] | None = None
    if likes:
        try:
            favorites = tuple(apple.favorite_songs(cfg.favorites_playlist))
            log.info("%d favorites from '%s'", len(favorites), cfg.favorites_playlist)
        except ServiceError as e:
            errors.append(str(e))
            likes = False  # a favorites failure must not abort the playlists
    all_tracks = [t for _, ts in fetched for t in ts] + list(favorites or ())
    resolutions, search_errors = resolve_tracks(
        all_tracks, state, ytm, retry_unmatched=retry_unmatched
    )
    errors += search_errors
    liked = frozenset(ytm.liked_video_ids()) if likes else frozenset()

    plan = plan_sync(
        AppleSnapshot(tuple(fetched), favorites),
        resolutions,
        YtmSnapshot(ytm_playlists, liked),
        mappings,
        state.owned_likes(),
        prefix=cfg.mirror_prefix,
    )
    warnings += plan.warnings
    unmatched = sum(isinstance(r, Unmatched) for r in resolutions.values())
    log.info("planned %d ops", len(plan.ops))
    if dry_run:
        log.info("dry run: not applying; %d unmatched", unmatched)
        return SyncReport(plan, None, warnings, unmatched, errors)
    result = apply_plan(plan, ytm, state)
    log.info(
        "done: applied %d, %d failed, %d unmatched", result.applied, len(result.failures), unmatched
    )
    return SyncReport(plan, result, warnings, unmatched, errors)

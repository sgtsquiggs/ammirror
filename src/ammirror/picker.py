from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

import questionary

from ammirror.models import (
    ApplePlaylist,
    PlaylistOwner,
    natural_sort_key,
    normalize_playlist_name,
)

GROUP_TITLES: dict[PlaylistOwner | None, str] = {
    None: "Not in Apple Music",
    PlaylistOwner.YOURS: "Yours",
    PlaylistOwner.APPLE: "Apple Music",
    PlaylistOwner.OTHER: "Other",
}
_OWNER_RANK = {owner: rank for rank, owner in enumerate(PlaylistOwner)}


@dataclass(frozen=True)
class PickerRow:
    name: str
    checked: bool
    tags: tuple[str, ...]
    group: PlaylistOwner | None = PlaylistOwner.YOURS  # None: configured, not in Apple Music


@dataclass(frozen=True)
class PlaylistChanges:
    names: tuple[str, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)


def build_rows(
    available: Sequence[ApplePlaylist],
    patterns: Sequence[str],
    mirrored_ids: Collection[str],
    favorites_playlist: str,
) -> list[PickerRow]:
    """Rows for the checklist: configured names Apple lacks first, then playlists
    grouped by owner, each group in natural order.

    Config selects playlists by name, so same-named playlists share one row (in the
    first group any of them belongs to), and unnamed ones are left out.
    """
    groups: dict[str, list[ApplePlaylist]] = {}
    for p in available:
        if key := normalize_playlist_name(p.name):
            groups.setdefault(key, []).append(p)
    select_all = "*" in patterns
    configured = {normalize_playlist_name(n) for n in patterns}
    favorites = normalize_playlist_name(favorites_playlist)

    missing: dict[str, str] = {}
    if not select_all:
        for name in patterns:
            missing.setdefault(normalize_playlist_name(name), name)
    rows = [PickerRow(name, True, (), None) for key, name in missing.items() if key not in groups]
    for key, group in groups.items():
        lead = min(group, key=lambda p: _OWNER_RANK[p.owner])
        tags = [f"{len(group)} playlists"] if len(group) > 1 else []
        if any(p.id in mirrored_ids for p in group):
            tags.append("mirrored")
        if key == favorites:
            tags.append("favorites")
        rows.append(PickerRow(lead.name, select_all or key in configured, tuple(tags), lead.owner))
    return sorted(rows, key=_row_order)


def _row_order(row: PickerRow) -> tuple[int, tuple[str, str, str]]:
    rank = -1 if row.group is None else _OWNER_RANK[row.group]
    return rank, natural_sort_key(row.name)


def group_headings(rows: Sequence[PickerRow]) -> dict[int, str]:
    """Map the index of each group's first row to the group's heading."""
    counts: dict[PlaylistOwner | None, int] = {}
    for r in rows:
        counts[r.group] = counts.get(r.group, 0) + 1
    headings: dict[int, str] = {}
    for i, r in enumerate(rows):
        if i == 0 or rows[i - 1].group != r.group:
            headings[i] = f"{GROUP_TITLES[r.group]} ({counts[r.group]})"
    return headings


def playlist_changes(rows: Sequence[PickerRow], checked: Collection[str]) -> PlaylistChanges:
    chosen = set(checked)
    names = tuple(r.name for r in rows if r.name in chosen)
    added = tuple(r.name for r in rows if r.name in chosen and not r.checked)
    removed = tuple(r.name for r in rows if r.checked and r.name not in chosen)
    return PlaylistChanges(names, added, removed)


class _Heading(str):
    """A group heading's text, which questionary's search filter never matches.

    The filter tests `query in title.lower()` on every choice, separators included. A
    query that matched only headings would leave nothing selectable, and questionary's
    arrow-key handler then loops forever looking for a row it can point at.
    """

    def lower(self) -> str:
        return ""


def checklist_choices(
    rows: Sequence[PickerRow],
) -> list[questionary.Choice | questionary.Separator]:
    """questionary choices for the rows, with a separator heading before each group.

    Titles are plain strings because questionary's search filter only handles those;
    tags are part of the title, so typing a tag such as "mirrored" filters by it.
    """
    headings = group_headings(rows)
    choices: list[questionary.Choice | questionary.Separator] = []
    for i, r in enumerate(rows):
        if i in headings:
            choices.append(
                questionary.Separator(_Heading(f"\u2500\u2500 {headings[i]} \u2500\u2500"))
            )
        title = f"{r.name}  [{', '.join(r.tags)}]" if r.tags else r.name
        choices.append(questionary.Choice(title, value=r.name, checked=r.checked))
    return choices


def ask_playlists(rows: Sequence[PickerRow], **app_kwargs: Any) -> list[str] | None:
    """Show the checklist; return the checked names, or None if cancelled.

    `app_kwargs` go to the prompt_toolkit application (tests pass input and output).
    """
    return questionary.checkbox(
        "Playlists to mirror",
        choices=checklist_choices(rows),
        instruction="(space toggles, type to filter, enter saves, ctrl-c cancels)",
        use_search_filter=True,
        use_jk_keys=False,
        **app_kwargs,
    ).ask()

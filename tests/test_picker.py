import threading

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from questionary import Separator
from questionary.prompts.common import InquirerControl

from ammirror.models import ApplePlaylist, PlaylistOwner
from ammirror.picker import (
    PickerRow,
    ask_playlists,
    build_rows,
    checklist_choices,
    group_headings,
    playlist_changes,
)

FAVORITES = "Favorite Songs"


def rows_for(
    playlists: list[ApplePlaylist], patterns: list[str], mirrored: set[str] | None = None
) -> list[PickerRow]:
    return build_rows(playlists, patterns, mirrored or set(), FAVORITES)


def test_rows_are_alphabetical_with_configured_ones_checked() -> None:
    rows = rows_for(
        [ApplePlaylist("p1", "gym"), ApplePlaylist("p2", "Chill"), ApplePlaylist("p3", "Beach")],
        ["Gym"],
    )
    assert rows == [
        PickerRow("Beach", False, ()),
        PickerRow("Chill", False, ()),
        PickerRow("gym", True, ()),
    ]


def test_star_checks_everything() -> None:
    rows = rows_for([ApplePlaylist("p1", "A"), ApplePlaylist("p2", "B")], ["*"])
    assert [r.checked for r in rows] == [True, True]


def test_configured_names_match_loosely() -> None:
    rows = rows_for([ApplePlaylist("p1", "\u201893")], ["'93"])
    assert rows == [PickerRow("\u201893", True, ())]


def test_tags_mirrored_and_favorites() -> None:
    rows = rows_for(
        [ApplePlaylist("p1", "Gym"), ApplePlaylist("p2", "favorite songs")], [], mirrored={"p1"}
    )
    assert rows == [
        PickerRow("favorite songs", False, ("favorites",)),
        PickerRow("Gym", False, ("mirrored",)),
    ]


def test_same_named_playlists_share_one_row() -> None:
    rows = rows_for(
        [ApplePlaylist("p1", "Mix"), ApplePlaylist("p2", "mix"), ApplePlaylist("p3", "Other")],
        ["Mix"],
        mirrored={"p2"},
    )
    assert rows[0] == PickerRow("Mix", True, ("2 playlists", "mirrored"))
    assert len(rows) == 2


def test_configured_names_missing_from_apple_come_first_and_checked() -> None:
    rows = rows_for([ApplePlaylist("p1", "Gym")], ["Gym", "Gone", "gone", "*nope"])
    assert rows == [
        PickerRow("Gone", True, (), None),
        PickerRow("*nope", True, (), None),
        PickerRow("Gym", True, ()),
    ]


def test_star_has_no_missing_row() -> None:
    assert rows_for([], ["*"]) == []


def test_changes_report_added_and_removed_in_row_order() -> None:
    rows = [PickerRow("A", True, ()), PickerRow("B", False, ()), PickerRow("C", True, ())]
    changes = playlist_changes(rows, ["B", "C"])
    assert changes.names == ("B", "C")
    assert changes.added == ("B",)
    assert changes.removed == ("A",)
    assert changes.changed


def test_no_toggles_is_no_change() -> None:
    rows = [PickerRow("A", True, ()), PickerRow("B", False, ())]
    changes = playlist_changes(rows, ["A"])
    assert not changes.changed
    assert changes.added == changes.removed == ()


def test_unchecking_everything_saves_empty() -> None:
    changes = playlist_changes([PickerRow("A", True, ())], [])
    assert changes.names == ()
    assert changes.removed == ("A",)


def test_unnamed_playlists_are_skipped() -> None:
    rows = rows_for([ApplePlaylist("p1", " "), ApplePlaylist("p2", "Gym")], ["*"])
    assert rows == [PickerRow("Gym", True, ())]


def test_checklist_filters_toggles_and_saves() -> None:
    rows = [
        PickerRow("Gone", True, (), None),
        PickerRow("\u201893", False, ("mirrored",)),
        PickerRow("Gym", True, ()),
    ]
    with create_pipe_input() as inp:
        inp.send_text("93 \r")  # filter to the '93 row, toggle it, save
        inp.close()
        checked = ask_playlists(rows, input=inp, output=DummyOutput())
    assert checked == ["Gone", "\u201893", "Gym"]


def test_checklist_filter_matches_tags() -> None:
    rows = [PickerRow("A", False, ()), PickerRow("B", False, ("mirrored",))]
    with create_pipe_input() as inp:
        inp.send_text("mirrored \r")
        inp.close()
        assert ask_playlists(rows, input=inp, output=DummyOutput()) == ["B"]


YOURS, APPLE, OTHER = PlaylistOwner.YOURS, PlaylistOwner.APPLE, PlaylistOwner.OTHER


def test_rows_are_grouped_by_owner_then_naturally_sorted() -> None:
    rows = rows_for(
        [
            ApplePlaylist("a1", "Ambient Essentials", APPLE),
            ApplePlaylist("s1", "Purchased Music", OTHER),
            ApplePlaylist("y1", "Mix 10", YOURS),
            ApplePlaylist("y2", "\u201893", YOURS),
            ApplePlaylist("y3", "mix 2", YOURS),
        ],
        ["Gone"],
    )
    assert [(r.group, r.name) for r in rows] == [
        (None, "Gone"),
        (YOURS, "\u201893"),
        (YOURS, "mix 2"),
        (YOURS, "Mix 10"),
        (APPLE, "Ambient Essentials"),
        (OTHER, "Purchased Music"),
    ]


def test_same_name_across_owners_goes_to_the_first_group() -> None:
    rows = rows_for([ApplePlaylist("a1", "Chill", APPLE), ApplePlaylist("y1", "chill", YOURS)], [])
    assert rows == [PickerRow("chill", False, ("2 playlists",), YOURS)]


def test_checklist_shows_group_headings_the_cursor_skips() -> None:
    rows = [PickerRow("A", False, (), YOURS), PickerRow("B", False, (), APPLE)]
    with create_pipe_input() as inp:
        inp.send_text("\x1b[B \r")  # down past the "Apple Music" heading to B, toggle, save
        inp.close()
        assert ask_playlists(rows, input=inp, output=DummyOutput()) == ["B"]


def test_group_headings() -> None:
    rows = [
        PickerRow("Gone", True, (), None),
        PickerRow("A", False, (), YOURS),
        PickerRow("B", False, (), YOURS),
        PickerRow("C", False, (), OTHER),
    ]
    assert group_headings(rows) == {
        0: "Not in Apple Music (1)",
        1: "Yours (2)",
        3: "Other (1)",
    }


@pytest.mark.parametrize("query", ["yours", "music", "apple", "(1)"])
def test_search_filter_always_leaves_a_selectable_row(query: str) -> None:
    rows = [PickerRow("A", False, (), YOURS), PickerRow("B", False, (), APPLE)]
    control = InquirerControl(checklist_choices(rows))
    control.search_filter = query
    assert any(not isinstance(c, Separator) for c in control.filtered_choices)


def test_search_filter_hides_headings() -> None:
    rows = [PickerRow("Ambient", False, (), APPLE), PickerRow("B", False, (), OTHER)]
    control = InquirerControl(checklist_choices(rows))
    control.search_filter = "a"  # also in the "Apple Music" heading
    assert [c.title for c in control.filtered_choices] == ["Ambient"]


def test_filter_matching_only_a_heading_does_not_hang() -> None:
    rows = [PickerRow("A", False, (), YOURS), PickerRow("B", False, (), APPLE)]
    result: list[list[str] | None] = []

    def run() -> None:
        with create_pipe_input() as inp:
            inp.send_text("yours\x1b[B\x1b[A \r")  # filter, arrow down and up, toggle, save
            inp.close()
            result.append(ask_playlists(rows, input=inp, output=DummyOutput()))

    worker = threading.Thread(target=run, daemon=True)  # the bug was an endless loop
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive(), "checklist hung"
    assert result == [["B"]]

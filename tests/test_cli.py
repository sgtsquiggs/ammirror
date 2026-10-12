import stat
from pathlib import Path

import pytest
import ytmusicapi
from click.testing import CliRunner
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ammirror.cli import cli
from ammirror.config import Paths, load_config
from ammirror.errors import YTMUSICAPI_HINT, AuthError, ServiceError
from ammirror.match import search_query
from ammirror.models import ApplePlaylist, PlaylistOwner
from ammirror.picker import PickerRow
from ammirror.state import State
from tests.factories import cand, track
from tests.fakes import FakeApple, FakeYtm


def test_version() -> None:
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "ammirror" in result.output


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Paths:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return Paths.from_env()


def write_config(paths: Paths, extra: str = "") -> None:
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    (paths.config_dir / "AuthKey.p8").write_text(pem)
    paths.config_file.write_text(
        f'[apple]\nkey_path = "{paths.config_dir / "AuthKey.p8"}"\n'
        f'key_id = "KEY1234567"\nteam_id = "TEAM123456"\n{extra}'
    )


def test_missing_config_exits_2(paths: Paths) -> None:
    result = CliRunner().invoke(cli, ["auth", "apple"])
    assert result.exit_code == 2
    assert "config not found" in result.output


def test_auth_apple_saves_token(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(paths)
    captured: dict[str, str] = {}

    def fake_flow(developer_token: str) -> str:
        captured["dev"] = developer_token
        return "music-user-token"

    monkeypatch.setattr("ammirror.cli.run_auth_flow", fake_flow)
    result = CliRunner().invoke(cli, ["auth", "apple"])
    assert result.exit_code == 0, result.output
    assert paths.apple_user_token_file.read_text() == "music-user-token"
    assert captured["dev"].count(".") == 2  # a JWT


def test_auth_ytm_runs_setup(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def fake_setup(headers_raw: str) -> str:
        seen["headers"] = headers_raw
        return '{"cookie": "abc"}'

    monkeypatch.setattr(ytmusicapi, "setup", fake_setup)
    pasted = (
        "POST /youtubei/v1/browse?prettyPrint=false HTTP/3\n"
        "cookie: abc\ncontent-encoding: gzip\ncontent-length: 9\nuser-agent: x\n"
    )
    result = CliRunner().invoke(cli, ["auth", "ytm"], input=pasted)
    assert result.exit_code == 0, result.output
    assert seen["headers"] == "cookie: abc\nuser-agent: x\n"
    assert paths.ytm_auth_file.read_text() == '{"cookie": "abc"}'
    assert stat.S_IMODE(paths.ytm_auth_file.stat().st_mode) == 0o600


def test_auth_ytm_bad_headers_exit_2(paths: Paths) -> None:
    # Real ytmusicapi.setup: it parses locally and rejects headers without x-goog-authuser.
    result = CliRunner().invoke(cli, ["auth", "ytm"], input="cookie: abc\nuser-agent: x\n")
    assert result.exit_code == 2
    assert "could not parse pasted headers" in result.output
    assert not paths.ytm_auth_file.exists()


def test_auth_ytm_empty_input(paths: Paths) -> None:
    result = CliRunner().invoke(cli, ["auth", "ytm"], input="")
    assert result.exit_code == 2


@pytest.fixture
def fakes(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> tuple[FakeApple, FakeYtm]:
    write_config(paths, '[sync]\nplaylists = ["Gym"]\nlikes = false\n')
    t1, t2 = track(1), track(2, title="Obscure")
    apple = FakeApple(
        playlists=[(ApplePlaylist("p.gym", "Gym"), [t1, t2]), (ApplePlaylist("p.x", "Other"), [])]
    )
    ytm = FakeYtm(search_results={search_query(t1): [cand("v1", t1.title)]})
    monkeypatch.setattr("ammirror.cli.make_apple", lambda _p, _c: apple)
    monkeypatch.setattr("ammirror.cli.make_ytm", lambda _p: ytm)
    return apple, ytm


def test_playlists_lists_by_owner(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(paths, '[sync]\nplaylists = ["Mix 2", "Gone"]\n')
    apple = FakeApple(
        playlists=[
            (ApplePlaylist("a1", "Ambient Essentials", PlaylistOwner.APPLE), []),
            (ApplePlaylist("y1", "Mix 10"), []),
            (ApplePlaylist("y2", "Mix 2"), []),
            (ApplePlaylist("s1", "Favorite Songs", PlaylistOwner.OTHER), []),
        ]
    )
    monkeypatch.setattr("ammirror.cli.make_apple", lambda _p, _c: apple)
    result = CliRunner().invoke(cli, ["playlists"])
    assert result.exit_code == 0, result.output
    assert result.output == (
        "Not in Apple Music (1)\n"
        "  Gone  [selected]\n"
        "Yours (2)\n"
        "  Mix 2  [selected]\n"
        "  Mix 10\n"
        "Apple Music (1)\n"
        "  Ambient Essentials\n"
        "Other (1)\n"
        "  Favorite Songs  [favorites]\n"
    )


@pytest.mark.usefixtures("fakes")
def test_playlists_marks_selected_and_mirrored() -> None:
    out = CliRunner().invoke(cli, ["playlists"]).output
    assert "Gym" in out and "Other" in out
    CliRunner().invoke(cli, ["sync"])
    out = CliRunner().invoke(cli, ["playlists"]).output
    gym_line = next(line for line in out.splitlines() if "Gym" in line)
    assert "mirrored" in gym_line


class FakePicker:
    def __init__(self) -> None:
        self.answer: list[str] | None = None
        self.shown: list[list[PickerRow]] = []

    def __call__(self, rows: list[PickerRow]) -> list[str] | None:
        self.shown.append(rows)
        return self.answer


@pytest.fixture
def picker(monkeypatch: pytest.MonkeyPatch) -> FakePicker:
    fake = FakePicker()
    monkeypatch.setattr("ammirror.cli.is_interactive", lambda: True)
    monkeypatch.setattr("ammirror.cli.ask_playlists", fake)
    return fake


@pytest.mark.usefixtures("fakes")
def test_playlists_edit_saves_selection(paths: Paths, picker: FakePicker) -> None:
    picker.answer = ["Other"]
    result = CliRunner().invoke(cli, ["playlists", "--edit"])
    assert result.exit_code == 0, result.output
    assert picker.shown[0] == [PickerRow("Gym", True, ()), PickerRow("Other", False, ())]
    assert load_config(paths.config_file).sync.playlists == ("Other",)
    assert "added: Other" in result.output
    assert "removed: Gym" in result.output
    assert "keep their YouTube Music copy" in result.output
    assert f"saved sync.playlists to {paths.config_file}" in result.output


@pytest.mark.usefixtures("fakes")
def test_playlists_edit_can_drop_missing_names(paths: Paths, picker: FakePicker) -> None:
    write_config(paths, '[sync]\nplaylists = ["Gym", "Gone"]\nlikes = false\n')
    picker.answer = ["Gym"]
    result = CliRunner().invoke(cli, ["playlists", "--edit"])
    assert result.exit_code == 0, result.output
    assert picker.shown[0][0] == PickerRow("Gone", True, (), None)
    assert load_config(paths.config_file).sync.playlists == ("Gym",)
    assert "removed: Gone" in result.output


@pytest.mark.usefixtures("fakes")
def test_playlists_edit_without_changes_leaves_config(paths: Paths, picker: FakePicker) -> None:
    before = paths.config_file.read_text()
    picker.answer = ["Gym"]
    result = CliRunner().invoke(cli, ["playlists", "--edit"])
    assert result.exit_code == 0, result.output
    assert "no changes" in result.output
    assert paths.config_file.read_text() == before


@pytest.mark.usefixtures("fakes")
def test_playlists_edit_cancel_leaves_config(paths: Paths, picker: FakePicker) -> None:
    before = paths.config_file.read_text()
    picker.answer = None
    result = CliRunner().invoke(cli, ["playlists", "--edit"])
    assert result.exit_code == 1
    assert "cancelled; config unchanged" in result.output
    assert paths.config_file.read_text() == before


@pytest.mark.usefixtures("fakes")
def test_playlists_edit_needs_a_terminal(paths: Paths) -> None:
    before = paths.config_file.read_text()
    result = CliRunner().invoke(cli, ["playlists", "--edit"])
    assert result.exit_code == 2
    assert "needs an interactive terminal" in result.output
    assert paths.config_file.read_text() == before


def test_sync_dry_run_prints_plan(fakes: tuple[FakeApple, FakeYtm]) -> None:
    _, ytm = fakes
    result = CliRunner().invoke(cli, ["sync", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "create playlist 'Gym' with 1 track" in result.output
    assert "1 unmatched" in result.output
    assert ytm.playlists == {}


def test_sync_applies_and_reports(fakes: tuple[FakeApple, FakeYtm]) -> None:
    _, ytm = fakes
    result = CliRunner().invoke(cli, ["sync"])
    assert result.exit_code == 0, result.output
    assert len(ytm.playlists) == 1
    assert "applied 1" in result.output


def with_artwork(apple: FakeApple) -> None:
    (gym, tracks), *rest = apple.playlists
    art = "https://img.example/gym/1200x1200bb.jpg"
    apple.playlists = [(ApplePlaylist(gym.id, gym.name, PlaylistOwner.APPLE, art), tracks), *rest]


def test_sync_dry_run_lists_covers_without_downloading(fakes: tuple[FakeApple, FakeYtm]) -> None:
    apple, ytm = fakes
    with_artwork(apple)
    result = CliRunner().invoke(cli, ["sync", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would set the cover of 'Gym' to its Apple Music artwork" in result.output
    assert apple.artwork_requests == [] and ytm.covers == {}


def test_sync_refused_cover_warns_and_exits_0(fakes: tuple[FakeApple, FakeYtm]) -> None:
    apple, ytm = fakes
    with_artwork(apple)
    ytm.fail_on = {"set_playlist_cover"}
    result = CliRunner().invoke(cli, ["sync"])
    assert result.exit_code == 0, result.output
    assert "warning: cover of 'Gym' not set: fake failure in set_playlist_cover" in result.output
    assert "applied 1, 0 failed" in result.output


def test_sync_partial_failure_exits_1(fakes: tuple[FakeApple, FakeYtm]) -> None:
    _, ytm = fakes
    ytm.fail_on = {"create_playlist"}
    result = CliRunner().invoke(cli, ["sync"])
    assert result.exit_code == 1
    assert "failed" in result.output


@pytest.mark.usefixtures("fakes")
def test_unmatched_and_pin(paths: Paths) -> None:
    CliRunner().invoke(cli, ["sync"])
    out = CliRunner().invoke(cli, ["unmatched"]).output
    assert "Obscure" in out and "2" in out
    result = CliRunner().invoke(cli, ["pin", "2", "vPINNED"])
    assert result.exit_code == 0
    assert "Obscure" not in CliRunner().invoke(cli, ["unmatched"]).output
    state = State(paths.state_db)
    try:
        assert state.get_match("2") is not None
    finally:
        state.close()


def test_sync_missing_ytm_auth_exits_2(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(paths)
    monkeypatch.setattr("ammirror.cli.make_apple", lambda _p, _c: FakeApple())
    result = CliRunner().invoke(cli, ["sync"])
    assert result.exit_code == 2
    assert "ammirror auth ytm" in result.output


def test_sync_checks_both_services_before_doing_anything(
    fakes: tuple[FakeApple, FakeYtm],
) -> None:
    apple, ytm = fakes
    apple.check_error = AuthError("apple")
    ytm.auth_fail_on = {"check_auth"}
    result = CliRunner().invoke(cli, ["sync", "--dry-run"])
    assert result.exit_code == 2
    assert "error: Apple Music auth missing or expired" in result.stderr
    assert "error: YouTube Music auth missing or expired" in result.stderr
    assert ytm.calls == [("check_auth",)]
    assert "dry run" not in result.output


def test_sync_reports_every_missing_credential(paths: Paths) -> None:
    write_config(paths)
    result = CliRunner().invoke(cli, ["sync"])
    assert result.exit_code == 2
    assert "ammirror auth apple" in result.stderr
    assert "ammirror auth ytm" in result.stderr


def test_sync_check_service_error_exits_1(fakes: tuple[FakeApple, FakeYtm]) -> None:
    apple, ytm = fakes
    apple.check_error = ServiceError("Apple Music request failed: timed out")
    result = CliRunner().invoke(cli, ["sync"])
    assert result.exit_code == 1
    assert "error: Apple Music request failed: timed out" in result.stderr
    assert ytm.calls == [("check_auth",)]


def test_sync_check_auth_error_wins_exit_code(fakes: tuple[FakeApple, FakeYtm]) -> None:
    apple, ytm = fakes
    apple.check_error = AuthError("apple")
    ytm.fail_on = {"check_auth"}
    assert CliRunner().invoke(cli, ["sync"]).exit_code == 2


def test_sync_dry_run_with_skipped_playlist_exits_1(fakes: tuple[FakeApple, FakeYtm]) -> None:
    apple, _ = fakes
    apple.failing_playlists = {"p.gym"}
    result = CliRunner().invoke(cli, ["sync", "--dry-run"])
    assert result.exit_code == 1
    assert "error: skipped 'Gym'" in result.output
    assert "dry run:" in result.output


def test_sync_search_failure_exits_1(fakes: tuple[FakeApple, FakeYtm]) -> None:
    _, ytm = fakes
    ytm.fail_on = {"search_songs"}
    result = CliRunner().invoke(cli, ["sync"])
    assert result.exit_code == 1
    assert "error: search failed" in result.output
    assert "applied" in result.output


def test_sync_suggests_upgrade_once_on_ytmusicapi_parse_errors(
    fakes: tuple[FakeApple, FakeYtm], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, ytm = fakes

    def broken(*_args: object) -> None:
        raise ServiceError(f"YouTube Music search failed: 'contents' {YTMUSICAPI_HINT}")

    monkeypatch.setattr(ytm, "search_songs", broken)
    monkeypatch.setattr(ytm, "create_playlist", broken)
    result = CliRunner().invoke(cli, ["sync"])
    assert result.exit_code == 1
    last = result.output.strip().splitlines()[-1]
    assert "upgrade ammirror" in last
    assert sum("hint:" in line for line in result.output.splitlines()) == 1


def test_sync_without_parse_errors_has_no_upgrade_hint(fakes: tuple[FakeApple, FakeYtm]) -> None:
    _, ytm = fakes
    ytm.fail_on = {"create_playlist"}
    result = CliRunner().invoke(cli, ["sync"])
    assert "hint:" not in result.output


def test_sync_dry_run_only_reads_youtube_music(
    paths: Paths, fakes: tuple[FakeApple, FakeYtm]
) -> None:
    apple, ytm = fakes
    write_config(paths, '[sync]\nplaylists = ["Gym"]\n')  # likes on
    apple.favorites = [track(1)]
    ytm.liked = {"vOLD"}
    state = State(paths.state_db)
    try:
        state.add_owned_like("vOLD", "99")
    finally:
        state.close()
    result = CliRunner().invoke(cli, ["sync", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would like v1" in result.output
    assert "would unlike vOLD" in result.output
    assert {c[0] for c in ytm.calls} <= {
        "check_auth",
        "search_songs",
        "get_playlist",
        "liked_video_ids",
    }
    assert ytm.liked == {"vOLD"}
    assert ytm.playlists == {}


def test_sync_auth_error_mid_apply_exits_2(fakes: tuple[FakeApple, FakeYtm]) -> None:
    _, ytm = fakes
    ytm.auth_fail_on = {"create_playlist"}
    result = CliRunner().invoke(cli, ["sync"])
    assert result.exit_code == 2
    assert "ammirror auth ytm" in result.output
    assert "applied" not in result.output


def test_sync_dry_run_help_mentions_local_cache() -> None:
    out = CliRunner().invoke(cli, ["sync", "--help"]).output
    assert "never changes YouTube Music" in " ".join(out.split())
    assert "caches match results locally" in " ".join(out.split())


def test_sync_reports_dropped_likes_and_exits_1(
    paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(paths, "[sync]\nplaylists = []\nlikes = true\n")
    t1 = track(1)
    apple = FakeApple(favorites=[t1])
    ytm = FakeYtm(search_results={search_query(t1): [cand("v1", t1.title)]}, drop_likes={"v1"})
    monkeypatch.setattr("ammirror.cli.make_apple", lambda _p, _c: apple)
    monkeypatch.setattr("ammirror.cli.make_ytm", lambda _p: ytm)
    result = CliRunner().invoke(cli, ["sync"])
    assert result.exit_code == 1
    assert f"error: like didn't stick on YouTube Music: {t1.artist} - {t1.title} (v1)" in (
        result.output
    )
    assert "run `ammirror sync` again" in result.output

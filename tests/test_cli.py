import stat
from pathlib import Path

import pytest
import ytmusicapi
from click.testing import CliRunner
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ammirror.cli import cli
from ammirror.config import Paths
from ammirror.match import search_query
from ammirror.models import ApplePlaylist
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

    def fake_setup(filepath: str, headers_raw: str) -> str:
        seen["headers"] = headers_raw
        Path(filepath).write_text("{}")
        return "{}"

    monkeypatch.setattr(ytmusicapi, "setup", fake_setup)
    result = CliRunner().invoke(cli, ["auth", "ytm"], input="cookie: abc\nuser-agent: x\n")
    assert result.exit_code == 0, result.output
    assert "cookie: abc" in seen["headers"]
    assert stat.S_IMODE(paths.ytm_auth_file.stat().st_mode) == 0o600


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


@pytest.mark.usefixtures("fakes")
def test_playlists_marks_selected_and_mirrored() -> None:
    out = CliRunner().invoke(cli, ["playlists"]).output
    assert "Gym" in out and "Other" in out
    CliRunner().invoke(cli, ["sync"])
    out = CliRunner().invoke(cli, ["playlists"]).output
    gym_line = next(line for line in out.splitlines() if "Gym" in line)
    assert "mirrored" in gym_line


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

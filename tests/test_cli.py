import stat
from pathlib import Path

import pytest
import ytmusicapi
from click.testing import CliRunner
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ammirror.cli import cli
from ammirror.config import Paths


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

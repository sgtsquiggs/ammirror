import stat
from pathlib import Path

import pytest

from ammirror.config import Paths, load_config, write_secret
from ammirror.errors import AuthError, ConfigError

GOOD = """
[apple]
key_path = "~/keys/AuthKey_ABC.p8"
key_id = "ABCDE12345"
team_id = "FGHIJ67890"

[sync]
playlists = ["Gym", "Chill"]
likes = false
mirror_prefix = "AM: "
favorites_playlist = "Lieblingssongs"
"""


def test_paths_from_env_honors_xdg(tmp_path: Path) -> None:
    p = Paths.from_env(
        {"XDG_CONFIG_HOME": str(tmp_path / "c"), "XDG_STATE_HOME": str(tmp_path / "s")}
    )
    assert p.config_file == tmp_path / "c" / "ammirror" / "config.toml"
    assert p.apple_user_token_file == tmp_path / "c" / "ammirror" / "apple-user-token"
    assert p.ytm_auth_file == tmp_path / "c" / "ammirror" / "ytm-browser.json"
    assert p.state_db == tmp_path / "s" / "ammirror" / "state.db"


def test_paths_from_env_defaults_to_home(tmp_path: Path) -> None:
    p = Paths.from_env({"HOME": str(tmp_path)})
    assert p.config_dir == tmp_path / ".config" / "ammirror"
    assert p.state_dir == tmp_path / ".local" / "state" / "ammirror"


def test_load_config(tmp_path: Path) -> None:
    f = tmp_path / "config.toml"
    f.write_text(GOOD)
    cfg = load_config(f)
    assert cfg.apple.key_path == Path("~/keys/AuthKey_ABC.p8").expanduser()
    assert cfg.apple.key_id == "ABCDE12345"
    assert cfg.apple.team_id == "FGHIJ67890"
    assert cfg.sync.playlists == ("Gym", "Chill")
    assert cfg.sync.likes is False
    assert cfg.sync.mirror_prefix == "AM: "
    assert cfg.sync.favorites_playlist == "Lieblingssongs"


def test_load_config_sync_defaults(tmp_path: Path) -> None:
    f = tmp_path / "config.toml"
    f.write_text(GOOD.split("[sync]")[0])
    cfg = load_config(f)
    assert cfg.sync.playlists == ("*",)
    assert cfg.sync.likes is True
    assert cfg.sync.mirror_prefix == ""
    assert cfg.sync.favorites_playlist == "Favorite Songs"


def test_load_config_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


@pytest.mark.parametrize(
    "text, msg",
    [
        ("not = [valid", "config.toml"),
        ("[sync]\nlikes = true\n", r"\[apple\]"),
        ('[apple]\nkey_path = "x"\nkey_id = ""\nteam_id = "T"\n', "key_id"),
        (GOOD.replace("likes = false", 'likes = "yes"'), "likes"),
        (GOOD.replace('["Gym", "Chill"]', '"Gym"'), "playlists"),
        (GOOD.replace('"Lieblingssongs"', '""'), "favorites_playlist"),
        (GOOD.replace('"Lieblingssongs"', "3"), "favorites_playlist"),
    ],
)
def test_load_config_invalid(tmp_path: Path, text: str, msg: str) -> None:
    f = tmp_path / "config.toml"
    f.write_text(text)
    with pytest.raises(ConfigError, match=msg):
        load_config(f)


def test_write_secret_is_0600(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "secret"
    write_secret(target, "hunter2")
    assert target.read_text() == "hunter2"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    write_secret(target, "again")
    assert target.read_text() == "again"


def test_auth_error_message() -> None:
    e = AuthError("ytm")
    assert e.service == "ytm"
    assert str(e) == "YouTube Music auth missing or expired: run `ammirror auth ytm`"


def test_load_config_unreadable_is_config_error(tmp_path: Path) -> None:
    f = tmp_path / "config.toml"
    f.mkdir()
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(f)


def test_load_config_not_utf8_is_config_error(tmp_path: Path) -> None:
    f = tmp_path / "config.toml"
    f.write_bytes(b'[apple]\nkey_id = "\xff\xfe"\n')
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(f)

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ammirror.errors import ConfigError


@dataclass(frozen=True)
class Paths:
    config_dir: Path
    state_dir: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Paths":
        env = os.environ if env is None else env
        home = Path(env["HOME"]) if env.get("HOME") else Path.home()
        config_home = (
            Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else home / ".config"
        )
        state_home = (
            Path(env["XDG_STATE_HOME"]) if env.get("XDG_STATE_HOME") else home / ".local" / "state"
        )
        return cls(config_home / "ammirror", state_home / "ammirror")

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def apple_user_token_file(self) -> Path:
        return self.config_dir / "apple-user-token"

    @property
    def ytm_auth_file(self) -> Path:
        return self.config_dir / "ytm-browser.json"

    @property
    def state_db(self) -> Path:
        return self.state_dir / "state.db"


@dataclass(frozen=True)
class AppleConfig:
    key_path: Path
    key_id: str
    team_id: str


@dataclass(frozen=True)
class SyncConfig:
    playlists: tuple[str, ...] = ("*",)
    likes: bool = True
    mirror_prefix: str = ""


@dataclass(frozen=True)
class Config:
    apple: AppleConfig
    sync: SyncConfig


def _require_str(table: Mapping[str, Any], key: str, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}.{key} must be a non-empty string")
    return value


def load_config(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(f"config not found at {path} (see README for an example)")
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(f"cannot read config {path}: {e}") from e
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e

    apple = raw.get("apple")
    if not isinstance(apple, dict):
        raise ConfigError(f"{path}: missing [apple] table")
    apple_cfg = AppleConfig(
        key_path=Path(_require_str(apple, "key_path", "apple")).expanduser(),
        key_id=_require_str(apple, "key_id", "apple"),
        team_id=_require_str(apple, "team_id", "apple"),
    )

    sync = raw.get("sync", {})
    if not isinstance(sync, dict):
        raise ConfigError(f"{path}: [sync] must be a table")
    playlists = sync.get("playlists", ["*"])
    if not isinstance(playlists, list) or not all(isinstance(p, str) for p in playlists):
        raise ConfigError("sync.playlists must be a list of strings")
    likes = sync.get("likes", True)
    if not isinstance(likes, bool):
        raise ConfigError("sync.likes must be true or false")
    prefix = sync.get("mirror_prefix", "")
    if not isinstance(prefix, str):
        raise ConfigError("sync.mirror_prefix must be a string")

    return Config(apple_cfg, SyncConfig(tuple(playlists), likes, prefix))


def write_secret(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    path.chmod(0o600)

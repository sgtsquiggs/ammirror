import time

import jwt

from ammirror.config import AppleConfig
from ammirror.errors import ConfigError

# Apple rejects developer tokens that expire more than ~6 months out.
MAX_TTL = 15_777_000


def make_developer_token(
    key_pem: str, key_id: str, team_id: str, *, now: int | None = None, ttl: int = 3600
) -> str:
    """Sign an Apple Music API developer token (ES256 JWT)."""
    issued = int(time.time()) if now is None else now
    claims = {"iss": team_id, "iat": issued, "exp": issued + min(ttl, MAX_TTL)}
    return jwt.encode(claims, key_pem, algorithm="ES256", headers={"kid": key_id})


def load_developer_token(cfg: AppleConfig, *, ttl: int = 3600) -> str:
    try:
        key_pem = cfg.key_path.read_text()
    except OSError as e:
        raise ConfigError(f"cannot read MusicKit key {cfg.key_path}: {e.strerror}") from e
    return make_developer_token(key_pem, cfg.key_id, cfg.team_id, ttl=ttl)

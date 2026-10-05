from typing import Literal

Service = Literal["apple", "ytm"]

_SERVICE_NAMES: dict[str, str] = {"apple": "Apple Music", "ytm": "YouTube Music"}

# Appended to errors caused by YouTube Music responses ytmusicapi could not parse.
YTMUSICAPI_HINT = (
    "(ytmusicapi may need upgrading: upgrade ammirror, "
    "e.g. `uv tool upgrade ammirror` or `pipx upgrade ammirror`)"
)


class AmmirrorError(Exception):
    pass


class ConfigError(AmmirrorError):
    pass


class ServiceError(AmmirrorError):
    pass


class AuthError(AmmirrorError):
    def __init__(self, service: Service, detail: str = "missing or expired") -> None:
        self.service = service
        super().__init__(f"{_SERVICE_NAMES[service]} auth {detail}: run `ammirror auth {service}`")

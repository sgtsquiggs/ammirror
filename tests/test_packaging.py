import re
from importlib.metadata import requires


def test_runtime_imports_are_declared_dependencies() -> None:
    declared = {
        re.split(r"[\[<>=!~ ;]", r, maxsplit=1)[0].lower() for r in requires("ammirror") or []
    }
    # requests is imported directly by ytm/client.py, not only via ytmusicapi.
    assert {"click", "httpx", "pyjwt", "ytmusicapi", "requests"} <= declared

import logging
from pathlib import Path

import pytest
import ytmusicapi
from click.testing import CliRunner

from ammirror.cli import cli
from ammirror.config import Paths
from ammirror.errors import ConfigError
from ammirror.match import search_query
from ammirror.models import ApplePlaylist
from tests.factories import cand, track
from tests.fakes import FakeApple, FakeYtm
from tests.test_cli import write_config

BULLET = "·"


def ammirror_handlers() -> list[logging.Handler]:
    return list(logging.getLogger("ammirror").handlers)


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Paths:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return Paths.from_env()


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


@pytest.mark.parametrize(
    ("args", "level"),
    [
        ([], logging.WARNING),
        (["-v"], logging.INFO),
        (["-vv"], logging.DEBUG),
        (["-vvv"], logging.DEBUG),
    ],
)
@pytest.mark.usefixtures("paths")
def test_verbosity_sets_ammirror_logger_level(args: list[str], level: int) -> None:
    result = CliRunner().invoke(cli, [*args, "unmatched"])
    assert result.exit_code == 0, result.output
    logger = logging.getLogger("ammirror")
    assert logger.level == level
    assert logger.propagate is False


@pytest.mark.usefixtures("paths")
def test_handler_is_not_duplicated_across_invocations() -> None:
    for _ in range(3):
        CliRunner().invoke(cli, ["-v", "unmatched"])
    CliRunner().invoke(cli, ["-vv", "unmatched"])
    assert len(ammirror_handlers()) == 1


HTTPX_LINE = "HTTP Request: GET https://example.test/x"


def _httpx_handlers() -> list[logging.Handler]:
    return list(logging.getLogger("httpx").handlers)


@pytest.mark.usefixtures("paths")
def test_httpx_request_lines_appear_only_at_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_playlists(_p: Paths, _c: object) -> object:
        logging.getLogger("httpx").info(HTTPX_LINE)
        raise ConfigError("stop")

    monkeypatch.setattr("ammirror.cli.make_apple", fake_playlists)
    write_config(Paths.from_env())
    for args, expected in ([[], False], [["-v"], False], [["-vv"], True]):
        result = CliRunner().invoke(cli, [*args, "playlists"])
        assert (HTTPX_LINE in result.stderr) is expected, args
        assert logging.getLogger("httpx").getEffectiveLevel() >= (
            logging.INFO if expected else logging.WARNING
        )


@pytest.mark.usefixtures("paths")
def test_httpx_handler_is_not_stacked_and_is_removed() -> None:
    for _ in range(3):
        CliRunner().invoke(cli, ["-vv", "unmatched"])
    assert len(_httpx_handlers()) == 1
    CliRunner().invoke(cli, ["-v", "unmatched"])
    assert _httpx_handlers() == []
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_help_documents_both_levels() -> None:
    out = CliRunner().invoke(cli, ["--help"]).output
    assert "-vv" in out
    assert "debug" in out.lower()


def _boom(*_a: object, **_k: object) -> None:
    raise ConfigError("boom")


@pytest.mark.parametrize(("args", "traceback"), [([], False), (["-v"], False), (["-vv"], True)])
def test_traceback_only_at_debug(
    args: list[str], traceback: bool, paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(paths)
    monkeypatch.setattr("ammirror.cli.make_apple", _boom)
    result = CliRunner().invoke(cli, [*args, "playlists"])
    assert result.exit_code == 2
    assert "error: boom" in result.stderr
    assert ("Traceback (most recent call last)" in result.stderr) is traceback


def test_sync_verbose_narrates_progress(fakes: tuple[FakeApple, FakeYtm]) -> None:
    result = CliRunner().invoke(cli, ["-v", "sync"])
    assert result.exit_code == 0, result.output
    err = result.stderr
    assert f"{BULLET} Fetching 'Gym'… 2 tracks" in err
    assert "resolved 2 tracks: 0 cached, 0 pinned, 2 searched → 1 matched, 1 unmatched" in err
    assert "planned 1 ops" in err
    assert f"{BULLET} → create playlist 'Gym' with 1 track" in err
    assert "DEBUG" not in err
    assert "Fetching" not in result.stdout


def test_sync_without_flags_has_no_log_lines(fakes: tuple[FakeApple, FakeYtm]) -> None:
    result = CliRunner().invoke(cli, ["sync"])
    assert result.exit_code == 0, result.output
    assert BULLET not in result.stderr
    assert "DEBUG" not in result.stderr


def test_sync_debug_logs_decisions(fakes: tuple[FakeApple, FakeYtm]) -> None:
    result = CliRunner().invoke(cli, ["-vv", "sync"])
    assert result.exit_code == 0, result.output
    err = result.stderr
    assert "DEBUG ammirror.sync: searching" in err
    assert "matched" in err and "v1" in err
    assert "unmatched" in err
    assert "DEBUG ammirror.sync: plan 'Gym':" in err
    assert "DEBUG ammirror.match: candidate 1 for Artist - Song 1" in err


def test_second_sync_logs_cache_hits(fakes: tuple[FakeApple, FakeYtm]) -> None:
    CliRunner().invoke(cli, ["sync"])
    result = CliRunner().invoke(cli, ["-vv", "sync"])
    assert "cache hit" in result.stderr
    assert "2 cached" in result.stderr


SENTINELS = [
    "SENTINEL-DEV-TOKEN",
    "SENTINEL-USER-TOKEN",
    "SENTINEL-COOKIE-VALUE",
    "SENTINEL-AUTH-HEADER",
    "SENTINEL-P8-KEY",
]


def _assert_no_secrets(text: str) -> None:
    for s in SENTINELS:
        assert s not in text, s


def test_sync_debug_never_logs_secrets(
    fakes: tuple[FakeApple, FakeYtm], paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths.apple_user_token_file.write_text("SENTINEL-USER-TOKEN")
    paths.ytm_auth_file.write_text('{"cookie": "SENTINEL-COOKIE-VALUE"}')
    result = CliRunner().invoke(cli, ["-vv", "sync"])
    assert result.exit_code == 0, result.output
    assert "DEBUG" in result.stderr
    _assert_no_secrets(result.output)


def test_real_clients_debug_never_log_secrets(
    paths: Paths, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The real Apple/YTM clients, with secret-bearing headers, driven under debug logging."""
    import httpx

    from ammirror.apple.client import AppleClient
    from ammirror.ytm.client import YtmusicapiClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    class StubYt:
        def search(self, *_a: object, **_k: object) -> list[object]:
            return []

    logger = logging.getLogger("ammirror")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(caplog.handler)
    try:
        apple = AppleClient(
            "SENTINEL-DEV-TOKEN", "SENTINEL-USER-TOKEN", transport=httpx.MockTransport(handler)
        )
        apple.library_playlists()
        YtmusicapiClient(StubYt()).search_songs("q")
    finally:
        logger.removeHandler(caplog.handler)
    assert "/v1/me/library/playlists" in caplog.text
    _assert_no_secrets(caplog.text)


def test_auth_apple_debug_never_logs_secrets(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(paths)
    key_file = paths.config_dir / "AuthKey.p8"
    pem = key_file.read_text()
    # Text before the PEM armor is ignored by the parser, so the sentinel rides along with
    # the real key and would show up if the key file's contents were ever logged.
    key_file.write_text(f"SENTINEL-P8-KEY\n{pem}")
    seen: dict[str, str] = {}

    def fake_flow(developer_token: str) -> str:
        seen["dev"] = developer_token
        return "SENTINEL-USER-TOKEN"

    monkeypatch.setattr("ammirror.cli.run_auth_flow", fake_flow)
    result = CliRunner().invoke(cli, ["-vv", "auth", "apple"])
    assert result.exit_code == 0, result.output
    _assert_no_secrets(result.output)
    assert seen["dev"] not in result.output
    assert "".join(pem.splitlines()[1:-1])[:40] not in result.output


def test_retry_logs_never_include_secrets(caplog: pytest.LogCaptureFixture) -> None:
    import httpx
    from ytmusicapi.exceptions import YTMusicServerError

    from ammirror.apple.client import AppleClient
    from ammirror.ytm.client import YtmusicapiClient

    responses = iter([httpx.Response(429, headers={"Retry-After": "7"}), None])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses) or httpx.Response(200, json={"data": []})

    class FlakyYt:
        calls = 0

        def search(self, *_a: object, **_k: object) -> list[object]:
            self.calls += 1
            if self.calls == 1:
                raise YTMusicServerError("HTTP 429 SENTINEL-COOKIE-VALUE")
            return []

    delays: list[float] = []
    logger = logging.getLogger("ammirror")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(caplog.handler)
    try:
        AppleClient(
            "SENTINEL-DEV-TOKEN",
            "SENTINEL-USER-TOKEN",
            transport=httpx.MockTransport(handler),
            sleep=delays.append,
        ).library_playlists()
        YtmusicapiClient(FlakyYt(), sleep=delays.append).search_songs("q")
    finally:
        logger.removeHandler(caplog.handler)
    assert delays == [7.0, 1.0]
    assert "retrying in 7s" in caplog.text
    assert "rate limited (HTTP 429); retrying in 1s" in caplog.text
    _assert_no_secrets(caplog.text)


def test_auth_apple_flow_logs_url_without_tokens(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from ammirror.apple.auth import run_auth_flow

    logger = logging.getLogger("ammirror")
    logger.setLevel(logging.INFO)
    logger.addHandler(caplog.handler)
    try:
        with pytest.raises(Exception, match="timed out"):
            run_auth_flow("SENTINEL-DEV-TOKEN", open_browser=lambda _u: None, timeout=0.01)
    finally:
        logger.removeHandler(caplog.handler)
    assert "Waiting for browser sign-in at http://127.0.0.1:" in caplog.text
    _assert_no_secrets(caplog.text)


def test_auth_ytm_logs_header_names_only(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ytmusicapi, "setup", lambda headers_raw: '{"cookie": "x"}')
    pasted = (
        "POST /youtubei/v1/browse HTTP/3\n"
        "cookie: SENTINEL-COOKIE-VALUE\ncontent-length: 9\n"
        "authorization: SENTINEL-AUTH-HEADER\nuser-agent: x\n"
    )
    result = CliRunner().invoke(cli, ["-vv", "auth", "ytm"], input=pasted)
    assert result.exit_code == 0, result.output
    assert "Saved credentials (3 headers kept, 2 dropped: " in result.stderr
    assert "content-length" in result.stderr
    _assert_no_secrets(result.output)

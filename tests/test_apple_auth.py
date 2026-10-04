import json
import re
import threading

import httpx
import pytest

from ammirror.apple.auth import render_page, run_auth_flow
from ammirror.errors import AuthError


def _browser(token: str | None, *, nonce_override: str | None = None):
    """Fake browser: loads the page, then posts a token back like MusicKit JS would."""
    seen: dict[str, object] = {}

    def open_browser(url: str) -> None:
        def run() -> None:
            page = httpx.get(url, trust_env=False)
            seen["page"] = page.text
            seen["referrer_policy"] = page.headers.get("referrer-policy")
            nonce = re.search(r'const NONCE = "([^"]+)"', page.text)
            assert nonce
            body = {"nonce": nonce_override or nonce.group(1), "token": token}
            seen["post_status"] = httpx.post(
                url + "token", content=json.dumps(body), trust_env=False
            ).status_code

        threading.Thread(target=run, daemon=True).start()

    return open_browser, seen


def test_flow_returns_token() -> None:
    open_browser, seen = _browser("user-token-abc")
    assert run_auth_flow("dev.jwt.token", open_browser=open_browser, timeout=5) == "user-token-abc"
    assert "dev.jwt.token" in str(seen["page"])
    assert "musickit/v3/musickit.js" in str(seen["page"])
    assert seen["referrer_policy"] == "strict-origin-when-cross-origin"


def test_flow_cancelled_raises() -> None:
    open_browser, _ = _browser(None)
    with pytest.raises(AuthError, match="cancelled"):
        run_auth_flow("dev", open_browser=open_browser, timeout=5)


def test_flow_rejects_wrong_nonce_then_times_out() -> None:
    open_browser, seen = _browser("tok", nonce_override="forged")
    with pytest.raises(AuthError, match="timed out"):
        run_auth_flow("dev", open_browser=open_browser, timeout=1)
    assert seen["post_status"] == 403


def test_render_page_escapes_values() -> None:
    page = render_page('a"b', "n0nce")
    assert 'const DEVELOPER_TOKEN = "a\\"b";' in page
    assert 'const NONCE = "n0nce";' in page

import json
import logging
import secrets
import threading
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from string import Template

from ammirror.errors import AuthError

log = logging.getLogger(__name__)

_PAGE = Template("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ammirror: Apple Music sign-in</title>
<script src="https://js-cdn.music.apple.com/musickit/v3/musickit.js" async></script>
<style>
body{font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem}
</style>
</head>
<body>
<h1>ammirror</h1>
<p id="status">Loading MusicKit…</p>
<button id="go" disabled>Sign in to Apple Music</button>
<script>
const DEVELOPER_TOKEN = $developer_token;
const NONCE = $nonce;
const statusEl = document.getElementById("status");
const go = document.getElementById("go");

async function send(token) {
  await fetch("/token", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({nonce: NONCE, token: token || null}),
  });
}

async function init() {
  try {
    await MusicKit.configure({
      developerToken: DEVELOPER_TOKEN,
      app: {name: "ammirror", build: $build},
    });
    statusEl.textContent = "Ready.";
    go.disabled = false;
  } catch (e) {
    statusEl.textContent = "MusicKit failed to configure: " + e;
  }
}

go.addEventListener("click", async () => {
  go.disabled = true;
  try {
    const token = await MusicKit.getInstance().authorize();
    await send(token);
    statusEl.textContent = token ? "Signed in. You can close this tab." : "Sign-in cancelled.";
  } catch (e) {
    statusEl.textContent = "Sign-in failed: " + e;
    await send(null);
  }
});

if (window.MusicKit) { init(); } else { document.addEventListener("musickitloaded", init); }
</script>
</body>
</html>
""")


def render_page(developer_token: str, nonce: str) -> str:
    return _PAGE.substitute(
        developer_token=json.dumps(developer_token),
        nonce=json.dumps(nonce),
        build=json.dumps(version("ammirror")),
    )


def run_auth_flow(
    developer_token: str,
    *,
    open_browser: Callable[[str], object] = webbrowser.open,
    timeout: float = 300.0,
) -> str:
    """Serve a local MusicKit JS page and return the Music-User-Token it posts back."""
    nonce = secrets.token_urlsafe(16)
    page = render_page(developer_token, nonce).encode()
    result: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def _host_ok(self) -> bool:
            # Blocks DNS-rebinding: a page on another origin that resolves to 127.0.0.1
            # still sends its own name in Host.
            if self.headers.get("Host") == f"127.0.0.1:{server.server_port}":
                return True
            self.send_error(403)
            return False

        def do_GET(self) -> None:
            if not self._host_ok():
                return
            if self.path != "/":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            # MusicKit sign-in fails with a no-referrer policy (Apple forum thread 709966).
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def do_POST(self) -> None:
            if not self._host_ok():
                return
            if self.path != "/token":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                data = json.loads(self.rfile.read(length))
            except ValueError:
                self.send_error(400)
                return
            if not isinstance(data, dict) or data.get("nonce") != nonce:
                self.send_error(403)
                return
            token = data.get("token")
            if isinstance(token, str) and token:
                result["token"] = token
            self.send_response(204)
            self.end_headers()
            done.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        log.info("Waiting for browser sign-in at %s", url)
        open_browser(url)
        if not done.wait(timeout):
            raise AuthError("apple", "sign-in timed out")
    finally:
        server.shutdown()
        server.server_close()
    if "token" not in result:
        raise AuthError("apple", "sign-in was cancelled or failed")
    return result["token"]

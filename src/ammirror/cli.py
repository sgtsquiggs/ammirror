import logging
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any, Protocol

import click
import ytmusicapi
from ytmusicapi.exceptions import YTMusicError

from ammirror.apple.auth import run_auth_flow
from ammirror.apple.client import AppleClient, AppleLibrary
from ammirror.apple.token import load_developer_token
from ammirror.config import Config, Paths, load_config, save_sync_playlists, write_secret
from ammirror.errors import YTMUSICAPI_HINT, AmmirrorError, AuthError, ConfigError
from ammirror.picker import PickerRow, ask_playlists, build_rows, group_headings, playlist_changes
from ammirror.state import State
from ammirror.sync import describe, run_sync
from ammirror.ytm.client import YtmClient, YtmusicapiClient, sanitize_ytm_headers_report

log = logging.getLogger(__name__)


@dataclass
class CliContext:
    paths: Paths
    verbosity: int

    def config(self) -> Config:
        return load_config(self.paths.config_file)


_HANDLER_TAG = "_ammirror_handler"
_INFO_FORMAT = "· %(message)s"
_DEBUG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(verbosity: int) -> None:
    """Send `ammirror` logs to stderr: WARNING by default, INFO for -v, DEBUG for -vv.

    Safe to call repeatedly: it replaces the handler it installed before.
    """
    logger = logging.getLogger("ammirror")
    httpx_logger = logging.getLogger("httpx")
    for lg in (logger, httpx_logger):
        for old in list(lg.handlers):
            if getattr(old, _HANDLER_TAG, False):
                lg.removeHandler(old)
    level = logging.DEBUG if verbosity >= 2 else logging.INFO if verbosity == 1 else logging.WARNING
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(_DEBUG_FORMAT, datefmt="%H:%M:%S")
        if level == logging.DEBUG
        else logging.Formatter(_INFO_FORMAT)
    )
    setattr(handler, _HANDLER_TAG, True)
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    # httpx logs only method, URL and status. httpcore and urllib3 can dump headers, so
    # they stay at WARNING. httpx is outside the `ammirror` tree, so it needs the handler too.
    if level == logging.DEBUG:
        httpx_logger.addHandler(handler)
        httpx_logger.setLevel(logging.INFO)
        httpx_logger.propagate = False
    else:
        httpx_logger.setLevel(logging.WARNING)
        httpx_logger.propagate = True


class _Group(click.Group):
    """Turn known errors into a one-line message and a meaningful exit code."""

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except AmmirrorError as e:
            obj = ctx.obj
            if isinstance(obj, CliContext) and obj.verbosity >= 2:
                traceback.print_exc()
            click.echo(f"error: {e}", err=True)
            ctx.exit(_exit_code(e))


def _exit_code(e: AmmirrorError) -> int:
    return 2 if isinstance(e, AuthError | ConfigError) else 1


def get_ctx(ctx: click.Context) -> CliContext:
    obj = ctx.find_object(CliContext)
    assert obj is not None
    return obj


@click.group(cls=_Group)
@click.version_option(version("ammirror"), prog_name="ammirror")
@click.option(
    "--verbose",
    "-v",
    count=True,
    help="-v narrates progress (verbose); -vv adds per-item decisions, request details "
    "and error tracebacks (debug). Logs go to stderr; secrets are never logged.",
)
@click.pass_context
def cli(ctx: click.Context, verbose: int) -> None:
    """Mirror Apple Music playlists and Favorite Songs into YouTube Music."""
    configure_logging(verbose)
    ctx.obj = CliContext(Paths.from_env(), verbose)


@cli.group()
def auth() -> None:
    """Sign in to Apple Music or YouTube Music."""


@auth.command("apple")
@click.pass_context
def auth_apple(ctx: click.Context) -> None:
    """Sign in to Apple Music in your browser and save the user token."""
    obj = get_ctx(ctx)
    developer_token = load_developer_token(obj.config().apple)
    click.echo("Opening your browser to sign in to Apple Music…")
    user_token = run_auth_flow(developer_token)
    write_secret(obj.paths.apple_user_token_file, user_token)
    click.echo(f"Saved Apple Music user token to {obj.paths.apple_user_token_file}")


_YTM_HELP = """\
1. Open https://music.youtube.com in your browser, signed in.
2. Open DevTools (F12) → Network, filter for "browse", and click around until a
   POST request to music.youtube.com/youtubei/v1/browse appears.
3. Copy its request headers (Firefox: right-click → Copy Value → Copy Request Headers;
   Chrome or Edge: Headers tab → Request Headers → select all and copy).
4. Paste them here, then press Ctrl-D (Ctrl-Z then Enter on Windows) on an empty line.
"""


@auth.command("ytm")
@click.pass_context
def auth_ytm(ctx: click.Context) -> None:
    """Save YouTube Music browser credentials from pasted request headers."""
    obj = get_ctx(ctx)
    click.echo(_YTM_HELP, err=True)
    headers = sys.stdin.read()
    if not headers.strip():
        raise click.UsageError("no headers pasted")
    cleaned, dropped = sanitize_ytm_headers_report(headers)
    try:
        # Without a filepath, setup only parses the headers and returns the JSON to save,
        # so the credentials are written once, through write_secret, with mode 0600.
        credentials = ytmusicapi.setup(headers_raw=cleaned)
    except YTMusicError as e:
        raise ConfigError(f"could not parse pasted headers: {e}") from e
    target = obj.paths.ytm_auth_file
    write_secret(target, credentials)
    kept = sum(1 for line in cleaned.splitlines() if line.strip())
    log.info(
        "Saved credentials (%d headers kept, %d dropped: %s)",
        kept,
        len(dropped),
        ", ".join(dropped) or "none",
    )
    click.echo(f"Saved YouTube Music credentials to {target}")


def make_apple(paths: Paths, cfg: Config) -> AppleLibrary:
    return AppleClient.from_paths(paths, cfg)


def make_ytm(paths: Paths) -> YtmClient:
    return YtmusicapiClient.from_auth_file(paths.ytm_auth_file)


class _AuthCheckable(Protocol):
    def check_auth(self) -> str: ...


def _checked[C: _AuthCheckable](
    service: str, make: Callable[[], C], failures: list[AmmirrorError]
) -> C | None:
    try:
        client = make()
        log.info("%s: %s", service, client.check_auth())
    except AmmirrorError as e:
        log.debug("%s auth check failed", service, exc_info=True)
        failures.append(e)
        return None
    return client


def connect(ctx: click.Context, paths: Paths, cfg: Config) -> tuple[AppleLibrary, YtmClient]:
    """Build both clients and check their auth, reporting every failure before exiting."""
    failures: list[AmmirrorError] = []
    apple = _checked("Apple Music", lambda: make_apple(paths, cfg), failures)
    ytm = _checked("YouTube Music", lambda: make_ytm(paths), failures)
    if apple is None or ytm is None:
        for e in failures:
            click.echo(f"error: {e}", err=True)
        ctx.exit(max(map(_exit_code, failures)))
    return apple, ytm


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


@cli.command()
@click.option(
    "--edit",
    is_flag=True,
    help="Choose the playlists to mirror in an interactive checklist and save them "
    "as sync.playlists in the config.",
)
@click.pass_context
def playlists(ctx: click.Context, edit: bool) -> None:
    """List Apple Music playlists and which ones are mirrored."""
    obj = get_ctx(ctx)
    cfg = obj.config()
    if edit and not is_interactive():
        raise click.UsageError("--edit needs an interactive terminal")
    available = make_apple(obj.paths, cfg).library_playlists()
    state = State(obj.paths.state_db)
    try:
        mirrored = state.playlist_mappings()
    finally:
        state.close()
    rows = build_rows(available, cfg.sync.playlists, mirrored, cfg.sync.favorites_playlist)
    if edit:
        _edit_playlists(ctx, obj.paths, rows)
        return
    headings = group_headings(rows)
    for i, r in enumerate(rows):
        if i in headings:
            click.echo(headings[i])
        tags = ("selected", *r.tags) if r.checked else r.tags
        click.echo(f"  {r.name}  [{', '.join(tags)}]" if tags else f"  {r.name}")


def _edit_playlists(ctx: click.Context, paths: Paths, rows: list[PickerRow]) -> None:
    checked = ask_playlists(rows)
    if checked is None:
        click.echo("cancelled; config unchanged", err=True)
        ctx.exit(1)
    changes = playlist_changes(rows, checked)
    if not changes.changed:
        click.echo("no changes")
        return
    save_sync_playlists(paths.config_file, changes.names)
    if changes.added:
        click.echo(f"added: {', '.join(changes.added)}")
    if changes.removed:
        click.echo(f"removed: {', '.join(changes.removed)}")
        click.echo("removed playlists keep their YouTube Music copy")
    click.echo(f"saved sync.playlists to {paths.config_file}")


@cli.command()
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the plan; never changes YouTube Music, but caches match results locally.",
)
@click.option(
    "--retry-unmatched", is_flag=True, help="Search again for previously unmatched tracks."
)
@click.pass_context
def sync(ctx: click.Context, dry_run: bool, retry_unmatched: bool) -> None:
    """Mirror Apple Music into YouTube Music."""
    obj = get_ctx(ctx)
    cfg = obj.config()
    apple, ytm = connect(ctx, obj.paths, cfg)
    state = State(obj.paths.state_db)
    try:
        report = run_sync(
            cfg.sync, apple, ytm, state, dry_run=dry_run, retry_unmatched=retry_unmatched
        )
    finally:
        state.close()

    for w in report.warnings:
        click.secho(f"warning: {w}", fg="yellow", err=True)
    for e in report.errors:
        click.secho(f"error: {e}", fg="red", err=True)
    for op in report.plan.ops:
        click.echo(("would " if dry_run else "") + describe(op))
    tail = f"{report.unmatched} unmatched (see `ammirror unmatched`)"
    failures = report.result.failures if report.result else []
    if report.result is None:
        click.echo(f"dry run: {len(report.plan.ops)} ops planned, {tail}")
    else:
        for op, err in failures:
            click.secho(f"failed: {describe(op)}: {err}", fg="red", err=True)
        click.echo(f"applied {report.result.applied}, {len(failures)} failed, {tail}")
    if any(YTMUSICAPI_HINT in m for m in [*report.errors, *(err for _, err in failures)]):
        click.secho(
            "hint: some YouTube Music responses could not be parsed; "
            "upgrade ammirror (e.g. `uv tool upgrade ammirror` or `pipx upgrade ammirror`)",
            fg="yellow",
            err=True,
        )
    if report.errors or failures:
        ctx.exit(1)


@cli.command()
@click.pass_context
def unmatched(ctx: click.Context) -> None:
    """List tracks that couldn't be matched on YouTube Music."""
    state = State(get_ctx(ctx).paths.state_db)
    try:
        rows = state.list_unmatched()
    finally:
        state.close()
    if not rows:
        click.echo("no unmatched tracks")
        return
    for r in rows:
        click.echo(f"{r.apple_id}  {r.artist} - {r.title}  [{r.reason}]")
        if r.candidate_video_id and r.candidate_score is not None:
            click.echo(
                f"    best guess: {r.candidate_title} ({r.candidate_video_id}, "
                f"score {r.candidate_score:.2f})"
            )
            click.echo(f"    accept with: ammirror pin {r.apple_id} {r.candidate_video_id}")


@cli.command()
@click.argument("apple_id")
@click.argument("video_id")
@click.pass_context
def pin(ctx: click.Context, apple_id: str, video_id: str) -> None:
    """Match an Apple Music track (APPLE_ID) to a YouTube Music VIDEO_ID by hand."""
    state = State(get_ctx(ctx).paths.state_db)
    try:
        state.put_match(apple_id, video_id, 1.0, "pin")
        state.clear_unmatched(apple_id)
    finally:
        state.close()
    click.echo(f"pinned {apple_id} → {video_id}; it will be used on the next sync")


def main() -> None:
    cli()

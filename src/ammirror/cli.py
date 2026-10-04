import sys
import traceback
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

import click
import ytmusicapi

from ammirror.apple.auth import run_auth_flow
from ammirror.apple.client import AppleClient, AppleLibrary
from ammirror.apple.token import load_developer_token
from ammirror.config import Config, Paths, load_config, write_secret
from ammirror.errors import AmmirrorError, AuthError, ConfigError
from ammirror.state import State
from ammirror.sync import describe, run_sync, select_playlists
from ammirror.ytm.client import YtmClient, YtmusicapiClient


@dataclass
class CliContext:
    paths: Paths
    verbose: bool

    def config(self) -> Config:
        return load_config(self.paths.config_file)


class _Group(click.Group):
    """Turn known errors into a one-line message and a meaningful exit code."""

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except AmmirrorError as e:
            obj = ctx.obj
            if isinstance(obj, CliContext) and obj.verbose:
                traceback.print_exc()
            click.echo(f"error: {e}", err=True)
            ctx.exit(2 if isinstance(e, AuthError | ConfigError) else 1)


def get_ctx(ctx: click.Context) -> CliContext:
    obj = ctx.find_object(CliContext)
    assert obj is not None
    return obj


@click.group(cls=_Group)
@click.version_option(version("ammirror"), prog_name="ammirror")
@click.option("--verbose", "-v", is_flag=True, help="Show tracebacks for errors.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Mirror Apple Music playlists and Favorite Songs into YouTube Music."""
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
   Chrome: Headers tab → Request Headers → select all and copy).
4. Paste them here, then press Ctrl-D on an empty line.
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
    target = obj.paths.ytm_auth_file
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    ytmusicapi.setup(filepath=str(target), headers_raw=headers)
    target.chmod(0o600)
    click.echo(f"Saved YouTube Music credentials to {target}")


def make_apple(paths: Paths, cfg: Config) -> AppleLibrary:
    return AppleClient.from_paths(paths, cfg)


def make_ytm(paths: Paths) -> YtmClient:
    return YtmusicapiClient.from_auth_file(paths.ytm_auth_file)


@cli.command()
@click.pass_context
def playlists(ctx: click.Context) -> None:
    """List Apple Music playlists and which ones are mirrored."""
    obj = get_ctx(ctx)
    cfg = obj.config()
    available = make_apple(obj.paths, cfg).library_playlists()
    selected, _ = select_playlists(available, cfg.sync.playlists)
    selected_ids = {p.id for p in selected}
    state = State(obj.paths.state_db)
    try:
        mirrored = state.playlist_mappings()
    finally:
        state.close()
    for p in available:
        flags = []
        if p.id in selected_ids:
            flags.append("selected")
        if p.id in mirrored:
            flags.append("mirrored")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        click.echo(f"{p.name}{suffix}")


@cli.command()
@click.option("--dry-run", is_flag=True, help="Print the plan without changing YouTube Music.")
@click.option(
    "--retry-unmatched", is_flag=True, help="Search again for previously unmatched tracks."
)
@click.pass_context
def sync(ctx: click.Context, dry_run: bool, retry_unmatched: bool) -> None:
    """Mirror Apple Music into YouTube Music."""
    obj = get_ctx(ctx)
    cfg = obj.config()
    apple = make_apple(obj.paths, cfg)
    ytm = make_ytm(obj.paths)
    state = State(obj.paths.state_db)
    try:
        report = run_sync(
            cfg.sync, apple, ytm, state, dry_run=dry_run, retry_unmatched=retry_unmatched
        )
    finally:
        state.close()

    for w in report.warnings:
        click.secho(f"warning: {w}", fg="yellow", err=True)
    for op in report.plan.ops:
        click.echo(("would " if dry_run else "") + describe(op))
    tail = f"{report.unmatched} unmatched (see `ammirror unmatched`)"
    if report.result is None:
        click.echo(f"dry run: {len(report.plan.ops)} ops planned, {tail}")
        return
    failures = report.result.failures
    for op, err in failures:
        click.secho(f"failed: {describe(op)}: {err}", fg="red", err=True)
    click.echo(f"applied {report.result.applied}, {len(failures)} failed, {tail}")
    if failures:
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

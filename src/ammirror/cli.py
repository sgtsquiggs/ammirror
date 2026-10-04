import traceback
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

import click

from ammirror.apple.auth import run_auth_flow
from ammirror.apple.token import load_developer_token
from ammirror.config import Config, Paths, load_config, write_secret
from ammirror.errors import AmmirrorError, AuthError, ConfigError


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


def main() -> None:
    cli()

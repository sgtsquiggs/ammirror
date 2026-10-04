from importlib.metadata import version

import click


@click.group()
@click.version_option(version("ammirror"), prog_name="ammirror")
def cli() -> None:
    """Mirror Apple Music playlists and Favorite Songs into YouTube Music."""


def main() -> None:
    cli()

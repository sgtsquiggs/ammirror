from click.testing import CliRunner

from ammirror.cli import cli


def test_version() -> None:
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "ammirror" in result.output

"""Smoke coverage for the generated CLI entry point."""

from typer.testing import CliRunner

from stance.cli import app


def test_example_command() -> None:
    result = CliRunner().invoke(app, ["example", "World"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "Hello, World!"

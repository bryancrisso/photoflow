"""Main Typer CLI entry point for photoflow."""

from __future__ import annotations

import typer

from photoflow.commands.cache import cache_app
from photoflow.commands.init_album import init_command
from photoflow.commands.stamp import stamp_command


app = typer.Typer(
    name="photoflow",
    help="Photography workflow CLI for album initialization, 3-layer metadata stamping, and rclone VFS caching.",
    no_args_is_help=True,
)

app.command(name="init", help="Initialize a new flat album, parent trip album, or sub-album.")(
    init_command
)
app.command(name="stamp", help="Stamp 3-layer EXIF and XMP sidecar metadata onto album photos.")(
    stamp_command
)
app.add_typer(cache_app, name="cache")


if __name__ == "__main__":
    app()

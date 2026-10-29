"""SQLite bridge for safely updating digiKam's Albums table in digikam4.db."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from rich.console import Console

from photoflow.core.yaml_loader import AlbumConfig


console = Console()


def compute_relative_path(album_path: Path | str, archive_root: Path | str) -> str:
    """Compute digiKam's relativePath relative to archive_root.

    Always starts with '/' and has no trailing slash (unless root '/').
    """
    alb = Path(album_path).expanduser()
    root = Path(archive_root).expanduser()

    try:
        rel = alb.relative_to(root)
    except ValueError:
        try:
            rel = alb.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError(
                f"Album path '{alb}' is not inside archive_root '{root}'."
            ) from exc

    rel_posix = rel.as_posix().strip("/")
    if not rel_posix or rel_posix == ".":
        return "/"
    return f"/{rel_posix}"


def format_album_caption(album: AlbumConfig) -> str:
    """Format a readable digiKam album caption containing title, notes, and gear summary."""
    sections: list[str] = []

    if album.title:
        sections.append(album.title.strip())

    if album.notes:
        sections.append(album.notes.strip())

    gear_lines: list[str] = []
    for dig in album.gear.digital:
        if dig.lenses:
            gear_lines.append(f"Digital: {dig.body} ({', '.join(dig.lenses)})")
        else:
            gear_lines.append(f"Digital: {dig.body}")

    for roll in album.gear.film:
        stock_str = f"{roll.stock_brand} {roll.stock_name}".strip()
        lenses_str = ", ".join(roll.lenses) if roll.lenses else (roll.default_lens or "N/A")
        roll_summary = (
            f"Film [{roll.roll_id}]: {roll.camera_model} | "
            f"{stock_str} (ISO {roll.iso_shot}, {roll.process}) | "
            f"Lenses: {lenses_str}"
        )
        gear_lines.append(roll_summary)

    if gear_lines:
        sections.append("Gear:\n" + "\n".join(f"- {line}" for line in gear_lines))

    return "\n\n".join(sections)


def update_digikam_album(
    album: AlbumConfig,
    album_path: Path | str,
    archive_root: Path | str,
    db_path: Path | str,
    *,
    quiet: bool = False,
) -> bool:
    """Update the Albums row in digikam4.db for the given album.

    Returns True if a row was updated, False if not yet indexed or DB unavailable.
    """
    db_file = Path(db_path).expanduser()
    if not db_file.is_file():
        if not quiet:
            console.print(
                f"[dim]Notice: digiKam database not found at {db_file}. "
                "Album metadata will sync on the next `photoflow stamp` run.[/dim]"
            )
        return False

    try:
        relative_path = compute_relative_path(album_path, archive_root)
    except ValueError:
        if not quiet:
            console.print(
                f"[dim]Notice: '{album_path}' is outside archive_root '{archive_root}'. "
                "Skipping digikam4.db sync.[/dim]"
            )
        return False

    caption = format_album_caption(album)

    try:
        with sqlite3.connect(db_file, timeout=5.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE Albums
                SET date = :date_start,
                    caption = :caption,
                    collection = :category
                WHERE relativePath = :relative_path;
                """,
                {
                    "date_start": album.date_start,
                    "caption": caption,
                    "category": album.category,
                    "relative_path": relative_path,
                },
            )
            conn.commit()
            updated = cursor.rowcount > 0
    except sqlite3.Error as exc:
        if not quiet:
            console.print(
                f"[yellow]Warning: Could not update digikam4.db ({exc}). "
                "Metadata will sync on the next `photoflow stamp` run.[/yellow]"
            )
        return False

    if not updated and not quiet:
        console.print(
            f"[cyan]Notice: Folder '{relative_path}' is not yet indexed in digikam4.db. "
            "Metadata will sync automatically on the next `photoflow stamp` run.[/cyan]"
        )

    return updated

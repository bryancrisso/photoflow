"""Implementation of `photoflow cache <status|warm|evict>` for rclone VFS cache management."""

from __future__ import annotations

import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

from photoflow.core.config import load_profiles


console = Console()
cache_app = typer.Typer(
    name="cache",
    help="Manage the local rclone VFS NVMe cache (status, warm, evict).",
    no_args_is_help=True,
)


def format_bytes(num_bytes: int | float) -> str:
    """Format byte count into human-readable string."""
    val = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(val) < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(val)} B"
            return f"{val:.2f} {unit} ({int(num_bytes)} B)"
        val /= 1024.0
    return f"{int(num_bytes)} B"


def fetch_vfs_stats(rc_url: str) -> dict[str, Any]:
    """Call POST `<rc_url>/vfs/stats` and return parsed JSON."""
    url = f"{rc_url.rstrip('/')}/vfs/stats"
    resp = httpx.post(url, json={}, timeout=5.0)
    resp.raise_for_status()
    return resp.json()


def parse_stats_summary(data: dict[str, Any]) -> tuple[int, int, int, int]:
    """Extract (uploads_in_progress, uploads_queued, bytes_used, cached_files) from vfs/stats JSON."""
    disk_cache = data.get("diskCache") if isinstance(data.get("diskCache"), dict) else data
    uploads_in_progress = int(
        disk_cache.get("uploadsInProgress", data.get("uploadsInProgress", 0))
    )
    uploads_queued = int(
        disk_cache.get("uploadsQueued", data.get("uploadsQueued", 0))
    )
    bytes_used = int(
        disk_cache.get(
            "bytesUsed",
            data.get("bytesUsed", data.get("totalCacheSize", 0)),
        )
    )
    files_count = int(disk_cache.get("files", data.get("files", 0)))
    return uploads_in_progress, uploads_queued, bytes_used, files_count


def _compute_rel_album_path(album_path: Path, archive_root: Path) -> str:
    alb = album_path.expanduser()
    root = archive_root.expanduser()
    try:
        rel = alb.relative_to(root)
        return rel.as_posix().strip("/")
    except ValueError:
        try:
            rel = alb.resolve().relative_to(root.resolve())
            return rel.as_posix().strip("/")
        except ValueError:
            return alb.name


def _read_file_to_devnull(file_path: Path) -> int:
    """Stream a file to /dev/null to trigger rclone VFS full caching."""
    total = 0
    chunk_size = 1024 * 1024  # 1 MiB
    with file_path.open("rb") as src, open(os.devnull, "wb") as null_out:
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            null_out.write(chunk)
            total += len(chunk)
    return total


def collect_warm_files(album_path: Path, *, with_video: bool = False) -> list[Path]:
    """Walk `album_path` and return all files, pruning `Video/` unless `with_video=True`."""
    files: list[Path] = []
    for root, dirs, filenames in os.walk(album_path):
        if not with_video:
            dirs[:] = [d for d in dirs if d.lower() != "video"]
        dirs.sort()
        for fname in sorted(filenames):
            files.append(Path(root) / fname)
    return files


@cache_app.command("status")
def cache_status(
    rc_url: Annotated[
        str | None,
        typer.Option("--rc-url", help="Override rclone rc URL (default http://127.0.0.1:5572)."),
    ] = None,
    profiles_path: Annotated[
        Path | None,
        typer.Option("--config", help="Override path to profiles.yaml."),
    ] = None,
) -> None:
    """Display rclone VFS cache statistics (`uploadsInProgress`, `uploadsQueued`, cache size)."""
    profiles = load_profiles(profiles_path)
    effective_url = rc_url or profiles.rclone_rc_url

    try:
        stats = fetch_vfs_stats(effective_url)
    except httpx.HTTPError as exc:
        console.print(f"[red]Failed to query rclone rc at {effective_url}/vfs/stats: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    in_progress, queued, bytes_used, files_count = parse_stats_summary(stats)

    table = Table(title="Rclone VFS Cache Status")
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="bold")

    table.add_row("uploadsInProgress", str(in_progress))
    table.add_row("uploadsQueued", str(queued))
    table.add_row("Total Cache Size", format_bytes(bytes_used))
    table.add_row("Cached Files", str(files_count))

    console.print(table)


@cache_app.command("warm")
def cache_warm(
    album_path: Annotated[
        Path,
        typer.Argument(help="Path to the album directory to pre-warm into local NVMe VFS cache."),
    ],
    with_video: Annotated[
        bool,
        typer.Option("--with-video", help="Include Video/ subfolder when warming cache."),
    ] = False,
    rc_url: Annotated[
        str | None,
        typer.Option("--rc-url", help="Override rclone rc URL."),
    ] = None,
    archive_root: Annotated[
        Path | None,
        typer.Option("--archive-root", help="Override archive_root directory."),
    ] = None,
    profiles_path: Annotated[
        Path | None,
        typer.Option("--config", help="Override path to profiles.yaml."),
    ] = None,
) -> None:
    """Refresh rclone directory metadata and pre-warm album files into local NVMe cache."""
    profiles = load_profiles(profiles_path)
    effective_url = (rc_url or profiles.rclone_rc_url).rstrip("/")
    root_dir = (archive_root or profiles.archive_root_path).expanduser()
    target_dir = album_path.expanduser()

    if not target_dir.is_dir():
        console.print(f"[red]Album directory does not exist: {target_dir}[/red]")
        raise typer.Exit(code=1)

    rel_album = _compute_rel_album_path(target_dir, root_dir)

    # 1. Trigger vfs/refresh via rclone rc
    try:
        resp = httpx.post(
            f"{effective_url}/vfs/refresh",
            json={"dir": rel_album, "recursive": "true"},
            timeout=30.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        console.print(
            f"[yellow]Warning: rclone vfs/refresh call failed ({exc}); proceeding with local file reads.[/yellow]"
        )

    # 2. Walk album_path (pruning Video/ unless --with-video) and read with 8 threads
    files_to_warm = collect_warm_files(target_dir, with_video=with_video)
    if not files_to_warm:
        console.print(f"[yellow]No files found to warm in {target_dir}.[/yellow]")
        return

    total_bytes = 0
    with Progress(console=console) as progress:
        task_id = progress.add_task(
            f"Warming {len(files_to_warm)} files...", total=len(files_to_warm)
        )
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_read_file_to_devnull, f): f for f in files_to_warm}
            for fut in as_completed(futures):
                total_bytes += fut.result()
                progress.advance(task_id, 1)

    console.print(
        f"[bold green]Warmed {len(files_to_warm)} file(s)[/bold green] "
        f"({format_bytes(total_bytes)}) from [cyan]{target_dir}[/cyan]."
    )


@cache_app.command("evict")
def cache_evict(
    album_path: Annotated[
        Path,
        typer.Argument(help="Path to the album directory to evict from the local VFS cache."),
    ],
    rc_url: Annotated[
        str | None,
        typer.Option("--rc-url", help="Override rclone rc URL."),
    ] = None,
    archive_root: Annotated[
        Path | None,
        typer.Option("--archive-root", help="Override archive_root directory."),
    ] = None,
    cache_dir: Annotated[
        Path | None,
        typer.Option("--cache-dir", help="Override rclone_cache_dir."),
    ] = None,
    profiles_path: Annotated[
        Path | None,
        typer.Option("--config", help="Override path to profiles.yaml."),
    ] = None,
) -> None:
    """Safely evict an album from local rclone VFS cache after verifying no pending uploads."""
    profiles = load_profiles(profiles_path)
    effective_url = rc_url or profiles.rclone_rc_url
    root_dir = (archive_root or profiles.archive_root_path).expanduser()
    vfs_cache_root = (cache_dir or profiles.rclone_cache_path).expanduser()

    # 1. Upfront check: uploadsInProgress == 0 and uploadsQueued == 0
    try:
        stats = fetch_vfs_stats(effective_url)
    except httpx.HTTPError as exc:
        console.print(
            f"[red]Cannot verify upload queue status via {effective_url}/vfs/stats: {exc}[/red]"
        )
        raise typer.Exit(code=1) from exc

    in_progress, queued, _, _ = parse_stats_summary(stats)
    if in_progress > 0 or queued > 0:
        console.print(
            f"[bold red]Aborting eviction:[/bold red] rclone has pending uploads "
            f"(uploadsInProgress={in_progress}, uploadsQueued={queued})."
        )
        raise typer.Exit(code=1)

    rel_album = _compute_rel_album_path(album_path.expanduser(), root_dir)
    cached_album_dir = vfs_cache_root / rel_album

    if not cached_album_dir.exists():
        console.print(
            f"[yellow]No cached files found at {cached_album_dir} (already evicted).[/yellow]"
        )
        return

    removed_files = sum(1 for p in cached_album_dir.rglob("*") if p.is_file())
    shutil.rmtree(cached_album_dir)
    console.print(
        f"[bold green]Evicted {removed_files} cached file(s)[/bold green] "
        f"from [cyan]{cached_album_dir}[/cyan]."
    )

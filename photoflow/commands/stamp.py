"""Implementation of `photoflow stamp` for 3-layer metadata and film roll stamping."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Sequence

import typer
from rich.console import Console
from rich.prompt import Prompt

from photoflow.core.config import ProfilesConfig, load_profiles
from photoflow.core.digikam_db import update_digikam_album
from photoflow.core.xmp_sidecar import (
    IMAGE_EXTENSIONS,
    STAGE_COLOR_LABELS,
    build_album_hierarchical_tags,
    detect_stage,
    execute_exiftool_batches,
    extract_existing_tags,
    format_scan_exif_args,
    format_xmp_args,
    get_sidecar_path,
    is_raw_file,
    merge_hierarchical_tags,
    read_metadata_batch,
)
from photoflow.core.yaml_loader import (
    MEDIA_SUBFOLDERS,
    AlbumConfig,
    FilmRollConfig,
    load_effective_album_yaml,
    resolve_lens_for_frame,
)


console = Console()


def natural_sort_key(path: Path | str) -> list[int | str]:
    """Natural sort key for ordering scan files (01.jpg, 02.jpg, ..., 10.jpg, 36.jpg)."""
    name = Path(path).name.lower()
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def compute_frame_datetime(base_date_str: str | None, frame_number: int) -> str:
    """Compute EXIF DateTimeOriginal starting at 12:00:00 + (frame_number - 1) minutes."""
    year, month, day = 2026, 1, 1
    if base_date_str:
        cleaned = base_date_str.strip()
        m_full = re.match(r"^(\d{4})-(\d{2})-(\d{2})", cleaned)
        m_month = re.match(r"^(\d{4})-(\d{2})", cleaned)
        if m_full:
            year = int(m_full.group(1))
            month = max(1, min(12, int(m_full.group(2))))
            day = max(1, min(28, int(m_full.group(3))))
        elif m_month:
            year = int(m_month.group(1))
            m_val = int(m_month.group(2))
            month = max(1, min(12, m_val)) if m_val > 0 else 1
            day = 1

    base_dt = datetime(year, month, day, 12, 0, 0)
    frame_dt = base_dt + timedelta(minutes=max(0, frame_number - 1))
    return frame_dt.strftime("%Y:%m:%d %H:%M:%S")


def discover_media_files(paths: Sequence[Path]) -> list[Path]:
    """Expand directories or individual file selections (%F) into deduplicated media files."""
    discovered: list[Path] = []
    seen: set[Path] = set()

    def _add_file(p: Path) -> None:
        if p.suffix.lower() == ".xmp":
            companion = p.with_suffix("")
            if companion.is_file() and companion.suffix.lower() in IMAGE_EXTENSIONS:
                p = companion
            else:
                return
        if p.name == "album.yaml" or p.suffix.lower() not in IMAGE_EXTENSIONS:
            return
        resolved = p.resolve()
        if resolved not in seen:
            seen.add(resolved)
            discovered.append(p)

    for item in paths:
        p = item.expanduser()
        if p.is_file():
            _add_file(p)
        elif p.is_dir():
            # Check if this directory is an album root containing media subfolders
            has_media_subfolders = any((p / sub).is_dir() for sub in MEDIA_SUBFOLDERS)
            if has_media_subfolders:
                for sub in ("RAW", "Scans", "Edits", "Exports"):
                    sub_dir = p / sub
                    if sub_dir.is_dir():
                        for child in sorted(sub_dir.iterdir(), key=natural_sort_key):
                            if child.is_file():
                                _add_file(child)
            else:
                for child in sorted(p.rglob("*"), key=natural_sort_key):
                    if child.is_file() and "Video" not in child.parts:
                        _add_file(child)

    return discovered


def _select_film_roll(
    album: AlbumConfig, explicit_roll_id: str | None
) -> FilmRollConfig | None:
    rolls = album.gear.film
    if explicit_roll_id:
        for r in rolls:
            if r.roll_id.lower() == explicit_roll_id.strip().lower():
                return r
        raise ValueError(
            f"Roll ID '{explicit_roll_id}' not found in effective album.yaml "
            f"(available: {[r.roll_id for r in rolls]})."
        )

    if not rolls:
        return None
    if len(rolls) == 1:
        return rolls[0]

    choices = [r.roll_id for r in rolls]
    if (
        not sys.stdin.isatty()
        and "PYTEST_CURRENT_TEST" not in os.environ
        and shutil.which("kdialog")
    ):
        proc = subprocess.run(
            ["kdialog", "--combobox", "Select Film Roll for Scans:", *choices],
            capture_output=True,
            text=True,
            check=False,
        )
        chosen = proc.stdout.strip()
        if chosen:
            for r in rolls:
                if r.roll_id == chosen:
                    return r

    chosen_id = Prompt.ask(
        "Multiple film rolls found. Select roll_id",
        choices=choices,
        default=choices[0],
    )
    for r in rolls:
        if r.roll_id == chosen_id:
            return r
    return rolls[0]


def _pick_lens_interactively(
    roll: FilmRollConfig | None, profiles: ProfilesConfig
) -> str | None:
    candidates: list[str] = []
    if roll and roll.lenses:
        candidates.extend(roll.lenses)
    for k, v in profiles.lenses.items():
        if v not in candidates:
            candidates.append(f"{k} ({v})")
    if not candidates:
        return None

    if (
        not sys.stdin.isatty()
        and "PYTEST_CURRENT_TEST" not in os.environ
        and shutil.which("kdialog")
    ):
        proc = subprocess.run(
            ["kdialog", "--combobox", "Select Lens Override:", *candidates],
            capture_output=True,
            text=True,
            check=False,
        )
        chosen = proc.stdout.strip()
        if chosen:
            key_part = chosen.split(" (", 1)[0].strip()
            return profiles.resolve_lens(key_part)

    raw = Prompt.ask("Select lens key or name", default=candidates[0].split(" (", 1)[0])
    return profiles.resolve_lens(raw.strip())


def _extract_existing_frame_and_dt(
    img_meta: dict[str, Any], xmp_meta: dict[str, Any]
) -> tuple[int | None, str | None]:
    frame_val: Any = None
    for k in ("XMP-photoflow:FrameNumber", "FrameNumber"):
        if k in xmp_meta and xmp_meta[k] is not None:
            frame_val = xmp_meta[k]
            break
        if k in img_meta and img_meta[k] is not None:
            frame_val = img_meta[k]
            break

    frame_num: int | None = None
    if frame_val is not None:
        try:
            frame_num = int(str(frame_val).strip())
        except ValueError:
            frame_num = None

    dt_val: str | None = None
    for source in (img_meta, xmp_meta):
        for k in (
            "EXIF:DateTimeOriginal",
            "ExifIFD:DateTimeOriginal",
            "XMP-exif:DateTimeOriginal",
            "DateTimeOriginal",
            "Composite:DateTimeOriginal",
        ):
            if k in source and source[k]:
                dt_val = str(source[k]).strip()
                break
        if dt_val:
            break

    return frame_num, dt_val


def stamp_command(
    paths: Annotated[
        list[Path] | None,
        typer.Argument(help="File paths or album/subfolder directories to stamp."),
    ] = None,
    roll: Annotated[
        str | None,
        typer.Option("--roll", "-r", help="Film roll_id to stamp on Scans/."),
    ] = None,
    lens: Annotated[
        str | None,
        typer.Option("--lens", "-l", help="Explicit lens key or model name override."),
    ] = None,
    pick_lens: Annotated[
        bool,
        typer.Option("--pick-lens", help="Interactively pick a lens override (CLI or kdialog)."),
    ] = False,
    archive_root: Annotated[
        Path | None,
        typer.Option("--archive-root", help="Override archive_root directory from profiles.yaml."),
    ] = None,
    profiles_path: Annotated[
        Path | None,
        typer.Option("--config", help="Override path to profiles.yaml."),
    ] = None,
) -> None:
    """Stamp 3-layer EXIF and XMP sidecar metadata onto album files."""
    target_paths = paths if paths else [Path.cwd()]
    profiles = load_profiles(profiles_path)
    root_dir = (archive_root or profiles.archive_root_path).expanduser()

    first_target = target_paths[0].expanduser()
    try:
        album = load_effective_album_yaml(first_target)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Error loading album.yaml: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    if album.album_dir is not None:
        update_digikam_album(
            album,
            album.album_dir,
            root_dir,
            profiles.digikam_db_path,
        )

    files = discover_media_files(target_paths)
    if not files:
        console.print("[yellow]No media files found to stamp.[/yellow]")
        return

    # Group files by stage
    grouped: dict[str, list[Path]] = {"RAW": [], "Scans": [], "Edits": [], "Exports": []}
    for f in files:
        st = detect_stage(f)
        grouped.setdefault(st, []).append(f)

    # Pre-read existing metadata for all target files and their sidecars in one batch
    paths_to_read: list[Path] = []
    for f in files:
        sidecar = get_sidecar_path(f)
        if sidecar.is_file():
            paths_to_read.append(sidecar)
        if not is_raw_file(f) and f.is_file():
            paths_to_read.append(f)
    existing_meta = read_metadata_batch(paths_to_read)

    stamped_at = datetime.now(timezone.utc).isoformat()
    command_batches: list[list[str]] = []
    stamped_count = 0

    # Resolve optional explicit lens override
    resolved_lens_override: str | None = None
    if lens:
        resolved_lens_override = profiles.resolve_lens(lens.strip())

    # 1. Process Scans/
    scan_files = sorted(grouped.get("Scans", []), key=natural_sort_key)
    if scan_files:
        try:
            active_roll = _select_film_roll(album, roll)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc

        if pick_lens and not resolved_lens_override:
            resolved_lens_override = _pick_lens_interactively(active_roll, profiles)

        base_date = (
            (active_roll.date_shot if active_roll and active_roll.date_shot else None)
            or album.date_start
        )

        for idx, scan_file in enumerate(scan_files):
            sidecar = get_sidecar_path(scan_file)
            img_m = existing_meta.get(scan_file.resolve(), existing_meta.get(scan_file, {}))
            xmp_m = existing_meta.get(sidecar.resolve(), existing_meta.get(sidecar, {}))

            existing_frame, existing_dt = _extract_existing_frame_and_dt(img_m, xmp_m)
            is_partial_restamp = (existing_frame is not None) and (existing_dt is not None)

            if is_partial_restamp:
                assert existing_frame is not None
                assert existing_dt is not None
                frame_number = existing_frame
                dt_original = existing_dt
            else:
                frame_number = existing_frame if existing_frame is not None else (idx + 1)
                dt_original = existing_dt or compute_frame_datetime(base_date, frame_number)

            if resolved_lens_override:
                exif_lens: str | None = resolved_lens_override
                lens_tags = [f"Film/Lens/{resolved_lens_override}"]
            elif active_roll is not None:
                exif_lens, lens_tags = resolve_lens_for_frame(active_roll, frame_number)
            else:
                exif_lens, lens_tags = None, []

            # Layer 1 EXIF on Scan file:
            # Stamp full EXIF on initial stamp; on partial re-stamp, preserve DateTimeOriginal and
            # only update LensModel if resolved.
            if not is_raw_file(scan_file):
                if not is_partial_restamp:
                    command_batches.append(
                        format_scan_exif_args(
                            scan_file,
                            camera_make=active_roll.camera_make if active_roll else None,
                            camera_model=active_roll.camera_model if active_roll else None,
                            exif_lens=exif_lens,
                            iso_shot=active_roll.iso_shot if active_roll else None,
                            datetime_original=dt_original,
                        )
                    )
                elif exif_lens is not None:
                    command_batches.append(
                        format_scan_exif_args(
                            scan_file,
                            exif_lens=exif_lens,
                        )
                    )

            # Layer 2 & Layer 3 on .xmp sidecar
            existing_tags = extract_existing_tags(xmp_m) or extract_existing_tags(img_m)
            new_tags = build_album_hierarchical_tags(
                album,
                "Scans",
                roll=active_roll,
                lens_tags=lens_tags,
            )
            merged_tags = merge_hierarchical_tags(existing_tags, new_tags)

            command_batches.append(
                format_xmp_args(
                    sidecar,
                    color_label=STAGE_COLOR_LABELS["Scans"],
                    hierarchical_tags=merged_tags,
                    album_id=album.album_id,
                    roll_id=active_roll.roll_id if active_roll else None,
                    frame_number=frame_number,
                    stamped_at=stamped_at,
                )
            )
            stamped_count += 1

    # 2. Process RAW/, Edits/, Exports/
    for stage_name in ("RAW", "Edits", "Exports"):
        stage_files = sorted(grouped.get(stage_name, []), key=natural_sort_key)
        for media_file in stage_files:
            sidecar = get_sidecar_path(media_file)
            img_m = existing_meta.get(media_file.resolve(), existing_meta.get(media_file, {}))
            xmp_m = existing_meta.get(sidecar.resolve(), existing_meta.get(sidecar, {}))

            existing_tags = extract_existing_tags({**img_m, **xmp_m})
            if xmp_m and img_m:
                existing_tags = merge_hierarchical_tags(
                    extract_existing_tags(img_m),
                    extract_existing_tags(xmp_m),
                    replace_prefixes=(),
                )

            lens_tags_other = (
                [f"Film/Lens/{resolved_lens_override}"] if resolved_lens_override else []
            )
            new_tags = build_album_hierarchical_tags(
                album,
                stage_name,
                lens_tags=lens_tags_other,
                is_digital=(stage_name == "RAW" or is_raw_file(media_file)),
            )
            merged_tags = merge_hierarchical_tags(existing_tags, new_tags)
            color_label = STAGE_COLOR_LABELS[stage_name]

            if stage_name == "Exports" and not is_raw_file(media_file):
                command_batches.append(
                    format_xmp_args(
                        media_file,
                        color_label=color_label,
                        hierarchical_tags=merged_tags,
                        album_id=album.album_id,
                        stamped_at=stamped_at,
                    )
                )
                if sidecar.is_file():
                    command_batches.append(
                        format_xmp_args(
                            sidecar,
                            color_label=color_label,
                            hierarchical_tags=merged_tags,
                            album_id=album.album_id,
                            stamped_at=stamped_at,
                        )
                    )
            else:
                # Strictly write to <filename>.<ext>.xmp; NEVER modify binary RAW files
                command_batches.append(
                    format_xmp_args(
                        sidecar,
                        color_label=color_label,
                        hierarchical_tags=merged_tags,
                        album_id=album.album_id,
                        stamped_at=stamped_at,
                    )
                )
            stamped_count += 1

    execute_exiftool_batches(command_batches)
    console.print(
        f"[bold green]Stamped {stamped_count} file(s)[/bold green] "
        f"for album [cyan]{album.album_id or album.title}[/cyan]."
    )

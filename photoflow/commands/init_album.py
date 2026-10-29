"""Implementation of `photoflow init` for interactive/CLI album and sub-album creation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from photoflow.core.config import ProfilesConfig, load_profiles
from photoflow.core.digikam_db import update_digikam_album
from photoflow.core.yaml_loader import (
    MEDIA_SUBFOLDERS,
    SUB_ALBUM_PATTERN,
    AlbumConfig,
    DigitalGearConfig,
    FilmRollConfig,
    GearConfig,
    LensOverride,
    load_effective_album_yaml,
    save_album_yaml,
    validate_album_folder_name,
    validate_album_nesting,
)


console = Console()


def _sanitize_slug(name: str) -> str:
    """Convert spaces and invalid characters in an event name to underscores."""
    cleaned = re.sub(r"\s+", "_", name.strip())
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", cleaned)
    return cleaned


def parse_lens_overrides_input(
    raw_input: str, profiles: ProfilesConfig
) -> list[LensOverride]:
    """Parse an override string like `zuiko_28: 14-18, 22; zuiko_50: 1-5` into LensOverride objects."""
    text = raw_input.strip()
    if not text or text.lower() == "none":
        return []

    overrides: list[LensOverride] = []
    for entry in text.split(";"):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        lens_part, frames_part = entry.split(":", 1)
        resolved_lens = profiles.resolve_lens(lens_part.strip())
        frames: list[int | str] = []
        for token in frames_part.split(","):
            tok = token.strip()
            if not tok:
                continue
            if tok.isdigit():
                frames.append(int(tok))
            else:
                frames.append(tok)
        if frames:
            overrides.append(LensOverride(lens=resolved_lens, frames=frames))
    return overrides


def build_folder_name(
    mode: str,
    date_start: str,
    event_name: str,
    date_end: str | None = None,
) -> tuple[str, str]:
    """Compute `(year_str, folder_name)` from mode, dates, and event_name."""
    slug = _sanitize_slug(event_name)
    if not slug:
        raise ValueError("Event/album name cannot be empty.")

    # Check if the caller already passed a complete valid folder name
    try:
        validate_album_folder_name(slug, allow_subalbum=(mode == "subalbum"))
        if mode == "subalbum":
            year_str = date_start[:4] if len(date_start) >= 4 and date_start[:4].isdigit() else "2026"
            return year_str, slug
        return slug[:4], slug
    except ValueError:
        pass

    if mode == "subalbum":
        year_str = date_start[:4] if len(date_start) >= 4 and date_start[:4].isdigit() else "2026"
        if SUB_ALBUM_PATTERN.match(slug):
            folder_name = slug
        else:
            folder_name = f"01_{slug}"
        validate_album_folder_name(folder_name, allow_subalbum=True)
        return year_str, folder_name

    if mode == "parent":
        year_str = date_start[:4]
        start_month = date_start[5:7] if len(date_start) >= 7 else "01"
        end_month = (
            date_end[5:7]
            if date_end and len(date_end) >= 7
            else (date_end[:2] if date_end and len(date_end) == 2 else start_month)
        )
        folder_name = f"{year_str}-{start_month}--{end_month}_{slug}"
        validate_album_folder_name(folder_name, allow_subalbum=False)
        return year_str, folder_name

    # mode == "flat"
    year_str = date_start[:4]
    if date_start.endswith("-00"):
        folder_name = f"{year_str}-00_{slug}"
    elif "--" in date_start:
        folder_name = f"{date_start}_{slug}"
    else:
        folder_name = f"{date_start}_{slug}"
    validate_album_folder_name(folder_name, allow_subalbum=False)
    return year_str, folder_name


def init_command(
    mode: Annotated[
        str | None,
        typer.Option("--mode", "-m", help="Album mode: flat, parent, or subalbum."),
    ] = None,
    date_start: Annotated[
        str | None,
        typer.Option("--date", "--date-start", "-d", help="Start date (YYYY-MM-DD or YYYY-00)."),
    ] = None,
    date_end: Annotated[
        str | None,
        typer.Option("--date-end", help="End date (YYYY-MM-DD or YYYY-MM)."),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", "-n", help="Event or sub-album leg name."),
    ] = None,
    title: Annotated[
        str | None,
        typer.Option("--title", "-t", help="Human-readable album title."),
    ] = None,
    category: Annotated[
        str | None,
        typer.Option(
            "--category",
            "-c",
            help="Album category: Trip, Day_Outing, Event, Film_Roll, Shoot, Misc.",
        ),
    ] = None,
    locations: Annotated[
        str | None,
        typer.Option("--locations", "-l", help="Comma-separated hierarchical locations."),
    ] = None,
    parent_path: Annotated[
        Path | None,
        typer.Option("--parent", "-p", help="Parent album directory (for subalbum mode)."),
    ] = None,
    digital_cameras: Annotated[
        str | None,
        typer.Option("--digital", help="Comma-separated digital camera keys (or 'none')."),
    ] = None,
    digital_lenses: Annotated[
        str | None,
        typer.Option("--digital-lenses", help="Comma-separated lens keys/names for digital camera."),
    ] = None,
    notes: Annotated[
        str | None,
        typer.Option("--notes", help="Optional album notes."),
    ] = None,
    archive_root: Annotated[
        Path | None,
        typer.Option("--archive-root", help="Override archive_root directory from profiles.yaml."),
    ] = None,
    profiles_path: Annotated[
        Path | None,
        typer.Option("--config", help="Override path to profiles.yaml."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview directory structure and album.yaml without writing."),
    ] = False,
) -> None:
    """Initialize a new flat album, multi-month parent album, or sub-album."""
    profiles = load_profiles(profiles_path)
    root_dir = (archive_root or profiles.archive_root_path).expanduser()

    # 1. Mode selection
    resolved_mode = (mode or "").strip().lower()
    if not resolved_mode:
        resolved_mode = Prompt.ask(
            "Album Mode",
            choices=["flat", "parent", "subalbum"],
            default="flat",
        ).lower()
    if resolved_mode not in {"flat", "parent", "subalbum"}:
        console.print(f"[red]Invalid mode '{resolved_mode}'. Must be flat, parent, or subalbum.[/red]")
        raise typer.Exit(code=1)

    resolved_parent_dir: Path | None = None
    if resolved_mode == "subalbum":
        if parent_path is not None:
            resolved_parent_dir = parent_path.expanduser()
        else:
            raw_parent = Prompt.ask("Parent album folder path")
            p_cand = Path(raw_parent).expanduser()
            if not p_cand.is_absolute() and not p_cand.exists():
                p_cand = root_dir / p_cand
            resolved_parent_dir = p_cand

    # 2. Dates, Name, Category, Locations
    resolved_date_start = (date_start or "").strip()
    if not resolved_date_start:
        prompt_label = (
            "Start Date (YYYY-MM-DD or YYYY-MM)"
            if resolved_mode == "parent"
            else "Date (YYYY-MM-DD or YYYY-00)"
        )
        resolved_date_start = Prompt.ask(prompt_label, default="2026-05-14").strip()

    resolved_date_end = date_end.strip() if date_end is not None else None
    if resolved_mode == "parent" and not resolved_date_end:
        resolved_date_end = Prompt.ask(
            "End Date (YYYY-MM-DD or YYYY-MM)",
            default=resolved_date_start,
        ).strip()
    elif resolved_date_end is None:
        resolved_date_end = resolved_date_start

    resolved_name = (name or "").strip()
    if not resolved_name:
        name_prompt = (
            "Sub-album Leg Name (e.g. 01_China)"
            if resolved_mode == "subalbum"
            else "Event Name (e.g. LakeDistrict_Hike)"
        )
        resolved_name = Prompt.ask(name_prompt).strip()

    default_category = "Trip" if resolved_mode in {"parent", "subalbum"} else "Event"
    resolved_category = (category or "").strip()
    if not resolved_category:
        resolved_category = Prompt.ask(
            "Category (Trip/Day_Outing/Event/Film_Roll/Shoot/Misc)",
            default=default_category,
        ).strip()

    if locations is not None:
        raw_locations = locations
    else:
        raw_locations = Prompt.ask("Locations (comma-separated, e.g. Europe/UK/Lake District)", default="")
    location_list = [loc.strip() for loc in raw_locations.split(",") if loc.strip()]

    # 3. Decoupled Gear & Film Selector
    digital_gear_list: list[DigitalGearConfig] = []
    avail_digital = list(profiles.cameras.digital.keys())
    if digital_cameras is not None:
        raw_dig = digital_cameras
    else:
        dig_hint = f" ({', '.join(avail_digital)})" if avail_digital else ""
        raw_dig = Prompt.ask(
            f"Digital camera keys{dig_hint} (comma-separated, or 'none')",
            default="",
        )

    if raw_dig.strip() and raw_dig.strip().lower() != "none":
        for cam_key in [k.strip() for k in raw_dig.split(",") if k.strip()]:
            cam_prof = profiles.cameras.digital.get(cam_key)
            body_name = cam_prof.model if cam_prof else cam_key
            make_name = cam_prof.make if cam_prof else None

            if digital_lenses is not None:
                raw_dlenses = digital_lenses
            else:
                raw_dlenses = Prompt.ask(
                    f"Lenses for {body_name} (keys: {', '.join(profiles.lenses.keys())})",
                    default="",
                )
            chosen_lenses = [
                profiles.resolve_lens(lk.strip())
                for lk in raw_dlenses.split(",")
                if lk.strip() and lk.strip().lower() != "none"
            ]
            digital_gear_list.append(
                DigitalGearConfig(body=body_name, lenses=chosen_lenses, make=make_name)
            )

    film_rolls_list: list[FilmRollConfig] = []
    # Only prompt for film rolls interactively if digital_cameras was not explicitly passed as CLI-only non-interactive flag,
    # or always allow interactive prompt until blank roll_id
    if digital_cameras is None:
        avail_film_cams = ", ".join(profiles.cameras.film.keys())
        avail_lenses = ", ".join(profiles.lenses.keys())
        avail_stocks = ", ".join(profiles.film_stocks.keys())
        avail_labs = ", ".join(profiles.labs.keys())

        while True:
            roll_id = Prompt.ask(
                "Add Film Roll ID (e.g. R045, or press Enter to finish)",
                default="",
            ).strip()
            if not roll_id or roll_id.lower() == "none":
                break

            default_cam_key = next(iter(profiles.cameras.film.keys()), "om1")
            cam_key = Prompt.ask(
                f"Film camera key ({avail_film_cams})",
                default=default_cam_key,
            ).strip()
            fcam = profiles.cameras.film.get(cam_key)
            cam_make = fcam.make if fcam else cam_key
            cam_model = fcam.model if fcam else cam_key
            cam_format = fcam.format if fcam else "35mm"

            default_flens = next(iter(profiles.lenses.keys()), "")
            raw_flenses = Prompt.ask(
                f"Film lens key(s) ({avail_lenses})",
                default=default_flens,
            ).strip()
            roll_lens_keys = [k.strip() for k in raw_flenses.split(",") if k.strip()]
            roll_lenses = [profiles.resolve_lens(k) for k in roll_lens_keys]

            default_lens_val: str | None = None
            overrides_list: list[LensOverride] = []
            if len(roll_lenses) == 1:
                default_lens_val = roll_lenses[0]
            elif len(roll_lenses) > 1:
                raw_def = Prompt.ask(
                    f"Default lens for roll {roll_id} ({', '.join(roll_lens_keys)} or 'none')",
                    default=roll_lens_keys[0],
                ).strip()
                if raw_def and raw_def.lower() != "none":
                    default_lens_val = profiles.resolve_lens(raw_def)

                raw_ov = Prompt.ask(
                    "Frame lens overrides (e.g. 'zuiko_28: 14-18, 22' or blank)",
                    default="",
                ).strip()
                if raw_ov:
                    overrides_list = parse_lens_overrides_input(raw_ov, profiles)

            default_stock_key = next(iter(profiles.film_stocks.keys()), "gold200")
            stock_key = Prompt.ask(
                f"Film stock key ({avail_stocks})",
                default=default_stock_key,
            ).strip()
            stock_prof = profiles.film_stocks.get(stock_key)
            stock_brand = stock_prof.brand if stock_prof else "Kodak"
            stock_name = stock_prof.name if stock_prof else stock_key
            film_type = stock_prof.type if stock_prof else "Color Negative"
            iso_shot = stock_prof.iso if stock_prof else 200

            process_val = Prompt.ask(
                "Process (Standard / Pushed +1 / Pushed +2 / Pulled -1)",
                default="Standard",
            ).strip()

            default_lab_key = next(iter(profiles.labs.keys()), "")
            lab_key = Prompt.ask(
                f"Processing lab key ({avail_labs})",
                default=default_lab_key,
            ).strip()
            lab_val = profiles.resolve_lab(lab_key) if lab_key else ""

            film_rolls_list.append(
                FilmRollConfig(
                    roll_id=roll_id,
                    camera_make=cam_make,
                    camera_model=cam_model,
                    lenses=roll_lenses,
                    default_lens=default_lens_val,
                    lens_overrides=overrides_list,
                    stock_brand=stock_brand,
                    stock_name=stock_name,
                    format=cam_format,
                    film_type=film_type,
                    iso_shot=iso_shot,
                    process=process_val,
                    lab=lab_val,
                    date_shot=resolved_date_start,
                )
            )

    # 4. Build directory path and validate naming + nesting depth
    try:
        year_str, folder_name = build_folder_name(
            resolved_mode,
            resolved_date_start,
            resolved_name,
            resolved_date_end,
        )
    except ValueError as exc:
        console.print(f"[red]Validation error: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    if resolved_mode == "subalbum":
        assert resolved_parent_dir is not None
        album_dir = resolved_parent_dir / folder_name
        parent_album_ref = "../album.yaml"
        album_id_str = f"{resolved_parent_dir.name}-{folder_name}".lower().replace("_", "-")
    else:
        album_dir = root_dir / year_str / folder_name
        parent_album_ref = None
        album_id_str = folder_name.lower().replace("_", "-")

    try:
        nesting_depth = validate_album_nesting(
            album_dir,
            archive_root=root_dir if album_dir.is_relative_to(root_dir) else None,
            validate_names=True,
        )
    except ValueError as exc:
        console.print(f"[red]Nesting validation error: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    resolved_title = title or resolved_name.replace("_", " ").strip()
    album_cfg = AlbumConfig(
        album_id=album_id_str,
        parent_album=parent_album_ref,
        title=resolved_title,
        category=resolved_category,
        date_start=resolved_date_start,
        date_end=resolved_date_end,
        status="staging",
        locations=location_list,
        album_tags=[],
        gear=GearConfig(digital=digital_gear_list, film=film_rolls_list),
        notes=notes or "",
        folder_name=folder_name,
        nesting_depth=nesting_depth,
        album_dir=album_dir,
    )

    subfolders_to_create = [] if resolved_mode == "parent" else sorted(MEDIA_SUBFOLDERS)

    if dry_run:
        console.print(
            Panel(
                f"[bold green]Dry Run:[/bold green] Would create album at [cyan]{album_dir}[/cyan]\n"
                f"Subfolders: {', '.join(subfolders_to_create) if subfolders_to_create else '(none - parent album)'}",
                title="photoflow init (dry-run)",
            )
        )
        return

    album_dir.mkdir(parents=True, exist_ok=True)
    for sub in subfolders_to_create:
        (album_dir / sub).mkdir(parents=True, exist_ok=True)

    yaml_file = save_album_yaml(album_cfg, album_dir / "album.yaml")

    # Load effective config (merging parent if subalbum) before updating digikam4.db
    try:
        effective_cfg = load_effective_album_yaml(yaml_file)
    except Exception:
        effective_cfg = album_cfg

    update_digikam_album(
        effective_cfg,
        album_dir,
        root_dir,
        profiles.digikam_db_path,
    )

    console.print(
        Panel(
            f"Created [bold green]{resolved_mode}[/bold green] album at [cyan]{album_dir}[/cyan]\n"
            f"Metadata saved to [cyan]{yaml_file}[/cyan]",
            title="photoflow init",
        )
    )

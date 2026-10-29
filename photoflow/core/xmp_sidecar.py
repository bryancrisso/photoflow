"""ExifTool batch wrapper for 3-layer metadata writing and non-destructive XMP sidecars."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from photoflow.core.yaml_loader import AlbumConfig, FilmRollConfig, resolve_lens_for_frame


RAW_EXTENSIONS = frozenset(
    {".cr3", ".cr2", ".nef", ".arw", ".dng", ".orf", ".raf", ".rw2", ".pef"}
)
IMAGE_EXTENSIONS = RAW_EXTENSIONS | frozenset(
    {".jpg", ".jpeg", ".tif", ".tiff", ".png", ".webp", ".heic"}
)

STAGE_COLOR_LABELS: dict[str, int] = {
    "RAW": 0,
    "Scans": 5,
    "Edits": 3,
    "Exports": 4,
}

STAGE_TAGS: dict[str, str] = {
    "RAW": "Stage/RAW",
    "Scans": "Stage/Scan",
    "Edits": "Stage/Edit",
    "Exports": "Stage/Export",
}

REPO_EXIFTOOL_CONFIG = Path(__file__).resolve().parents[2] / ".ExifTool_config"
HOME_EXIFTOOL_CONFIG = Path("~/.ExifTool_config").expanduser()


def get_exiftool_config_path() -> Path | None:
    """Locate the .ExifTool_config file defining the XMP-photoflow namespace."""
    if HOME_EXIFTOOL_CONFIG.is_file():
        return HOME_EXIFTOOL_CONFIG
    if REPO_EXIFTOOL_CONFIG.is_file():
        return REPO_EXIFTOOL_CONFIG
    return None


def is_raw_file(file_path: Path | str) -> bool:
    """Return True if the file has a binary RAW camera extension (e.g. .CR3)."""
    return Path(file_path).suffix.lower() in RAW_EXTENSIONS


def get_sidecar_path(file_path: Path | str) -> Path:
    """Return the `<filename>.<ext>.xmp` sidecar path for a media file."""
    p = Path(file_path)
    if p.suffix.lower() == ".xmp":
        return p
    return p.with_name(f"{p.name}.xmp")


def detect_stage(file_path: Path | str) -> str:
    """Determine the workflow stage ('RAW', 'Scans', 'Edits', 'Exports') from path."""
    p = Path(file_path)
    lookup = {k.lower(): k for k in STAGE_COLOR_LABELS}
    for part in reversed(p.parts[:-1]):
        canonical = lookup.get(part.lower())
        if canonical:
            return canonical
    if is_raw_file(p):
        return "RAW"
    return "Scans"


def _normalize_tag_list(raw_val: Any) -> list[str]:
    if raw_val is None:
        return []
    if isinstance(raw_val, list):
        return [str(x) for x in raw_val if str(x).strip()]
    if isinstance(raw_val, (str, int, float)):
        text = str(raw_val).strip()
        return [text] if text else []
    return []


def extract_existing_tags(metadata: dict[str, Any]) -> list[str]:
    """Extract existing hierarchical tags from an ExifTool JSON metadata dict."""
    tags: list[str] = []
    for key in (
        "XMP-digiKam:TagsList",
        "TagsList",
        "XMP-lr:HierarchicalSubject",
        "XMP-lr:hierarchicalSubject",
        "HierarchicalSubject",
        "hierarchicalSubject",
    ):
        if key in metadata:
            tags.extend(_normalize_tag_list(metadata[key]))
    seen: set[str] = set()
    deduped: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped


def merge_hierarchical_tags(
    existing_tags: Sequence[str],
    new_tags: Sequence[str],
    *,
    replace_prefixes: tuple[str, ...] = ("Film/Lens/", "Stage/"),
) -> list[str]:
    """Merge new hierarchical tags with existing tags while preserving user tags.

    Replaces any existing tag starting with a prefix in `replace_prefixes` if `new_tags`
    provides updated tags for that prefix (or always for `Film/Lens/` when re-stamping lenses).
    """
    active_replace_prefixes = tuple(
        prefix
        for prefix in replace_prefixes
        if any(nt.startswith(prefix) for nt in new_tags)
    )
    filtered_existing = [
        tag
        for tag in existing_tags
        if not (active_replace_prefixes and tag.startswith(active_replace_prefixes))
    ]

    merged: list[str] = []
    seen: set[str] = set()
    for tag in [*filtered_existing, *new_tags]:
        cleaned = tag.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            merged.append(cleaned)
    return merged


def build_album_hierarchical_tags(
    album: AlbumConfig,
    stage: str,
    *,
    roll: FilmRollConfig | None = None,
    lens_tags: Sequence[str] = (),
    is_digital: bool = False,
) -> list[str]:
    """Build Layer 2 hierarchical XMP tags from album and roll configuration."""
    tags: list[str] = []

    stage_tag = STAGE_TAGS.get(stage)
    if stage_tag:
        tags.append(stage_tag)

    if roll is not None:
        tags.append(f"Medium/Film/{roll.format}")
        tags.append(f"Film/Roll/{roll.roll_id}")
        tags.append(f"Film/Camera/{roll.camera_model}")
        tags.append(f"Film/Stock/{roll.stock_brand}/{roll.stock_name}")
        tags.append(f"Film/Type/{roll.film_type}")
        tags.append(f"Film/Process/{roll.process}")
        if roll.lab:
            tags.append(f"Film/Lab/{roll.lab}")
        tags.extend(lens_tags)
    elif is_digital or stage == "RAW":
        tags.append("Medium/Digital")

    for loc in album.locations:
        loc_clean = loc.strip().strip("/")
        if not loc_clean:
            continue
        if loc_clean.startswith("Location/"):
            tags.append(loc_clean)
        else:
            tags.append(f"Location/{loc_clean}")

    for album_tag in album.album_tags:
        cleaned = album_tag.strip().strip("/")
        if cleaned:
            tags.append(cleaned)

    return tags


def format_scan_exif_args(
    scan_path: Path | str,
    *,
    camera_make: str | None = None,
    camera_model: str | None = None,
    exif_lens: str | None = None,
    iso_shot: int | None = None,
    datetime_original: str | None = None,
) -> list[str]:
    """Format ExifTool CLI arguments for stamping Layer 1 EXIF into a scan file."""
    target = Path(scan_path)
    if is_raw_file(target):
        raise ValueError(f"Refusing to build EXIF write args for RAW file: {target}")

    args: list[str] = ["-overwrite_original"]
    if camera_make is not None:
        args.append(f"-EXIF:Make={camera_make}")
    if camera_model is not None:
        args.append(f"-EXIF:Model={camera_model}")
    if exif_lens is not None:
        args.append(f"-EXIF:LensModel={exif_lens}")
    if iso_shot is not None:
        args.append(f"-EXIF:ISO={iso_shot}")
    if datetime_original is not None:
        args.append(f"-EXIF:DateTimeOriginal={datetime_original}")
    args.append(str(target))
    return args


def format_xmp_args(
    target_path: Path | str,
    *,
    color_label: int,
    hierarchical_tags: Sequence[str],
    album_id: str | None = None,
    roll_id: str | None = None,
    frame_number: int | str | None = None,
    stamped_at: str | None = None,
) -> list[str]:
    """Format ExifTool CLI arguments for writing Layer 2 & Layer 3 tags to an XMP sidecar or Export JPEG."""
    target = Path(target_path)
    if is_raw_file(target):
        raise ValueError(f"Refusing to build XMP write args directly for RAW file: {target}")

    ts = stamped_at or datetime.now(timezone.utc).isoformat()
    args: list[str] = [
        "-overwrite_original",
        f"-XMP-digiKam:ColorLabel={color_label}",
    ]

    if hierarchical_tags:
        for tag in hierarchical_tags:
            args.append(f"-XMP-digiKam:TagsList={tag}")
        for tag in hierarchical_tags:
            args.append(f"-XMP-lr:hierarchicalSubject={tag}")

    if album_id is not None:
        args.append(f"-XMP-photoflow:AlbumID={album_id}")
    if roll_id is not None:
        args.append(f"-XMP-photoflow:RollID={roll_id}")
    if frame_number is not None:
        args.append(f"-XMP-photoflow:FrameNumber={frame_number}")
    args.append(f"-XMP-photoflow:StampedAt={ts}")
    args.append(str(target))
    return args


def build_exiftool_args(
    file_path: Path | str,
    album: AlbumConfig,
    *,
    stage: str | None = None,
    roll: FilmRollConfig | None = None,
    frame_number: int | None = None,
    datetime_original: str | None = None,
    lens_override: str | None = None,
    existing_tags: Sequence[str] = (),
    stamped_at: str | None = None,
) -> dict[str, list[str]]:
    """Build ExifTool command arguments for a media file according to stage rules.

    Returns a dictionary with optional keys:
    - `'image_args'`: Layer 1 EXIF args for `Scans/` or direct tags for `Exports/`
    - `'sidecar_args'`: Layer 2 & 3 XMP sidecar args for `<file>.<ext>.xmp`
    """
    p = Path(file_path)
    effective_stage = stage or detect_stage(p)
    color_label = STAGE_COLOR_LABELS.get(effective_stage, 0)
    sidecar = get_sidecar_path(p)

    exif_lens: str | None = None
    lens_tags: list[str] = []
    if lens_override:
        exif_lens = lens_override
        lens_tags = [f"Film/Lens/{lens_override}"]
    elif roll is not None and frame_number is not None:
        exif_lens, lens_tags = resolve_lens_for_frame(roll, frame_number)
    elif roll is not None:
        exif_lens, lens_tags = resolve_lens_for_frame(roll, 1)

    new_tags = build_album_hierarchical_tags(
        album,
        effective_stage,
        roll=roll,
        lens_tags=lens_tags,
        is_digital=(effective_stage == "RAW" or is_raw_file(p)),
    )
    merged_tags = merge_hierarchical_tags(existing_tags, new_tags)

    result: dict[str, list[str]] = {}

    if effective_stage == "Scans" and not is_raw_file(p):
        if roll is not None or exif_lens is not None or datetime_original is not None:
            result["image_args"] = format_scan_exif_args(
                p,
                camera_make=roll.camera_make if roll else None,
                camera_model=roll.camera_model if roll else None,
                exif_lens=exif_lens,
                iso_shot=roll.iso_shot if roll else None,
                datetime_original=datetime_original,
            )
        result["sidecar_args"] = format_xmp_args(
            sidecar,
            color_label=color_label,
            hierarchical_tags=merged_tags,
            album_id=album.album_id,
            roll_id=roll.roll_id if roll else None,
            frame_number=frame_number,
            stamped_at=stamped_at,
        )
    elif effective_stage == "Exports" and not is_raw_file(p):
        result["image_args"] = format_xmp_args(
            p,
            color_label=color_label,
            hierarchical_tags=merged_tags,
            album_id=album.album_id,
            roll_id=roll.roll_id if roll else None,
            frame_number=frame_number,
            stamped_at=stamped_at,
        )
        if sidecar.exists():
            result["sidecar_args"] = format_xmp_args(
                sidecar,
                color_label=color_label,
                hierarchical_tags=merged_tags,
                album_id=album.album_id,
                roll_id=roll.roll_id if roll else None,
                frame_number=frame_number,
                stamped_at=stamped_at,
            )
    else:
        # RAW/ or Edits/ (or any RAW file): strictly write ONLY to <filename>.<ext>.xmp
        result["sidecar_args"] = format_xmp_args(
            sidecar,
            color_label=color_label,
            hierarchical_tags=merged_tags,
            album_id=album.album_id,
            roll_id=roll.roll_id if roll else None,
            frame_number=frame_number,
            stamped_at=stamped_at,
        )

    return result


def read_metadata_batch(paths: Sequence[Path | str]) -> dict[Path, dict[str, Any]]:
    """Read metadata for existing files in a single ExifTool invocation."""
    existing = [Path(p) for p in paths if Path(p).is_file()]
    if not existing:
        return {}

    cmd = ["exiftool"]
    cfg = get_exiftool_config_path()
    if cfg is not None:
        cmd.extend(["-config", str(cfg)])
    cmd.extend(["-j", "-G1", *[str(p) for p in existing]])

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if not proc.stdout.strip():
        return {}

    try:
        items = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}

    by_path: dict[Path, dict[str, Any]] = {}
    for item in items:
        src = item.get("SourceFile")
        if src:
            normalized = dict(item)
            for k, val in list(item.items()):
                if ":" in k:
                    grp, tag_name = k.split(":", 1)
                    if grp in ("IFD0", "ExifIFD", "InteropIFD"):
                        normalized.setdefault(f"EXIF:{tag_name}", val)
                    normalized.setdefault(tag_name, val)
            by_path[Path(src)] = normalized
            by_path[Path(src).resolve()] = normalized
    return by_path


def execute_exiftool_batches(command_batches: Sequence[Sequence[str]]) -> None:
    """Execute one or more ExifTool argument batches in a single `exiftool -@ -` process."""
    if not command_batches:
        return

    lines: list[str] = []
    for batch in command_batches:
        if not batch:
            continue
        target_str = batch[-1]
        if is_raw_file(target_str):
            raise RuntimeError(f"Safety violation: refusing to write to binary RAW file '{target_str}'")
        lines.extend(batch)
        lines.append("-execute")

    if not lines:
        return

    cmd = ["exiftool"]
    cfg = get_exiftool_config_path()
    if cfg is not None:
        cmd.extend(["-config", str(cfg)])
    cmd.extend(["-@", "-"])

    payload = "\n".join(lines) + "\n"
    proc = subprocess.run(cmd, input=payload, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ExifTool batch execution failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )


@dataclass
class StampOperation:
    """Represents a resolved stamp operation for a single file."""

    file_path: Path
    stage: str
    sidecar_path: Path
    frame_number: int | None = None
    datetime_original: str | None = None
    exif_lens: str | None = None
    preserved_existing_frame: bool = False
    command_batches: list[list[str]] = field(default_factory=list)

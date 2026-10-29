"""Pydantic models for album.yaml, naming/nesting validation, multi-lens resolution, and YAML inheritance."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, Sequence

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SINGLE_DAY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}_[A-Za-z0-9_-]+$")
MULTI_MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}--\d{2}_[A-Za-z0-9_-]+$")
SEASONAL_PATTERN = re.compile(r"^\d{4}-00_[A-Za-z0-9_-]+$")
SUB_ALBUM_PATTERN = re.compile(r"^\d{2}_[A-Za-z0-9_-]+$")
YEAR_DIR_PATTERN = re.compile(r"^\d{4}$")
FRAME_RANGE_PATTERN = re.compile(r"^(\d+)-(\d+)$")

MEDIA_SUBFOLDERS = frozenset({"RAW", "Scans", "Edits", "Exports", "Video"})
MAX_NESTING_LEVELS = 2

AlbumCategory = Literal["Trip", "Day_Outing", "Event", "Film_Roll", "Shoot", "Misc"]
AlbumStatus = Literal["staging", "culling", "editing", "archived"]


def classify_album_folder_name(folder_name: str) -> str:
    """Classify an album folder name according to prefix rules or raise ValueError."""
    if SINGLE_DAY_PATTERN.match(folder_name):
        return "single_day"
    if MULTI_MONTH_PATTERN.match(folder_name):
        return "multi_month"
    if SEASONAL_PATTERN.match(folder_name):
        return "seasonal"
    if SUB_ALBUM_PATTERN.match(folder_name):
        return "subalbum"
    raise ValueError(
        f"Invalid album folder name '{folder_name}'. Must match single-day "
        r"(^\d{4}-\d{2}-\d{2}_[A-Za-z0-9_-]+$), multi-month "
        r"(^\d{4}-\d{2}--\d{2}_[A-Za-z0-9_-]+$), seasonal "
        r"(^\d{4}-00_[A-Za-z0-9_-]+$), or sub-album leg (^\d{2}_[A-Za-z0-9_-]+$)."
    )


def validate_album_folder_name(folder_name: str, *, allow_subalbum: bool = True) -> str:
    """Validate an album directory name against the allowed naming regexes."""
    kind = classify_album_folder_name(folder_name)
    if kind == "subalbum" and not allow_subalbum:
        raise ValueError(
            f"Sub-album folder name '{folder_name}' cannot be used as a top-level album under YYYY/."
        )
    return folder_name


def validate_album_nesting(
    album_path: Path | str,
    archive_root: Path | str | None = None,
    *,
    validate_names: bool = True,
) -> int:
    """Validate that an album path does not exceed 2 physical nesting levels below YYYY/.

    Returns the album nesting depth (1 or 2).
    """
    path = Path(album_path).expanduser()
    parts = list(path.parts)

    # Strip trailing media subfolder if path points to e.g. .../Scans
    if parts and parts[-1] in MEDIA_SUBFOLDERS:
        parts = parts[:-1]

    rel_parts: list[str] | None = None
    if archive_root is not None:
        root = Path(archive_root).expanduser()
        try:
            rel = Path(*parts).relative_to(root)
            rel_parts = list(rel.parts)
        except ValueError:
            try:
                rel = Path(*parts).resolve().relative_to(root.resolve())
                rel_parts = list(rel.parts)
            except ValueError:
                rel_parts = None

    if rel_parts is not None:
        if rel_parts and YEAR_DIR_PATTERN.match(rel_parts[0]):
            album_parts = rel_parts[1:]
        else:
            album_parts = rel_parts
    else:
        # Locate YYYY directory component in path
        year_idx: int | None = None
        for idx, part in enumerate(parts):
            if YEAR_DIR_PATTERN.match(part):
                year_idx = idx
                break
        if year_idx is not None:
            album_parts = parts[year_idx + 1 :]
        else:
            album_parts = [parts[-1]] if parts else []

    depth = len(album_parts)
    if depth == 0:
        raise ValueError(f"Path '{album_path}' does not contain an album directory below YYYY/.")
    if depth > MAX_NESTING_LEVELS:
        raise ValueError(
            f"Album nesting depth {depth} exceeds maximum of {MAX_NESTING_LEVELS} levels below YYYY/: "
            f"{'/'.join(album_parts)}"
        )

    if validate_names:
        validate_album_folder_name(album_parts[0], allow_subalbum=False)
        if depth == 2:
            validate_album_folder_name(album_parts[1], allow_subalbum=True)

    return depth


def expand_frame_spec(frames: Sequence[int | str]) -> set[int]:
    """Expand a list of frame numbers or 'start-end' range strings into a set of ints."""
    expanded: set[int] = set()
    for item in frames:
        if isinstance(item, int) and not isinstance(item, bool):
            if item < 1:
                raise ValueError(f"Frame number must be >= 1, got {item}")
            expanded.add(item)
        elif isinstance(item, str):
            text = item.strip()
            if text.isdigit():
                num = int(text)
                if num < 1:
                    raise ValueError(f"Frame number must be >= 1, got {item}")
                expanded.add(num)
            else:
                match = FRAME_RANGE_PATTERN.match(text)
                if not match:
                    raise ValueError(
                        f"Invalid frame specification '{item}'. Expected int or 'start-end' range."
                    )
                start, end = int(match.group(1)), int(match.group(2))
                if start < 1 or start > end:
                    raise ValueError(
                        f"Invalid frame range '{item}': start must be >= 1 and <= end."
                    )
                expanded.update(range(start, end + 1))
        else:
            raise ValueError(f"Unsupported frame specification type: {type(item).__name__}")
    return expanded


class DigitalGearConfig(BaseModel):
    """Digital camera body and associated lenses for an album."""

    body: str
    lenses: list[str] = Field(default_factory=list)
    make: str | None = None


class LensOverride(BaseModel):
    """Per-frame lens assignment for a film roll."""

    lens: str
    frames: list[int | str] = Field(default_factory=list)

    @field_validator("frames")
    @classmethod
    def _validate_frames(cls, v: list[int | str]) -> list[int | str]:
        expand_frame_spec(v)
        return v

    @property
    def expanded_frames(self) -> set[int]:
        """Set of integer frame numbers covered by this override."""
        return expand_frame_spec(self.frames)


class FilmRollConfig(BaseModel):
    """Film roll metadata and multi-lens configuration."""

    roll_id: str
    camera_make: str
    camera_model: str
    lenses: list[str] = Field(default_factory=list)
    default_lens: str | None = None
    lens_overrides: list[LensOverride] = Field(default_factory=list)
    stock_brand: str
    stock_name: str
    format: str = "35mm"
    film_type: str = "Color Negative"
    iso_shot: int = 200
    process: str = "Standard"
    lab: str = ""
    date_shot: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _support_single_lens_field(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            if "lens" in data and not data.get("lenses"):
                single_lens = data.pop("lens")
                if single_lens:
                    data["lenses"] = [single_lens]
            else:
                data.pop("lens", None)
        return data

    @model_validator(mode="after")
    def _auto_set_default_lens(self) -> FilmRollConfig:
        if len(self.lenses) == 1 and self.default_lens is None:
            self.default_lens = self.lenses[0]
        return self


class GearConfig(BaseModel):
    """Container for digital and film gear in album.yaml."""

    digital: list[DigitalGearConfig] = Field(default_factory=list)
    film: list[FilmRollConfig] = Field(default_factory=list)


class AlbumConfig(BaseModel):
    """Pydantic model for album.yaml supporting parent/child inheritance."""

    model_config = ConfigDict(validate_assignment=True)

    album_id: str | None = None
    parent_album: str | None = None
    title: str | None = None
    category: str | None = "Event"
    date_start: str | None = None
    date_end: str | None = None
    status: str | None = "staging"
    locations: list[str] = Field(default_factory=list)
    album_tags: list[str] = Field(default_factory=list)
    gear: GearConfig = Field(default_factory=GearConfig)
    notes: str | None = None

    # Optional validation fields for folder naming & nesting depth
    folder_name: str | None = Field(default=None, exclude=True)
    nesting_depth: int | None = Field(default=None, exclude=True)
    album_dir: Path | None = Field(default=None, exclude=True)

    @field_validator("folder_name")
    @classmethod
    def _validate_folder_name_field(cls, v: str | None) -> str | None:
        if v is not None:
            validate_album_folder_name(v, allow_subalbum=True)
        return v

    @field_validator("nesting_depth")
    @classmethod
    def _validate_nesting_depth_field(cls, v: int | None) -> int | None:
        if v is not None and (v < 1 or v > MAX_NESTING_LEVELS):
            raise ValueError(
                f"Album nesting depth {v} exceeds maximum of {MAX_NESTING_LEVELS} levels."
            )
        return v

    def to_yaml_dict(self) -> dict[str, Any]:
        """Serialize AlbumConfig to a clean dictionary for writing to album.yaml."""
        data: dict[str, Any] = {
            "album_id": self.album_id,
            "parent_album": self.parent_album,
            "title": self.title,
            "category": self.category,
            "date_start": self.date_start,
            "date_end": self.date_end,
            "status": self.status,
            "locations": list(self.locations),
            "album_tags": list(self.album_tags),
            "gear": {
                "digital": [
                    {
                        "body": d.body,
                        "lenses": list(d.lenses),
                        **({"make": d.make} if d.make else {}),
                    }
                    for d in self.gear.digital
                ],
                "film": [
                    {
                        "roll_id": f.roll_id,
                        "camera_make": f.camera_make,
                        "camera_model": f.camera_model,
                        "lenses": list(f.lenses),
                        "default_lens": f.default_lens,
                        **(
                            {
                                "lens_overrides": [
                                    {"lens": ov.lens, "frames": list(ov.frames)}
                                    for ov in f.lens_overrides
                                ]
                            }
                            if f.lens_overrides
                            else {}
                        ),
                        "stock_brand": f.stock_brand,
                        "stock_name": f.stock_name,
                        "format": f.format,
                        "film_type": f.film_type,
                        "iso_shot": f.iso_shot,
                        "process": f.process,
                        "lab": f.lab,
                        **({"date_shot": f.date_shot} if f.date_shot else {}),
                    }
                    for f in self.gear.film
                ],
            },
            "notes": self.notes or "",
        }
        return data


def resolve_lens_for_frame(
    roll: FilmRollConfig, frame_number: int
) -> tuple[str | None, list[str]]:
    """Resolve the EXIF lens model and XMP hierarchical lens tags for a film frame.

    Returns `(exif_lens, xmp_lens_tags)`:
    1. Match in `lens_overrides`: `(override.lens, [f"Film/Lens/{override.lens}"])`
    2. No override match, but `default_lens` is set (or `len(roll.lenses) == 1`):
       `(default_lens, [f"Film/Lens/{default_lens}"])`
    3. Multiple `lenses` listed, no `default_lens` and no override for this frame:
       `(None, [f"Film/Lens/{l}" for l in roll.lenses])`
    """
    for override in roll.lens_overrides:
        if frame_number in override.expanded_frames:
            return override.lens, [f"Film/Lens/{override.lens}"]

    effective_default = roll.default_lens
    if not effective_default and len(roll.lenses) == 1:
        effective_default = roll.lenses[0]

    if effective_default:
        return effective_default, [f"Film/Lens/{effective_default}"]

    if roll.lenses:
        return None, [f"Film/Lens/{lens}" for lens in roll.lenses]

    return None, []


def _dedupe_list(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def merge_album_configs(parent: AlbumConfig, child: AlbumConfig) -> AlbumConfig:
    """Merge a child AlbumConfig on top of a parent AlbumConfig."""
    scalar_fields = (
        "album_id",
        "title",
        "category",
        "date_start",
        "date_end",
        "status",
        "notes",
    )
    merged_scalars: dict[str, Any] = {}
    for field_name in scalar_fields:
        child_val = getattr(child, field_name)
        parent_val = getattr(parent, field_name)
        merged_scalars[field_name] = child_val if child_val is not None else parent_val

    locations = _dedupe_list([*parent.locations, *child.locations])
    album_tags = _dedupe_list([*parent.album_tags, *child.album_tags])

    # Merge digital gear (preserve parent + child, deduplicating identical body/lenses)
    digital_by_body: dict[str, DigitalGearConfig] = {}
    digital_order: list[str] = []
    for item in [*parent.gear.digital, *child.gear.digital]:
        if item.body not in digital_by_body:
            digital_order.append(item.body)
            digital_by_body[item.body] = DigitalGearConfig(
                body=item.body,
                lenses=list(item.lenses),
                make=item.make,
            )
        else:
            existing = digital_by_body[item.body]
            merged_lenses = _dedupe_list([*existing.lenses, *item.lenses])
            digital_by_body[item.body] = DigitalGearConfig(
                body=item.body,
                lenses=merged_lenses,
                make=item.make or existing.make,
            )
    merged_digital = [digital_by_body[b] for b in digital_order]

    # Merge film rolls: concatenate parent and child; child roll_id overrides parent roll_id
    film_by_id: dict[str, FilmRollConfig] = {}
    film_order: list[str] = []
    for roll in parent.gear.film:
        film_order.append(roll.roll_id)
        film_by_id[roll.roll_id] = roll.model_copy(deep=True)
    for roll in child.gear.film:
        if roll.roll_id not in film_by_id:
            film_order.append(roll.roll_id)
        film_by_id[roll.roll_id] = roll.model_copy(deep=True)
    merged_film = [film_by_id[rid] for rid in film_order]

    return AlbumConfig(
        **merged_scalars,
        parent_album=child.parent_album,
        locations=locations,
        album_tags=album_tags,
        gear=GearConfig(digital=merged_digital, film=merged_film),
        album_dir=child.album_dir or parent.album_dir,
    )


def find_album_yaml(start_path: Path | str) -> Path:
    """Walk upward from `start_path` to find the nearest `album.yaml`."""
    path = Path(start_path).expanduser().resolve()
    if path.is_file():
        if path.name == "album.yaml":
            return path
        current = path.parent
    else:
        current = path

    while True:
        candidate = current / "album.yaml"
        if candidate.is_file():
            return candidate
        if current.parent == current:
            break
        current = current.parent

    raise FileNotFoundError(f"No album.yaml found in '{start_path}' or any parent directory.")


def _load_raw_album_yaml(yaml_path: Path) -> AlbumConfig:
    with yaml_path.open("r", encoding="utf-8") as fh:
        raw_data = yaml.safe_load(fh) or {}

    if not isinstance(raw_data, dict):
        raise ValueError(f"Expected mapping in {yaml_path}, got {type(raw_data).__name__}")

    # Preserve None for scalar fields not explicitly specified in YAML so inheritance works cleanly
    if "category" not in raw_data:
        raw_data["category"] = None
    if "status" not in raw_data:
        raw_data["status"] = None

    cfg = AlbumConfig.model_validate(raw_data)
    cfg.album_dir = yaml_path.parent
    return cfg


def load_effective_album_yaml(
    path: Path | str,
    *,
    archive_root: Path | str | None = None,
) -> AlbumConfig:
    """Walk upward from `path` to find `album.yaml`, validate nesting, and merge parent inheritance."""
    yaml_path = find_album_yaml(path)
    album_dir = yaml_path.parent

    # If album_dir is under a YYYY/ directory (or archive_root is provided), enforce max 2-level nesting
    has_year_ancestor = any(YEAR_DIR_PATTERN.match(p) for p in album_dir.parts)
    if archive_root is not None or has_year_ancestor:
        validate_album_nesting(album_dir, archive_root=archive_root, validate_names=has_year_ancestor)

    chain: list[AlbumConfig] = []
    visited: set[Path] = set()
    curr_yaml = yaml_path

    while True:
        resolved_yaml = curr_yaml.resolve()
        if resolved_yaml in visited:
            raise ValueError(f"Circular parent_album reference detected at {resolved_yaml}")
        visited.add(resolved_yaml)

        cfg = _load_raw_album_yaml(resolved_yaml)
        chain.append(cfg)

        if len(chain) > MAX_NESTING_LEVELS:
            raise ValueError(
                f"Album inheritance depth ({len(chain)}) exceeds maximum of {MAX_NESTING_LEVELS} levels."
            )

        if not cfg.parent_album:
            break

        parent_yaml = (resolved_yaml.parent / cfg.parent_album).resolve()
        if not parent_yaml.is_file():
            raise FileNotFoundError(
                f"Parent album.yaml referenced by {resolved_yaml} not found at {parent_yaml}"
            )
        curr_yaml = parent_yaml

    # Merge from root parent down to leaf child
    chain.reverse()
    effective = chain[0]
    for child_cfg in chain[1:]:
        effective = merge_album_configs(effective, child_cfg)

    # Apply defaults if root parent omitted category or status
    if effective.category is None:
        effective.category = "Event"
    if effective.status is None:
        effective.status = "staging"
    effective.album_dir = album_dir
    return effective


def save_album_yaml(album: AlbumConfig, yaml_path: Path | str) -> Path:
    """Write an AlbumConfig to `album.yaml` on disk."""
    target = Path(yaml_path).expanduser()
    if target.is_dir():
        target = target / "album.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            album.to_yaml_dict(),
            fh,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
    return target

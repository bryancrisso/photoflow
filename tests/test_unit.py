"""Unit tests for yaml_loader, config, digikam_db, and xmp_sidecar."""

from __future__ import annotations

from pathlib import Path

import pytest

from photoflow.core.config import load_profiles
from photoflow.core.digikam_db import compute_relative_path, format_album_caption
from photoflow.core.xmp_sidecar import (
    STAGE_COLOR_LABELS,
    build_exiftool_args,
    format_scan_exif_args,
    format_xmp_args,
    get_sidecar_path,
    merge_hierarchical_tags,
)
from photoflow.core.yaml_loader import (
    AlbumConfig,
    DigitalGearConfig,
    FilmRollConfig,
    GearConfig,
    LensOverride,
    expand_frame_spec,
    load_effective_album_yaml,
    resolve_lens_for_frame,
    save_album_yaml,
    validate_album_folder_name,
    validate_album_nesting,
)


# ---------------------------------------------------------------------------
# 1. Album Naming Regex & Nesting Depth Tests (core/yaml_loader.py)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "folder_name",
    [
        "2026-05-14_LakeDistrict_Hike",
        "2026-07--09_Asia_Trip",
        "2026-00_Summer_Portra400",
        "01_China",
        "02_Japan-Tokyo_Leg",
    ],
)
def test_valid_album_folder_names(folder_name: str) -> None:
    assert validate_album_folder_name(folder_name, allow_subalbum=True) == folder_name


@pytest.mark.parametrize(
    "invalid_name",
    [
        "LakeDistrict_Hike",
        "2026-5-4_BadDate",
        "2026-07---09_Trip",
        "2026-05-14_",
        "1_China",
        "2026-05-14_Spaces Not Allowed",
    ],
)
def test_invalid_album_folder_names(invalid_name: str) -> None:
    with pytest.raises(ValueError):
        validate_album_folder_name(invalid_name, allow_subalbum=True)


def test_subalbum_disallowed_at_top_level() -> None:
    with pytest.raises(ValueError):
        validate_album_folder_name("01_China", allow_subalbum=False)


def test_max_two_level_nesting_below_year(tmp_path: Path) -> None:
    archive_root = tmp_path / "Archive"
    level1 = archive_root / "2026" / "2026-07--09_Asia_Trip"
    level2 = level1 / "01_China"
    level3 = level2 / "01_Beijing"

    assert validate_album_nesting(level1, archive_root=archive_root) == 1
    assert validate_album_nesting(level2, archive_root=archive_root) == 2
    # Media subfolder inside level 2 is still album depth 2
    assert validate_album_nesting(level2 / "Scans", archive_root=archive_root) == 2

    with pytest.raises(ValueError, match="exceeds maximum of 2 levels"):
        validate_album_nesting(level3, archive_root=archive_root)


# ---------------------------------------------------------------------------
# 2. Multi-Lens Frame Range Parsing & Lens Resolution (core/yaml_loader.py)
# ---------------------------------------------------------------------------


def test_expand_frame_spec_int_and_ranges() -> None:
    frames = expand_frame_spec(["14-18", 22, "30"])
    assert frames == {14, 15, 16, 17, 18, 22, 30}


def test_expand_frame_spec_invalid_ranges() -> None:
    with pytest.raises(ValueError):
        expand_frame_spec(["18-14"])
    with pytest.raises(ValueError):
        expand_frame_spec(["0-5"])
    with pytest.raises(ValueError):
        expand_frame_spec(["invalid"])


def test_resolve_lens_for_frame_with_overrides_and_default() -> None:
    roll = FilmRollConfig(
        roll_id="R045",
        camera_make="Olympus",
        camera_model="Olympus OM-1",
        lenses=["Zuiko Auto-S 50mm f/1.8", "Zuiko Auto-W 28mm f/2.8"],
        default_lens="Zuiko Auto-S 50mm f/1.8",
        lens_overrides=[
            LensOverride(lens="Zuiko Auto-W 28mm f/2.8", frames=["14-18", 22]),
        ],
        stock_brand="Kodak",
        stock_name="Gold 200",
    )

    # Frame in range 14-18 -> override lens
    exif_lens, tags = resolve_lens_for_frame(roll, 16)
    assert exif_lens == "Zuiko Auto-W 28mm f/2.8"
    assert tags == ["Film/Lens/Zuiko Auto-W 28mm f/2.8"]

    # Frame 22 -> override lens
    exif_lens_22, tags_22 = resolve_lens_for_frame(roll, 22)
    assert exif_lens_22 == "Zuiko Auto-W 28mm f/2.8"
    assert tags_22 == ["Film/Lens/Zuiko Auto-W 28mm f/2.8"]

    # Frame 5 -> falls back to default_lens
    exif_lens_5, tags_5 = resolve_lens_for_frame(roll, 5)
    assert exif_lens_5 == "Zuiko Auto-S 50mm f/1.8"
    assert tags_5 == ["Film/Lens/Zuiko Auto-S 50mm f/1.8"]


def test_resolve_lens_single_lens_auto_default() -> None:
    roll = FilmRollConfig(
        roll_id="R046",
        camera_make="Minolta",
        camera_model="Minolta X-700",
        lenses=["MD 50mm f/1.7"],
        stock_brand="Ilford",
        stock_name="HP5 Plus",
    )
    assert roll.default_lens == "MD 50mm f/1.7"
    exif_lens, tags = resolve_lens_for_frame(roll, 1)
    assert exif_lens == "MD 50mm f/1.7"
    assert tags == ["Film/Lens/MD 50mm f/1.7"]


def test_resolve_lens_multiple_lenses_no_default() -> None:
    roll = FilmRollConfig(
        roll_id="R047",
        camera_make="Olympus",
        camera_model="Olympus OM-1",
        lenses=["Zuiko Auto-S 50mm f/1.8", "Zuiko Auto-W 28mm f/2.8"],
        default_lens=None,
        lens_overrides=[
            LensOverride(lens="Zuiko Auto-W 28mm f/2.8", frames=[10]),
        ],
        stock_brand="Kodak",
        stock_name="Portra 400",
    )
    # Frame 10 matches explicit override
    assert resolve_lens_for_frame(roll, 10) == (
        "Zuiko Auto-W 28mm f/2.8",
        ["Film/Lens/Zuiko Auto-W 28mm f/2.8"],
    )
    # Frame 1 has no override and no default_lens -> EXIF is None, all candidate lenses in XMP
    exif_lens, tags = resolve_lens_for_frame(roll, 1)
    assert exif_lens is None
    assert tags == [
        "Film/Lens/Zuiko Auto-S 50mm f/1.8",
        "Film/Lens/Zuiko Auto-W 28mm f/2.8",
    ]


# ---------------------------------------------------------------------------
# 3. Upward Walk & Parent/Child YAML Inheritance (core/yaml_loader.py)
# ---------------------------------------------------------------------------


def test_upward_walk_and_parent_child_inheritance(tmp_path: Path) -> None:
    year_dir = tmp_path / "2026"
    parent_dir = year_dir / "2026-07--09_Asia_Trip"
    child_dir = parent_dir / "01_China"
    scans_dir = child_dir / "Scans"
    scans_dir.mkdir(parents=True)

    parent_cfg = AlbumConfig(
        album_id="2026-07-asia-trip",
        title="Summer Asia Trip",
        category="Trip",
        date_start="2026-07-01",
        date_end="2026-09-15",
        status="editing",
        locations=["Asia"],
        album_tags=["Event/Vacation", "Collection/Summer2026"],
        gear=GearConfig(
            digital=[
                DigitalGearConfig(
                    body="Canon EOS R10",
                    lenses=["RF-S 18-150mm F3.5-6.3 IS STM"],
                )
            ],
            film=[
                FilmRollConfig(
                    roll_id="R044",
                    camera_make="Minolta",
                    camera_model="Minolta X-700",
                    lenses=["MD 50mm f/1.7"],
                    stock_brand="Ilford",
                    stock_name="HP5 Plus",
                ),
                FilmRollConfig(
                    roll_id="R045",
                    camera_make="Olympus",
                    camera_model="Olympus OM-1",
                    lenses=["Zuiko Auto-S 50mm f/1.8"],
                    stock_brand="Kodak",
                    stock_name="Gold 200",
                    notes="Parent version",
                ),
            ],
        ),
        notes="Trip-wide notes.",
    )
    save_album_yaml(parent_cfg, parent_dir / "album.yaml")

    # Write child album.yaml overriding title, date_end, R045, and adding locations/tags
    child_yaml_text = """
album_id: "2026-07-asia-trip-01-china"
parent_album: "../album.yaml"
title: "China Leg - Summer Asia Trip"
date_start: "2026-07-01"
date_end: "2026-07-14"
locations:
  - "Asia/China/Beijing"
  - "Asia/China/Shanghai"
album_tags:
  - "Event/Vacation"
  - "Subject/Street"
gear:
  digital: []
  film:
    - roll_id: "R045"
      camera_make: "Olympus"
      camera_model: "Olympus OM-1"
      lenses:
        - "Zuiko Auto-S 50mm f/1.8"
        - "Zuiko Auto-W 28mm f/2.8"
      default_lens: "Zuiko Auto-S 50mm f/1.8"
      stock_brand: "Kodak"
      stock_name: "Gold 200"
      iso_shot: 200
      process: "Pushed +1"
      lab: "Darkroom8"
"""
    (child_dir / "album.yaml").write_text(child_yaml_text.strip(), encoding="utf-8")

    # Load starting from deep inside Scans/ subfolder
    effective = load_effective_album_yaml(scans_dir)

    assert effective.album_id == "2026-07-asia-trip-01-china"
    assert effective.title == "China Leg - Summer Asia Trip"
    assert effective.category == "Trip"  # Inherited from parent
    assert effective.status == "editing"  # Inherited from parent
    assert effective.date_end == "2026-07-14"  # Overridden by child
    assert effective.notes == "Trip-wide notes."  # Inherited from parent
    assert effective.locations == [
        "Asia",
        "Asia/China/Beijing",
        "Asia/China/Shanghai",
    ]
    assert effective.album_tags == [
        "Event/Vacation",
        "Collection/Summer2026",
        "Subject/Street",
    ]
    assert len(effective.gear.digital) == 1
    assert effective.gear.digital[0].body == "Canon EOS R10"

    # R044 preserved from parent, R045 overridden by child
    assert [r.roll_id for r in effective.gear.film] == ["R044", "R045"]
    r045 = effective.gear.film[1]
    assert r045.process == "Pushed +1"
    assert len(r045.lenses) == 2


# ---------------------------------------------------------------------------
# 4. Decoupled Profiles Inventory Loader (core/config.py)
# ---------------------------------------------------------------------------


def test_load_profiles_inventory(tmp_path: Path) -> None:
    cfg_file = tmp_path / "profiles.yaml"
    cfg_file.write_text(
        """
archive_root: "~/PhotoWorkspace/Archive"
digikam_db: "~/PhotoWorkspace/digikam_db/digikam4.db"
rclone_cache_dir: "~/PhotoWorkspace/.rclone_cache/vfs/gdrive/PhotoArchive"
rclone_rc_url: "http://127.0.0.1:5572"
cameras:
  digital:
    r10:
      make: "Canon"
      model: "Canon EOS R10"
      mount: "RF"
  film:
    om1:
      make: "Olympus"
      model: "Olympus OM-1"
      format: "35mm"
      mount: "OM"
lenses:
  zuiko_50: "Zuiko Auto-S 50mm f/1.8"
  zuiko_28: "Zuiko Auto-W 28mm f/2.8"
film_stocks:
  gold200:
    brand: "Kodak"
    name: "Gold 200"
    type: "Color Negative"
    iso: 200
labs:
  malaysia: "Darkroom8"
""".strip(),
        encoding="utf-8",
    )
    prof = load_profiles(cfg_file)
    assert prof.cameras.digital["r10"].model == "Canon EOS R10"
    assert prof.cameras.film["om1"].model == "Olympus OM-1"
    assert prof.resolve_lens("zuiko_50") == "Zuiko Auto-S 50mm f/1.8"
    assert prof.resolve_lens("Custom Lens 35mm") == "Custom Lens 35mm"
    assert prof.film_stocks["gold200"].iso == 200
    assert prof.resolve_lab("malaysia") == "Darkroom8"


# ---------------------------------------------------------------------------
# 5. digiKam DB Relative Path & Caption Formatting (core/digikam_db.py)
# ---------------------------------------------------------------------------


def test_compute_relative_path(tmp_path: Path) -> None:
    root = tmp_path / "Archive"
    album_dir = root / "2026" / "2026-05-14_LakeDistrict_Hike"
    subalbum_dir = root / "2026" / "2026-07--09_Asia_Trip" / "01_China"

    rel1 = compute_relative_path(album_dir, root)
    assert rel1 == "/2026/2026-05-14_LakeDistrict_Hike"
    assert rel1.startswith("/") and not rel1.endswith("/")

    rel2 = compute_relative_path(subalbum_dir, root)
    assert rel2 == "/2026/2026-07--09_Asia_Trip/01_China"


def test_format_album_caption() -> None:
    album = AlbumConfig(
        album_id="2026-05-14-lakedistrict-hike",
        title="Lake District Hike",
        notes="Sunny ridge walk around Helvellyn.",
        gear=GearConfig(
            digital=[
                DigitalGearConfig(
                    body="Canon EOS R10",
                    lenses=["RF-S 18-150mm F3.5-6.3 IS STM"],
                )
            ],
            film=[
                FilmRollConfig(
                    roll_id="R045",
                    camera_make="Olympus",
                    camera_model="Olympus OM-1",
                    lenses=["Zuiko Auto-S 50mm f/1.8"],
                    stock_brand="Kodak",
                    stock_name="Gold 200",
                    iso_shot=200,
                    process="Standard",
                )
            ],
        ),
    )
    caption = format_album_caption(album)
    assert "Lake District Hike" in caption
    assert "Sunny ridge walk around Helvellyn." in caption
    assert "Canon EOS R10 (RF-S 18-150mm F3.5-6.3 IS STM)" in caption
    assert "Film [R045]: Olympus OM-1 | Kodak Gold 200 (ISO 200, Standard)" in caption


# ---------------------------------------------------------------------------
# 6. XMP Sidecar Path & ExifTool Argument Formatting (core/xmp_sidecar.py)
# ---------------------------------------------------------------------------


def test_sidecar_path_generation() -> None:
    assert get_sidecar_path(Path("/photos/RAW/IMG_001.CR3")) == Path(
        "/photos/RAW/IMG_001.CR3.xmp"
    )
    assert get_sidecar_path(Path("/photos/Scans/01.jpg")) == Path(
        "/photos/Scans/01.jpg.xmp"
    )
    assert get_sidecar_path(Path("/photos/RAW/IMG_001.CR3.xmp")) == Path(
        "/photos/RAW/IMG_001.CR3.xmp"
    )


def test_color_label_mapping_and_exiftool_args() -> None:
    assert STAGE_COLOR_LABELS == {"RAW": 0, "Scans": 5, "Edits": 3, "Exports": 4}

    # Verify RAW file never produces image_args and refuses direct EXIF write
    with pytest.raises(ValueError, match="RAW file"):
        format_scan_exif_args(Path("/album/RAW/IMG_001.CR3"), camera_make="Canon")

    with pytest.raises(ValueError, match="RAW file"):
        format_xmp_args(
            Path("/album/RAW/IMG_001.CR3"),
            color_label=0,
            hierarchical_tags=["Stage/RAW"],
        )

    album = AlbumConfig(
        album_id="2026-05-14-hike",
        title="Hike",
        locations=["Europe/UK/Lake District"],
        album_tags=["Event/Outdoors"],
    )
    roll = FilmRollConfig(
        roll_id="R045",
        camera_make="Olympus",
        camera_model="Olympus OM-1",
        lenses=["Zuiko Auto-S 50mm f/1.8"],
        stock_brand="Kodak",
        stock_name="Gold 200",
        iso_shot=200,
    )

    # 1. RAW file args -> strictly sidecar_args only, ColorLabel=0
    raw_built = build_exiftool_args(
        Path("/album/RAW/IMG_001.CR3"),
        album,
        stamped_at="2026-05-14T12:00:00+00:00",
    )
    assert "image_args" not in raw_built
    assert raw_built["sidecar_args"][-1] == "/album/RAW/IMG_001.CR3.xmp"
    assert "-XMP-digiKam:ColorLabel=0" in raw_built["sidecar_args"]
    assert "-XMP-digiKam:TagsList=Stage/RAW" in raw_built["sidecar_args"]
    assert "-XMP-lr:hierarchicalSubject=Stage/RAW" in raw_built["sidecar_args"]

    # 2. Scan file args -> image_args (Layer 1 EXIF) + sidecar_args (Layer 2 & 3), ColorLabel=5
    scan_built = build_exiftool_args(
        Path("/album/Scans/01.jpg"),
        album,
        roll=roll,
        frame_number=1,
        datetime_original="2026:05:14 12:00:00",
        existing_tags=["People/Alice", "Film/Lens/OldLens"],
        stamped_at="2026-05-14T12:00:00+00:00",
    )
    img_args = scan_built["image_args"]
    side_args = scan_built["sidecar_args"]

    assert img_args[-1] == "/album/Scans/01.jpg"
    assert "-EXIF:Make=Olympus" in img_args
    assert "-EXIF:Model=Olympus OM-1" in img_args
    assert "-EXIF:LensModel=Zuiko Auto-S 50mm f/1.8" in img_args
    assert "-EXIF:ISO=200" in img_args
    assert "-EXIF:DateTimeOriginal=2026:05:14 12:00:00" in img_args

    assert side_args[-1] == "/album/Scans/01.jpg.xmp"
    assert "-XMP-digiKam:ColorLabel=5" in side_args
    assert "-XMP-digiKam:TagsList=People/Alice" in side_args
    assert "-XMP-digiKam:TagsList=Film/Lens/Zuiko Auto-S 50mm f/1.8" in side_args
    assert "-XMP-digiKam:TagsList=Film/Lens/OldLens" not in side_args
    assert "-XMP-photoflow:AlbumID=2026-05-14-hike" in side_args
    assert "-XMP-photoflow:RollID=R045" in side_args
    assert "-XMP-photoflow:FrameNumber=1" in side_args


def test_merge_hierarchical_tags_preserves_user_tags() -> None:
    existing = [
        "People/Family/Bob",
        "Collection/Portfolio",
        "Film/Lens/Zuiko Auto-S 50mm f/1.8",
        "Stage/Scan",
    ]
    new_tags = [
        "Stage/Scan",
        "Film/Lens/Zuiko Auto-W 28mm f/2.8",
        "Location/Asia/China",
    ]
    merged = merge_hierarchical_tags(existing, new_tags)
    assert "People/Family/Bob" in merged
    assert "Collection/Portfolio" in merged
    assert "Film/Lens/Zuiko Auto-W 28mm f/2.8" in merged
    assert "Film/Lens/Zuiko Auto-S 50mm f/1.8" not in merged
    assert "Location/Asia/China" in merged

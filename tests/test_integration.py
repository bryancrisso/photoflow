"""Integration tests for real ExifTool sidecar/EXIF stamping and digiKam SQLite database updates."""

from __future__ import annotations

import base64
import hashlib
import sqlite3
from pathlib import Path

from photoflow.commands.stamp import stamp_command
from photoflow.core.digikam_db import update_digikam_album
from photoflow.core.xmp_sidecar import (
    execute_exiftool_batches,
    extract_existing_tags,
    format_xmp_args,
    get_sidecar_path,
    read_metadata_batch,
)
from photoflow.core.yaml_loader import (
    AlbumConfig,
    DigitalGearConfig,
    FilmRollConfig,
    GearConfig,
    LensOverride,
    save_album_yaml,
)


# Minimal valid 1x1 white JPEG bytes
MINIMAL_JPEG_BYTES = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA="
)


def _create_dummy_jpeg(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(MINIMAL_JPEG_BYTES)
    return path


def _create_dummy_cr3(path: Path) -> tuple[Path, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b"FAKE_CR3_BINARY_RAW_HEADER_DO_NOT_MODIFY_1234567890"
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    return path, digest


def test_exiftool_integration_raw_scans_exports_and_tag_merging(tmp_path: Path) -> None:
    archive_root = tmp_path / "Archive"
    album_dir = archive_root / "2026" / "2026-05-14_LakeDistrict_Hike"
    for sub in ("RAW", "Scans", "Edits", "Exports"):
        (album_dir / sub).mkdir(parents=True, exist_ok=True)

    album = AlbumConfig(
        album_id="2026-05-14-lakedistrict-hike",
        title="Lake District Hike",
        category="Day_Outing",
        date_start="2026-05-14",
        date_end="2026-05-14",
        status="editing",
        locations=["Europe/UK/Lake District"],
        album_tags=["Event/Hiking"],
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
                    lenses=["Zuiko Auto-S 50mm f/1.8", "Zuiko Auto-W 28mm f/2.8"],
                    default_lens="Zuiko Auto-S 50mm f/1.8",
                    lens_overrides=[
                        LensOverride(lens="Zuiko Auto-W 28mm f/2.8", frames=[2]),
                    ],
                    stock_brand="Kodak",
                    stock_name="Gold 200",
                    format="35mm",
                    film_type="Color Negative",
                    iso_shot=200,
                    process="Standard",
                    lab="AG Photo Lab",
                    date_shot="2026-05-14",
                )
            ],
        ),
        notes="Great light on Striding Edge.",
    )
    save_album_yaml(album, album_dir / "album.yaml")

    # 1. Create dummy RAW (.CR3), Scans (.jpg), and Exports (.jpg)
    cr3_file, cr3_sha256_before = _create_dummy_cr3(album_dir / "RAW" / "IMG_001.CR3")
    scan1 = _create_dummy_jpeg(album_dir / "Scans" / "01.jpg")
    scan2 = _create_dummy_jpeg(album_dir / "Scans" / "02.jpg")
    export1 = _create_dummy_jpeg(album_dir / "Exports" / "IMG_001_export.jpg")

    # Pre-seed a manual user tag (`People/Alice`) in scan1's sidecar to test safe tag merging
    scan1_xmp = get_sidecar_path(scan1)
    execute_exiftool_batches(
        [
            format_xmp_args(
                scan1_xmp,
                color_label=5,
                hierarchical_tags=["People/Alice", "Collection/Favorites"],
            )
        ]
    )

    # 2. Run stamp_command on the album directory
    stamp_command(
        paths=[album_dir],
        roll=None,
        lens=None,
        pick_lens=False,
        archive_root=archive_root,
        profiles_path=None,
    )

    # 3. Verify .CR3 binary was NEVER modified and IMG_001.CR3.xmp was created
    cr3_sha256_after = hashlib.sha256(cr3_file.read_bytes()).hexdigest()
    assert cr3_sha256_after == cr3_sha256_before
    cr3_xmp = get_sidecar_path(cr3_file)
    assert cr3_xmp.is_file()

    # 4. Read back all metadata using real ExifTool
    scan2_xmp = get_sidecar_path(scan2)
    meta = read_metadata_batch([cr3_xmp, scan1, scan1_xmp, scan2, scan2_xmp, export1])

    # Check RAW sidecar
    raw_m = meta[cr3_xmp.resolve()]
    assert int(raw_m["XMP-digiKam:ColorLabel"]) == 0
    assert raw_m["XMP-photoflow:AlbumID"] == "2026-05-14-lakedistrict-hike"
    raw_tags = extract_existing_tags(raw_m)
    assert "Stage/RAW" in raw_tags
    assert "Medium/Digital" in raw_tags
    assert "Location/Europe/UK/Lake District" in raw_tags

    # Check Scan 1 (default lens: Zuiko Auto-S 50mm f/1.8, Frame 1, preserved People/Alice)
    s1_img = meta[scan1.resolve()]
    s1_xmp = meta[scan1_xmp.resolve()]
    assert s1_img.get("EXIF:Make") == "Olympus"
    assert s1_img.get("EXIF:Model") == "Olympus OM-1"
    assert s1_img.get("EXIF:LensModel") == "Zuiko Auto-S 50mm f/1.8"
    assert int(s1_img.get("EXIF:ISO")) == 200
    assert s1_img.get("EXIF:DateTimeOriginal") == "2026:05:14 12:00:00"

    assert int(s1_xmp["XMP-digiKam:ColorLabel"]) == 5
    assert s1_xmp["XMP-photoflow:AlbumID"] == "2026-05-14-lakedistrict-hike"
    assert s1_xmp["XMP-photoflow:RollID"] == "R045"
    assert str(s1_xmp["XMP-photoflow:FrameNumber"]) == "1"
    s1_tags = extract_existing_tags(s1_xmp)
    assert "People/Alice" in s1_tags
    assert "Collection/Favorites" in s1_tags
    assert "Stage/Scan" in s1_tags
    assert "Film/Roll/R045" in s1_tags
    assert "Film/Lens/Zuiko Auto-S 50mm f/1.8" in s1_tags

    # Check Scan 2 (lens override on frame 2: Zuiko Auto-W 28mm f/2.8, timestamp 12:01:00)
    s2_img = meta[scan2.resolve()]
    s2_xmp = meta[scan2_xmp.resolve()]
    assert s2_img.get("EXIF:LensModel") == "Zuiko Auto-W 28mm f/2.8"
    assert s2_img.get("EXIF:DateTimeOriginal") == "2026:05:14 12:01:00"
    assert str(s2_xmp["XMP-photoflow:FrameNumber"]) == "2"
    s2_tags = extract_existing_tags(s2_xmp)
    assert "Film/Lens/Zuiko Auto-W 28mm f/2.8" in s2_tags

    # Check Export 1 (tags baked directly into JPEG, ColorLabel=4)
    exp_m = meta[export1.resolve()]
    assert int(exp_m["XMP-digiKam:ColorLabel"]) == 4
    assert exp_m["XMP-photoflow:AlbumID"] == "2026-05-14-lakedistrict-hike"
    exp_tags = extract_existing_tags(exp_m)
    assert "Stage/Export" in exp_tags


def test_sqlite_digikam_db_integration(tmp_path: Path) -> None:
    archive_root = tmp_path / "Archive"
    album_dir = archive_root / "2026" / "2026-05-14_LakeDistrict_Hike"
    album_dir.mkdir(parents=True)

    db_path = tmp_path / "digikam4.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE Albums (
                id INTEGER PRIMARY KEY,
                albumRoot INTEGER NOT NULL,
                relativePath TEXT NOT NULL,
                date DATE,
                caption TEXT,
                collection TEXT,
                icon INTEGER,
                modificationDate DATETIME,
                UNIQUE(albumRoot, relativePath)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO Albums (id, albumRoot, relativePath, date, caption, collection)
            VALUES (1, 1, '/2026/2026-05-14_LakeDistrict_Hike', NULL, NULL, NULL);
            """
        )
        conn.commit()

    album = AlbumConfig(
        album_id="2026-05-14-lakedistrict-hike",
        title="Lake District Hike",
        category="Day_Outing",
        date_start="2026-05-14",
        notes="Ridge scramble in clear weather.",
        gear=GearConfig(
            digital=[
                DigitalGearConfig(
                    body="Canon EOS R10",
                    lenses=["RF 50mm F1.8 STM"],
                )
            ]
        ),
    )

    # 1. Update existing indexed album row
    updated = update_digikam_album(album, album_dir, archive_root, db_path, quiet=True)
    assert updated is True

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT date, caption, collection FROM Albums WHERE relativePath = ?",
            ("/2026/2026-05-14_LakeDistrict_Hike",),
        ).fetchone()

    assert row is not None
    assert row[0] == "2026-05-14"
    assert "Lake District Hike" in row[1]
    assert "Ridge scramble in clear weather." in row[1]
    assert "Canon EOS R10 (RF 50mm F1.8 STM)" in row[1]
    assert row[2] == "Day_Outing"

    # 2. Unindexed folder returns False gracefully without raising an exception
    unindexed_dir = archive_root / "2026" / "2026-06-01_Unindexed_Album"
    unindexed_dir.mkdir(parents=True)
    not_updated = update_digikam_album(album, unindexed_dir, archive_root, db_path, quiet=True)
    assert not_updated is False

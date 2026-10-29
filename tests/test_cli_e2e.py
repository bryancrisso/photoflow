"""End-to-end CLI tests using typer.testing.CliRunner."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from photoflow.cli import app
from photoflow.core.xmp_sidecar import extract_existing_tags, get_sidecar_path, read_metadata_batch
from photoflow.core.yaml_loader import load_effective_album_yaml


runner = CliRunner()

MINIMAL_JPEG_BYTES = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA="
)


def _write_test_profiles(tmp_path: Path) -> Path:
    profiles_file = tmp_path / "profiles.yaml"
    archive_dir = tmp_path / "Archive"
    cache_dir = tmp_path / "rclone_cache"
    db_file = tmp_path / "digikam4.db"

    profiles_file.write_text(
        f"""
archive_root: "{archive_dir}"
digikam_db: "{db_file}"
rclone_cache_dir: "{cache_dir}"
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
  canon_18_150: "RF-S 18-150mm F3.5-6.3 IS STM"
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
    return profiles_file


def test_cli_init_and_stamp_e2e_lifecycle(tmp_path: Path) -> None:
    profiles_file = _write_test_profiles(tmp_path)
    archive_dir = tmp_path / "Archive"

    # 1. Interactive `photoflow init` creating a flat album with 1 digital body + 1 film roll
    interactive_inputs = "\n".join(
        [
            "flat",  # Album Mode
            "2026-05-14",  # Date
            "LakeDistrict_Hike",  # Event Name
            "Day_Outing",  # Category
            "Europe/UK/Lake District",  # Locations
            "r10",  # Digital camera key
            "canon_18_150",  # Digital lens key
            "R045",  # Film Roll ID
            "om1",  # Film camera key
            "zuiko_50, zuiko_28",  # Film lenses
            "zuiko_50",  # Default lens
            "zuiko_28: 3-3",  # Lens overrides (frame 3 uses 28mm)
            "gold200",  # Film stock key
            "Standard",  # Process
            "malaysia",  # Lab key
            "",  # Finish adding film rolls
        ]
    ) + "\n"

    res_init = runner.invoke(
        app,
        ["init", "--config", str(profiles_file)],
        input=interactive_inputs,
    )
    assert res_init.exit_code == 0, res_init.output

    album_dir = archive_dir / "2026" / "2026-05-14_LakeDistrict_Hike"
    assert album_dir.is_dir()
    for sub in ("RAW", "Scans", "Edits", "Exports", "Video"):
        assert (album_dir / sub).is_dir()
    assert (album_dir / "album.yaml").is_file()

    cfg = load_effective_album_yaml(album_dir)
    assert cfg.category == "Day_Outing"
    assert len(cfg.gear.film) == 1
    assert cfg.gear.film[0].roll_id == "R045"
    assert cfg.gear.film[0].default_lens == "Zuiko Auto-S 50mm f/1.8"

    # 2. Populate Scans/ with files needing natural sort: 1.jpg, 2.jpg, 10.jpg
    scans_dir = album_dir / "Scans"
    f1 = scans_dir / "1.jpg"
    f2 = scans_dir / "2.jpg"
    f10 = scans_dir / "10.jpg"
    for f in (f10, f1, f2):
        f.write_bytes(MINIMAL_JPEG_BYTES)

    # 3. Initial `photoflow stamp` on the album directory
    res_stamp = runner.invoke(
        app,
        ["stamp", str(album_dir), "--config", str(profiles_file)],
    )
    assert res_stamp.exit_code == 0, res_stamp.output

    meta1 = read_metadata_batch(
        [f1, get_sidecar_path(f1), f2, get_sidecar_path(f2), f10, get_sidecar_path(f10)]
    )
    # Verify natural sort order: 1.jpg -> frame 1 (12:00:00), 2.jpg -> frame 2 (12:01:00), 10.jpg -> frame 3 (12:02:00)
    assert str(meta1[get_sidecar_path(f1).resolve()]["XMP-photoflow:FrameNumber"]) == "1"
    assert meta1[f1.resolve()]["EXIF:DateTimeOriginal"] == "2026:05:14 12:00:00"
    assert meta1[f1.resolve()]["EXIF:LensModel"] == "Zuiko Auto-S 50mm f/1.8"

    assert str(meta1[get_sidecar_path(f2).resolve()]["XMP-photoflow:FrameNumber"]) == "2"
    assert meta1[f2.resolve()]["EXIF:DateTimeOriginal"] == "2026:05:14 12:01:00"
    assert meta1[f2.resolve()]["EXIF:LensModel"] == "Zuiko Auto-S 50mm f/1.8"

    assert str(meta1[get_sidecar_path(f10).resolve()]["XMP-photoflow:FrameNumber"]) == "3"
    assert meta1[f10.resolve()]["EXIF:DateTimeOriginal"] == "2026:05:14 12:02:00"
    # Frame 3 had override zuiko_28!
    assert meta1[f10.resolve()]["EXIF:LensModel"] == "Zuiko Auto-W 28mm f/2.8"

    # 4. Partial selection re-stamp on ONLY `2.jpg` with `--lens zuiko_28`:
    # Must preserve FrameNumber=2 and DateTimeOriginal="2026:05:14 12:01:00" while updating lens to 28mm!
    res_restamp = runner.invoke(
        app,
        [
            "stamp",
            str(f2),
            "--lens",
            "zuiko_28",
            "--config",
            str(profiles_file),
        ],
    )
    assert res_restamp.exit_code == 0, res_restamp.output

    meta2 = read_metadata_batch([f2, get_sidecar_path(f2)])
    f2_img = meta2[f2.resolve()]
    f2_xmp = meta2[get_sidecar_path(f2).resolve()]
    assert str(f2_xmp["XMP-photoflow:FrameNumber"]) == "2"
    assert f2_img["EXIF:DateTimeOriginal"] == "2026:05:14 12:01:00"
    assert f2_img["EXIF:LensModel"] == "Zuiko Auto-W 28mm f/2.8"
    f2_tags = extract_existing_tags(f2_xmp)
    assert "Film/Lens/Zuiko Auto-W 28mm f/2.8" in f2_tags
    assert "Film/Lens/Zuiko Auto-S 50mm f/1.8" not in f2_tags


def test_cli_cache_status_warm_and_evict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles_file = _write_test_profiles(tmp_path)
    archive_dir = tmp_path / "Archive"
    cache_dir = tmp_path / "rclone_cache"

    album_dir = archive_dir / "2026" / "2026-05-14_LakeDistrict_Hike"
    (album_dir / "Scans").mkdir(parents=True)
    (album_dir / "Video").mkdir(parents=True)
    (album_dir / "Scans" / "01.jpg").write_bytes(b"scan-bytes")
    (album_dir / "Video" / "clip.mp4").write_bytes(b"heavy-video-bytes")

    # Populate simulated rclone VFS cache directory
    cached_album = cache_dir / "2026" / "2026-05-14_LakeDistrict_Hike"
    (cached_album / "Scans").mkdir(parents=True)
    (cached_album / "Scans" / "01.jpg").write_bytes(b"cached-scan-bytes")

    calls: list[tuple[str, Any]] = []
    mock_stats_payload: dict[str, Any] = {
        "diskCache": {
            "uploadsInProgress": 0,
            "uploadsQueued": 0,
            "bytesUsed": 1048576,
            "files": 12,
        }
    }

    def fake_post(url: str, json: Any = None, timeout: float = 5.0) -> httpx.Response:
        calls.append((url, json))
        request = httpx.Request("POST", url)
        if url.endswith("/vfs/stats"):
            return httpx.Response(200, json=mock_stats_payload, request=request)
        if url.endswith("/vfs/refresh"):
            return httpx.Response(200, json={"result": {"": "OK"}}, request=request)
        return httpx.Response(404, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)

    # 1. Test `photoflow cache status`
    res_status = runner.invoke(
        app,
        ["cache", "status", "--config", str(profiles_file)],
    )
    assert res_status.exit_code == 0, res_status.output
    assert "uploadsInProgress" in res_status.output
    assert "uploadsQueued" in res_status.output
    assert "1.00 MB" in res_status.output

    # 2. Test `photoflow cache warm` (verifying Video/ is pruned by default)
    res_warm = runner.invoke(
        app,
        ["cache", "warm", str(album_dir), "--config", str(profiles_file)],
    )
    assert res_warm.exit_code == 0, res_warm.output
    assert "Warmed 1 file(s)" in res_warm.output
    assert any(u.endswith("/vfs/refresh") for u, _ in calls)

    # 3. Test `photoflow cache evict` blocked when uploads are queued
    mock_stats_payload["diskCache"]["uploadsQueued"] = 2
    res_evict_blocked = runner.invoke(
        app,
        ["cache", "evict", str(album_dir), "--config", str(profiles_file)],
    )
    assert res_evict_blocked.exit_code == 1
    assert "Aborting eviction" in res_evict_blocked.output
    assert cached_album.exists()

    # 4. Test `photoflow cache evict` succeeds when uploadsInProgress == 0 and uploadsQueued == 0
    mock_stats_payload["diskCache"]["uploadsQueued"] = 0
    res_evict_ok = runner.invoke(
        app,
        ["cache", "evict", str(album_dir), "--config", str(profiles_file)],
    )
    assert res_evict_ok.exit_code == 0, res_evict_ok.output
    assert not cached_album.exists()


def test_cli_init_parent_and_subalbum(tmp_path: Path) -> None:
    profiles_file = _write_test_profiles(tmp_path)
    archive_dir = tmp_path / "Archive"

    # 1. Create parent multi-month album
    res_parent = runner.invoke(
        app,
        [
            "init",
            "--mode",
            "parent",
            "--date-start",
            "2026-07-01",
            "--date-end",
            "2026-09-15",
            "--name",
            "Asia_Trip",
            "--category",
            "Trip",
            "--locations",
            "Asia",
            "--digital",
            "r10",
            "--digital-lenses",
            "canon_18_150",
            "--config",
            str(profiles_file),
        ],
    )
    assert res_parent.exit_code == 0, res_parent.output
    parent_dir = archive_dir / "2026" / "2026-07--09_Asia_Trip"
    assert parent_dir.is_dir()
    assert (parent_dir / "album.yaml").is_file()
    # Parent album should NOT have media subfolders directly inside it
    assert not (parent_dir / "RAW").exists()

    # 2. Create subalbum inside parent_dir
    res_sub = runner.invoke(
        app,
        [
            "init",
            "--mode",
            "subalbum",
            "--parent",
            str(parent_dir),
            "--date-start",
            "2026-07-01",
            "--date-end",
            "2026-07-14",
            "--name",
            "01_China",
            "--category",
            "Trip",
            "--locations",
            "Asia/China/Beijing",
            "--digital",
            "none",
            "--config",
            str(profiles_file),
        ],
    )
    assert res_sub.exit_code == 0, res_sub.output
    sub_dir = parent_dir / "01_China"
    assert sub_dir.is_dir()
    assert (sub_dir / "RAW").is_dir()
    assert (sub_dir / "Scans").is_dir()
    assert (sub_dir / "album.yaml").is_file()

    # Verify upward inheritance from sub_dir
    eff = load_effective_album_yaml(sub_dir)
    assert eff.parent_album == "../album.yaml"
    assert eff.locations == ["Asia", "Asia/China/Beijing"]
    assert len(eff.gear.digital) == 1
    assert eff.gear.digital[0].body == "Canon EOS R10"


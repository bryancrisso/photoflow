# Project Specification: `photoflow` CLI (Stage 1)

## 1. Overview & Tech Stack
Build a modular Python 3.12+ CLI package named `photoflow` managed via `uv` (`pyproject.toml`).
- **CLI Framework:** `typer` + `rich` (for interactive prompts and clean tables)
- **Validation & Config:** `pydantic` v2 + `PyYAML`
- **Metadata Engine:** `exiftool` (via `PyExifTool` or batched `subprocess` calls to `exiftool -@ -`)
- **HTTP Client:** `httpx` (for calling the local `rclone rc` daemon at `http://127.0.0.1:5572`)
- **Database:** Python standard library `sqlite3` (for updating digiKam's `digikam4.db`)

## 2. Directory Structure
```text
~/photoflow/
├── pyproject.toml
└── photoflow/
    ├── __init__.py
    ├── cli.py                  # Typer app registering `init`, `stamp`, `cache`
    ├── core/
    │   ├── config.py           # Loads ~/.config/photoflow/profiles.yaml
    │   ├── yaml_loader.py      # Pydantic models for album.yaml + parent/child inheritance merger
    │   ├── digikam_db.py       # Updates digiKam's `Albums` SQLite table safely
    │   └── xmp_sidecar.py      # ExifTool batch wrapper for 3-layer metadata writing
    └── commands/
        ├── init_album.py       # `photoflow init`
        ├── stamp.py            # `photoflow stamp`
        └── cache.py            # `photoflow cache`
```

## 3. Core Module Specifications

### 3.1 `core/yaml_loader.py` (Album Schema, Multi-Lens Film Model & Inheritance)
- Validate `album.yaml` using Pydantic v2.
- **Prefix Validation Regex:**
  - Single day: `^\d{4}-\d{2}-\d{2}_[A-Za-z0-9_-]+$`
  - Multi-month: `^\d{4}-\d{2}--\d{2}_[A-Za-z0-9_-]+$`
  - Seasonal/Misc: `^\d{4}-00_[A-Za-z0-9_-]+$`
  - Sub-album leg: `^\d{2}_[A-Za-z0-9_-]+$` (e.g., `01_China`)
- **Max Nesting Depth:** Block physical album nesting deeper than 2 levels below the `YYYY/` directory.
- **Film Roll & Multi-Lens Schema (`FilmRollConfig`):**
  ```yaml
  gear:
    digital:
      - body: "Canon EOS R10"
        lenses:
          - "RF-S 18-150mm F3.5-6.3 IS STM"
          - "RF 50mm F1.8 STM"
    film:
      - roll_id: "R045"
        camera_make: "Olympus"
        camera_model: "Olympus OM-1"
        lenses:
          - "Zuiko Auto-S 50mm f/1.8"
          - "Zuiko Auto-W 28mm f/2.8"
        default_lens: "Zuiko Auto-S 50mm f/1.8" # Optional; auto-set if len(lenses) == 1
        lens_overrides:                          # Optional per-frame lens assignments
          - lens: "Zuiko Auto-W 28mm f/2.8"
            frames: ["14-18", 22]                # Supports int or range strings "start-end"
        stock_brand: "Kodak"
        stock_name: "Gold 200"
        format: "35mm"                           # [35mm | 120 | Half-Frame]
        film_type: "Color Negative"              # [Color Negative | B&W | Slide]
        iso_shot: 200
        process: "Standard"                      # [Standard | Pushed +1 | Pushed +2 | Pulled -1]
        lab: "AG Photo Lab"
        date_shot: "2026-07-04"                  # Optional override for roll timestamping
  ```
- **Frame Range Parser Helper (`resolve_lens_for_frame(roll, frame_number: int)`):**
  - Parses `lens_overrides` entries where `frames` can contain integers (`22`) or range strings (`"14-18"` -> `{14, 15, 16, 17, 18}`).
  - Returns `(exif_lens: str | None, xmp_lens_tags: list[str])`:
    1. **Match in `lens_overrides`:** Returns `(override.lens, [f"Film/Lens/{override.lens}"])`.
    2. **No override match, but `default_lens` is set (or `len(roll.lenses) == 1`):** Returns `(default_lens, [f"Film/Lens/{default_lens}"])`.
    3. **Multiple `lenses` listed, no `default_lens` and no override for this frame:** Returns `(None, [f"Film/Lens/{l}" for l in roll.lenses])` so `EXIF:LensModel` is left blank/untouched while all candidate lenses are indexed in XMP tags.
- **Upward Walk & Inheritance (`load_effective_album_yaml(path)`):**
  - Given any file or folder path, walk upward to find the nearest `album.yaml`.
  - If `parent_album` is set (e.g., `"../album.yaml"`), load the parent first and merge the child on top:
    - Scalar fields (`album_id`, `title`, `category`, `date_start`, `date_end`, `status`, `notes`): Child overrides parent if non-null.
    - List fields (`locations`, `album_tags`): Union of parent and child lists (preserving order, deduplicated).
    - `gear.digital` and `gear.film`: Concatenate parent and child lists (if child defines a `roll_id` matching parent, child overrides that `roll_id`).

### 3.2 `core/digikam_db.py` (SQLite Bridge)
- Connect to `digikam4.db` (path from `profiles.yaml`) using `sqlite3.connect(db_path, timeout=5.0)`.
- Compute `relativePath` relative to `archive_root`, starting with `/` and with NO trailing slash (e.g., `/2026/2026-05-14_LakeDistrict_Hike`).
- Build a formatted caption combining `title`, `notes`, and a concise summary of `gear` (including digital bodies/lenses and film rolls/lenses).
- Execute an `UPDATE` on the `Albums` table:
  ```sql
  UPDATE Albums
  SET date = :date_start,
      caption = :caption,
      collection = :category
  WHERE relativePath = :relative_path;
  ```
- If `cursor.rowcount == 0` (meaning digiKam hasn't indexed the new folder yet), log a friendly notice that metadata will sync to `digikam4.db` on the next `photoflow stamp` run.

### 3.3 `core/xmp_sidecar.py` (3-Layer Metadata & ExifTool Rules)
- **Crucial Sidecar Rule:**
  - For `.CR3` (and any RAW format): NEVER modify the binary RAW file. Write strictly to `<filename>.<ext>.xmp` (e.g., `IMG_001.CR3.xmp`).
  - For `.jpg` / `.tiff` inside `Scans/`: Write Layer 1 EXIF tags (`-EXIF:Make`, `-EXIF:Model`, `-EXIF:LensModel` if resolved, `-EXIF:ISO`, `-EXIF:DateTimeOriginal`, `-overwrite_original`) directly into the scan file, and write all Layer 2 & Layer 3 XMP tags into `<filename>.<ext>.xmp`.
  - For `.jpg` inside `Exports/`: Write tags directly into the JPEG file (`-overwrite_original`) AND update `.xmp` if one exists.
- **Exact digiKam Color Label Integer Mapping (`-XMP-digiKam:ColorLabel=<int>`):**
  - `RAW/` subfolder -> `0` (None) + tag `Stage/RAW`
  - `Scans/` subfolder -> `5` (Blue) + tag `Stage/Scan`
  - `Edits/` subfolder -> `3` (Yellow) + tag `Stage/Edit`
  - `Exports/` subfolder -> `4` (Green) + tag `Stage/Export`
- **Hierarchical Tag Formatting & Safe Merging:**
  - Read existing `XMP-digiKam:TagsList` and `XMP-lr:hierarchicalSubject` first so manual user tags (like `People/*`, `Collection/*`, `Subject/*`, `Workflow/*`) are preserved.
  - When updating a lens on an already-stamped scan, replace any existing `Film/Lens/*` tag with the newly resolved `Film/Lens/<lens>` tag(s) while preserving all other tags.
  - Write the merged tag list back to BOTH `-XMP-digiKam:TagsList` and `-XMP-lr:hierarchicalSubject`.

## 4. Subcommand Requirements

### 4.1 `photoflow init`
- Interactive CLI prompt (via `rich.prompt` or CLI flags) to create a new album or sub-album:
  1. Ask for Album Mode: `flat` (`YYYY/YYYY-MM-DD_Event`), `parent` (`YYYY/YYYY-MM--MM_Trip`), or `subalbum` (`YYYY/YYYY-MM--MM_Trip/01_Leg`).
  2. Prompt for Date(s), Event Name, Category (`Trip`, `Day_Outing`, `Event`, `Film_Roll`, `Shoot`, `Misc`), and Locations.
  3. **Decoupled Gear & Film Selector (from `~/.config/photoflow/profiles.yaml`):**
     - **Digital:** Pick one or more camera keys from `cameras.digital` (e.g., `r10`) and select one or more lenses from `lenses` (defaulting to lenses matching the camera's `mount`, while allowing any lens).
     - **Film Rolls:** Loop to add 0 or more film rolls. For each roll:
       - Prompt for `roll_id` (e.g., `R045`).
       - Pick a camera key from `cameras.film` (e.g., `om1`).
       - Pick **one or more** lens keys from `lenses` (e.g., `zuiko_50, zuiko_28`).
       - If 1 lens is chosen, auto-set `default_lens` to that lens. If >1 lens is chosen, ask which lens is the `default_lens` (or `"none"` if unknown) and optionally prompt for any known `lens_overrides` frame ranges (e.g., `zuiko_28: 14-18`).
       - Pick a `film_stocks` key (`gold200`), `process` (`Standard`, `Pushed +1`, etc.), and `labs` key (`ag`).
  4. Create the directory structure:
     - If `parent`: create only the parent folder and `album.yaml`.
     - If `flat` or `subalbum`: create `[RAW, Scans, Edits, Exports, Video]` subfolders and write `album.yaml`.
  5. Attempt `digikam_db` update.

### 4.2 `photoflow stamp [PATHS...] [--roll ROLL_ID] [--lens LENS_KEY_OR_NAME]`
- Accepts one or more file paths or a directory path (works from CLI and digiKam's "Open With" `%F`).
- Resolves the effective `album.yaml` (with parent inheritance) and updates `digikam4.db`.
- Groups files by subfolder (`RAW`, `Scans`, `Edits`, `Exports`).
- **Film Scan Stamping & Multi-Lens Logic (`Scans/`):**
  1. **Roll Selection:** If `--roll` is passed, use it. Otherwise, if the effective `album.yaml` has 1 film roll, select it automatically; if >1 film roll, prompt the user (via CLI prompt or `kdialog --combobox` when launched from digiKam without a TTY) to pick which `roll_id` applies.
  2. **Frame Numbering & Partial Re-Stamp Safeguard:**
     - Sort the selected scan files using natural sort (`01.jpg`, `02.jpg`, ..., `36.jpg`).
     - Check if each file's `.xmp` sidecar already has `XMP-photoflow:FrameNumber` and `EXIF:DateTimeOriginal`:
       - **Full / Initial Stamp (no existing `FrameNumber`):** Assign sequential `FrameNumber` (`1..N`) and compute `EXIF:DateTimeOriginal` starting at `roll.date_shot` (or `album.date_start`) `12:00:00`, incrementing by `+1 minute` per frame so roll order is strictly preserved.
       - **Partial Selection Re-Stamp (existing `FrameNumber` present, e.g., user selected 5 frames in digiKam to override their lens):** Preserve each file's existing `FrameNumber` and `EXIF:DateTimeOriginal` so sequential roll order is never broken.
  3. **Per-Frame Lens Resolution:**
     - If `--lens` is explicitly passed on the CLI (or chosen via `--pick-lens` in `kdialog` from digiKam), resolve that lens key against `profiles.yaml` (or use the literal string) and stamp it into `EXIF:LensModel` and `Film/Lens/<lens>` for all selected files.
     - Otherwise, call `resolve_lens_for_frame(roll, frame_number)` for each frame to stamp the frame-specific override lens, the `default_lens`, or the multi-lens XMP tags.
- Batch-execute ExifTool to stamp Layer 1 (`Scans/` EXIF), Layer 2 (Hierarchical Tags + ColorLabel), and Layer 3 (`XMP-photoflow:AlbumID`, `RollID`, `FrameNumber`, `StampedAt`).

### 4.3 `photoflow cache <warm|evict|status> [ALBUM_PATH]`
- `status`: Calls `POST http://127.0.0.1:5572/vfs/stats` and prints a Rich summary table of `uploadsInProgress`, `uploadsQueued`, and total cache size.
- `warm <album_path>`:
  1. Calls `POST http://127.0.0.1:5572/vfs/refresh` with `dir=<relative_album_path>` and `recursive=true`.
  2. Walks `<album_path>`, excluding `Video/` (unless `--with-video` is passed), and reads all files in parallel (`ThreadPoolExecutor(max_workers=8)`) with a Rich progress bar to pull them into the local NVMe VFS cache.
- `evict <album_path>`:
  1. Checks `vfs/stats` first; aborts with a warning if `uploadsInProgress > 0` or `uploadsQueued > 0`.
  2. Deletes the corresponding cached files inside `rclone_cache_dir` (`~/Photo_Workspace/.rclone_cache/vfs/gdrive/Photo_Archive/<relative_album_path>`).

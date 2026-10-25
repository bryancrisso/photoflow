<!-- markdownlint-disable-file MD013 -->
<!-- markdownlint-disable-file MD009 -->

# Photography Workflows System

## 1. Initial prompt outline

I'm trying to organise my photos across many different storage mediums and set up a workflow for editing and storing these photos for easy access.

Previous workflow:

I used Lightroom Classic on Windows and stored photos in a very unstructured way across a large number of backing stores. RAW files were stored on disk, on OneDrive, and final results often uploaded to Google Drive.

Current workflow:

I've since switched to Linux and am using Arch Linux on my laptop. It has a fairly powerful CPU (Intel i7-12700H) but an integrated GPU.
Because of this, I've switched to Lightroom Cloud, which is convenient for being able to edit from a range of devices, but this benefit is usually offset by how slow the software is, and I find it limiting with some features missing from the Classic app, and I've quickly reached the 100GB storage limit, which means I have to go back to trying to store photos locally with no real structure.

Requirements and current resources:

In terms of storage, I have a lot. My laptop has some local storage on a very fast nvme drive (1TB). Half of this is unused and dedicated to a broken Windows partition that is no longer in use and that I haven't got round to clearing and consuming into Linux yet. I also have a spare 500GB nvme drive that I use to store more photos, and a 500GB hard drive, and a large 3.5" 4TB server hard drive.

I also have 5TB of Google Drive space on the AI pro subscription, that I would wonder whether I could use for a backing store (and whether I could programmatically interact with to directly use from code/programs on my laptop). I have a pretty fast internet connection at home (500Mbps) so this would be good.

I've previously tried to collect all my photos onto the 4TB server hard drive, but this is a pretty slow drive, inconvenient to use and with huge latency. I previously had it hooked up to a home server set up but the speeds weren't fast enough for easy photo access (before i upgraded internet). Currently I rarely use it to make quite incomplete backups, and it is filling up with stuff other than photos. It'd be a good project to organise it out and remove duplicates, but it would be best as an infrequent backup device.

I'd like to collect all my existing photos into one easy to access system so that I can easy access and show off my photos.

I'd also like to formalise a workflow where I can take an album of photos (maybe from a particular trip), load through and cull them, and then apply edits using a Linux-based Lightroom alternative.

I'm not too keen on darktable as I find it less easy to process many photos quickly compared to Lightroom, but I'm happy to learn it and try to develop a fast workflow if it is the best option.

I usually process many RAW photos from my Canon R10 and a lot of JPEG scans from my film cameras. My albums are fairly self-contained but I also get some miscellaneous photos from odd use.

I have access to Google antigravity through my google ai pro subscritpion so I'm happy to use agents to help me set up this workflow.

Goals:

I'd like to formalise a filing system for my photos. Maybe by date would be a good idea?

Also a local workflow for processing these photos, that covers the pipeline from import -> cull -> edit -> export -> store.

I'd like to work out how to use my various backing stores to be able to have lightning fast access for editing, and then a medium speed store for being able to access these photos later, and then a backup system (fine if it is slow).
I was thinking a cool workflow might be to have a large backing store in Google Drive that a program directly works with. When importing, I copy from an SD card onto my laptop's drive, then push to Google Drive. When I want to edit said album, I can pull the files from GDrive for quick access, edit locally, then push back to GDrive for long term storage. Every few months I can backup to the large hard drive. I want to reduce the amount of manual work required so would like to write code for an application to handle all these steps.

Would be cool to have a good search function, so I can have album metadata and be able to search through photos.

In a future system would also be interested in making use of AI photo analysis. I would find this especially helpful for analysis of film scans, where I can ask if I overexposed a particular photo or whether it's a poor scan.

## 2. Post initial prompt (1.) worries

I have questions on how to plan out the filing system. Of course, for individual, one-day events, the YYYY-MM-DD_Event format works quite well, but I often have trips that span multiple months, or a roll of film that I might shoot miscellaneously over a summer/winter period. Here then the formatting convention slightly falls apart. I also wonder whether you can organise photos into "logical" albums with digiKam or the like, so that you can have a photo in multiple "collections" while being in one physical filepath. I guess my ideal would be to keep the Event subfolder system with Event / [RAW, Scans, Edits, Exports] but have some sort of prefix to Event that allows me to easily sort the folders (maybe I can allow omission of the day/month or only include the start month or a range, etc.). I also like the idea of the logical album for grouping stuff differently but would struggle to propagate this to other access methods (e.g. from my phone). What about nested albums (only physical). For example, folder Asia Trip 2026, with subfolders China, Japan (more subfolders per city), Malaysia?

Video support in the filing system?

I also would like to plan out/expand what my photo/album metadata files would look like. It'd be good to have a custom private metadata format for organisation.

I worry whether all the different versions of an image (raw, export, etc.) would confuse digikam? How can I store and display custom metadata in digiKam. Can I connect the vision AI workflow directly to digiKam and store state in image metadata etc.?

I also don't really like the Google Drive interface for viewing photos. It's really slow to load photos on my phone, and then I have to download and reorganise the photos to then send them to other apps (e.g. Instagram). I quite like the Google Photos interface, and the "logical album" system is good, although they aren't directly exposed to other apps. Are there any alternative interfaces or should I just stick with the good old Drive app.

## 3. Reiterate on (2.)

Am I going to write a whole python script to upload? Maybe I will just use the rclone mount feature. Also, if I mirror a logical album, is it like a symbolic link? I don't want tons of duplicates. I have the same worry for if I'm using Google Photos. Will I just end up duplicating a bunch of exported photos?

Could I hold RAW/Edit/Export info in the metadata as well and use that to filter in digiKam?

## 5 Plan draft

### 5.1 Filing system

#### 5.1.1 Overview

Photos are sorted into 'albums'. A photo has a fixed base album where it is stored, and then there should be support for 'logical albums' (essentially classification/tags) for recollecting photos in a different way.
For physical storage, use chronological, event-based folder structure. Flexible naming using Variable-Precision ISO Prefixes to ensure alphabetical sorting remains chronological while allowing fuzzy dates.
Prefix rules:

- Single day: `2026-05-14_LakeDistrict_Hike`
- Multi-month trip: `2026-07--09_Asia_Trip`, using `--` to denote a month range.
- Seasonal/Miscellaneous collections: `2026-00_Summer_Portra400` (sorts to top of the folder), or group into a miscellaneous folder.

There is support for nested physical albums (e.g. Asia Trip/China/Beijing). But we should avoid nesting deeper than two
levels, to avoid excessive physical fragmentation of photo collections. Can use tagging systems on individual photo metadata instead.
Each album structure's leaf nodes contain media subfolders, for example under `[Raw, Edits, Film Scans, Exports, Video, ...]`.

Each album has its own metadata (tags, rating, faces) inside the album folder as a sidecar (`album.yaml`).

```yaml
# Sample album metadata. Not exhaustive
album_id: "2026-07-asia-trip-japan"
title: "Japan Leg - Summer Asia Trip"
date_start: "2026-07-15"
date_end: "2026-08-05"
status: "archived" # staging | editing | archived
gear:
  digital: ["Canon EOS R10", "RF-S 18-150mm"]
  film:
    - roll_id: "R045"
      camera: "Olympus OM-1"
      stock: "Kodak Gold 200"
      lab: "AG Photo Lab"
      dev_notes: "Pushed +1 stop"
locations: ["Tokyo", "Kyoto", "Osaka"]
notes: "Heavy rain during the first three days in Kyoto."
```

Individual photos will also have metadata sidecars, that allow us to invent our own private XML namespaces using Python
and `pyexiftool`, while also mapping useful outputs into standard fields that digiKam displays natively in the UI. Good for sorting for photos by hierarchical tags.

Logical album support should be done with Google Drive shortcuts to prevent file duplication, and allows for easy viewing of logical albums from Dolphin or GDrive. There should be an auotmated script to copy over a particular digiKam logical collection tag to a logical folder of shortcuts in GDrive.

#### 5.1.2 Metadata

We keep mutable metadata in sidecar files to limit reuploads of large image files when updating metadata.

| FileType               | Metadata Location                                                 | Notes                                                                                                                                                         |
| ---------------------- | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Digital RAWs (`.CR3`)  | Read-only EXIF (in file) + write mutable metadata to .xmp sidecar | Camera already embeds EXIF data into the image. Use the xmp sidecar to prevent reuploading the entire RAW file when updating medata.                          |
| Film Scans (`.jpg`)    | 1-Time EXIF Stamp on Import + .xmp sidecar for ongoing metadata   | Lab scans arrive with blank/scanner-only EXIF data. We stamp EXIF data on import to recognise the original camera, then continue to use the sidecar as above. |
| Final Exports (`.jpg`) | Baked inside the JPEG file on export from ART.                    | ART bakes EXIF and XMP tags into the JPEG header so it is self-contained for sharing.                                                                         |

The metadata arrives in 3 layers:

1. Standard EXIF fields (built for digital cameras)
   - `EXIF:Make` & `EXIF:Model`
   - `EXIF:LensModel`
   - `EXIF:ISO`
   - `EXIF:DateTimeOriginal`, in the format YYYY:MM:DD HH:MM:SS. For month-only timestamps, increment each frame's timestamp by 1 minute during import so frames 1 to 36 always stay in chronological order!
2. Standard XMP hierarchical tags
   - `Film/Roll/R045`
   - `Stage/Scan`, `Stage/Export`, and `Stage/RAW`
3. Custom private XMP Namespace (`XMP-photoflow:*`) for scripts and AI state
   - Allows Python scripts to read/write structured data cleanly.

We can interact with metadata via digiKam.

- Configure non-destructive sidecars
- Bulk notes, ratings and hierarchical tags
- Bulk Date/Time shifting for film rolls
- Using user shell scripts to batch queue metadata management
- Can also view custom XMP tags

Album.yaml doesn't interact with digiKam directly, but we use it to access album metadata from elsewhere, and we can use a python script to bridge `album.yaml` to digiKam's Albums SQLite database.

Sub-album metadata should use Parent-Child YAML Inheritance to never duplicate trip-wide info. A child leaf folder holds only overrides and specifics for that branch.
To stamp with inheritance,

1. Start from the photo's folder, and look for the YAML.
2. Check for a parent album. If it exists:
   - Single values (`title`, `date`, `notes`) are overriden.
   - Hierarchical tags and locations are combined.
   - Film rolls should be defined at the album which they were shot across.

#### 5.1.3 Metadata specification

##### 5.1.3.A Photo-level schema

| Root Tag Tree | Example Hierarchical Paths                                                                                             | Population Method                                           | Purpose                                                    |
| ------------- | ---------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- | ---------------------------------------------------------- |
| Stage/        | RAW, Scan, Edit, Export                                                                                                | Auto, by subfolder name, with colour label on import/export | Hide unedited RAWs or isolate film scans vs finished JPGs. |
| Medium/       | Digital, Film/35mm, Film/120                                                                                           | Auto, digital if .cr3, otherwise stamped from album.yaml    | Instantly filter entire library by medium                  |
| Film/         | Roll/R045, Camera/Minolta X-700, Stock/Kodak/Gold 200, Type/Colour Negative, Process/Pushed +1, Lab/21 Studio Photolab | Auto, stamped from album metadata                           | Gives film scans the rich metadata as digital              |
| Location/     | Europe/UK/Lake District                                                                                                | Auto from album metadata                                    | Hierarchical location narrowing                            |
| Collection/   | Themes/Minimalism                                                                                                      | Manual                                                      | Logical album system                                       |
| Subject/      | Landscape/                                                                                                             | Manual, or AI-tagged                                        | Broad genre filtering                                      |
| People/       | Family/, Friends/                                                                                                      | Semi-auto, digiKam facial recognition                       |                                                            |
| Workflow/     | Needs_Dust_Cleanup, Needs_Re-edit                                                                                      | Manual                                                      | Actionable workflow notes                                  |
| AI/           | Exposure/Well_Exposed, Exposure/Under_Exposed, ...                                                                     | Auto                                                        | Immediately filter new photos for defects.                 |

##### 5.1.3.B Album-level schema

```yaml
album_id: "2026-07-asia-trip-01-china"
parent_album: "../album.yaml" # null for standard flat albums
title: "China Leg - Summer Asia Trip"
category:
  "Trip" # Maps to digiKam Album Category:
  # [Trip | Day_Outing | Event | Film_Roll | Shoot | Misc]
date_start: "2026-07-01"
date_end: "2026-07-14"
status: "editing" # [staging | culling | editing | archived]

# Auto-stamped as Location/* hierarchical XMP tags on all photos in this album:
locations:
  - "Asia/China/Beijing"
  - "Asia/China/Shanghai"

# Auto-stamped as Event/* or Album/* hierarchical XMP tags if desired:
album_tags:
  - "Event/Vacation"

gear:
  digital:
    - body: "Canon EOS R10"
      lenses: ["RF-S 18-150mm F3.5-6.3 IS STM"]
  film:
    - roll_id: "R045"
      camera_make: "Olympus"
      camera_model: "Olympus OM-1"
      lens: "Zuiko Auto-S 50mm f/1.8"
      stock_brand: "Kodak"
      stock_name: "Gold 200"
      format: "35mm" # [35mm | 120 | Half-Frame]
      film_type: "Color Negative" # [Color Negative | B&W | Slide]
      iso_shot: 200 # Box or metered ISO
      process: "Standard" # [Standard | Pushed +1 | Pushed +2 | Pulled -1]
      lab: "AG Photo Lab"
      date_shot: "2026-07-04" # Optional override for roll timestamping

notes: "Heavy rain in Shanghai; shot R045 mostly around The Bund."
```

##### 5.1.3.C digiKam standard labels

1. Pick labels
   - rejected: unusable/defect, duplicate. Delete at end of cull
   - pending: unsure/second-choice in burst
   - accepted: keeper
2. Star ratings
   - 0 Unculled
   - 1-2 documentary/memory keeper. Archive, no edit
   - 3 solid photo - queue for editing
   - 4 album highlight - shareable in gallery
   - 5 portfolio tier
3. Color Labels
   - None: Original Digital RAW (.CR3)
   - Blue: Original Film Scan (.jpg / .tiff)
   - Yellow: Work-in-progress Edit
   - Green: Final Export (.jpg ready for showcase)

##### 5.1.3.D Private Custom Namespace (XMP-photoflow:\*)

Finally, for machine-readable state that shouldn't clutter your visual tag tree in digiKam, scripts will write these attributes under the custom photoflow XMP namespace:

- XMP-photoflow:AlbumID (e.g., "2026-07-asia-trip-01-china")
- XMP-photoflow:RollID (e.g., "R045")
- XMP-photoflow:FrameNumber (e.g., "14")
- XMP-photoflow:StampedAt (ISO timestamp of when album.yaml was stamped)
- XMP-photoflow:AIStatus ("unanalyzed" | "analyzed" | "skipped")
- XMP-photoflow:AIModel (e.g., "gemini-3-flash")
- XMP-photoflow:AIScanVerdict (e.g., "thin_negative_scanner_boost" | "clean_scan")

AI human-readable critique paragraph goes into the standard `XMP-dc:Description` tag so it is readable from digiKam Captions panel.

### 5.2 Storage Architecture

#### 5.2.1 Tiered Structure

The storage architecture will have a 3 tier structure.

| Tier | Hardware          | Purpose                                                                            |
| ---- | ----------------- | ---------------------------------------------------------------------------------- |
| Hot  | Laptop NVMe drive | Active workspace on a dedicated partition. Synced with the Warm tier via `rclone`. |
| Warm | 5TB Google Drive  | The master archive holding all filed photos.                                       |
| Cold | 4TB Server HDD    | The vault, holding an offline incremental backup of the Google Drive.              |

#### 5.2.2 `rclone` and active workspace sync

`rclone` is a command-line program that can sync files to GDrive. We use its FUSE (Filesystem in Userspace) feature to mount the Google Drive store locally. VFS caching allows the hot tier to stay mostly empty, and I can precache entire folders to quickly load them before working on them.
We can precache the folder structure via the rc command, and precache actual file data by streaming the files through to /dev/null.
We can also control when the cache is cleared via rules (max age, max size), or programmatically by purging the cache.
Be careful to exclude videos from precache warming as this could cause unnecessary cache bloat. `rclone` will support HTTP range requests and streaming.

#### 5.2.3 Important `rclone` commands

```bash
# 1. Mount Google Drive (typically run automatically via a systemd user service)
rclone mount gdrive:Photo_Archive ~/Photo_Workspace/Archive \
  --cache-dir ~/Photo_Workspace/.rclone_cache \
  --vfs-cache-mode full \
  --vfs-cache-max-size 150G \
  --vfs-cache-max-age 9999h \
  --vfs-write-back 5s \
  --rc --rc-no-auth

# 2. Pre-warm an album into local NVMe cache (excluding heavy Video subfolder)
find ~/Photo_Workspace/Archive/2026/2026-05-14_LakeDistrict_Hike \
  -path "*/Video/*" -prune -o -type f -print0 | xargs -0 -P 8 -n 1 cat > /dev/null

# 3. Check if all background uploads have finished (look for uploadsQueued: 0)
rclone rc vfs/stats

# 4. Create a zero-byte Google Drive Shortcut for a Logical Album
rclone backend shortcut gdrive:Photo_Archive \
  "2026/2026-05-14_LakeDistrict_Hike/Exports/IMG_001.jpg" \
  "Logical_Albums/Portfolio/IMG_001.jpg"
```

### 5.3 Software choices

- `rclone` for the storage sync with the cloud.
- digiKam for photo management and culling.
- ART (Another RawTherapee) for RAW and film scan editing. I choose to use ART over Darktable as I am using my laptop that has a powerful CPU but a weaker integrated GPU.

Custom scripts:

```txt
~/photoflow/
├── pyproject.toml              # Registers the `photoflow` CLI command
├── .ExifTool_config            # Defines your custom `XMP-photoflow` XML namespace
└── photoflow/
    ├── cli.py                  # Main entry point (using `typer` or `click`)
    ├── core/
    │   ├── yaml_loader.py      # Walks up folders & merges parent/child album.yaml
    │   ├── xmp_sidecar.py      # pyexiftool wrapper for EXIF + 3-layer XMP writing
    │   └── digikam_db.py       # Safely updates digiKam's `Albums` SQLite table
    └── commands/
        ├── init_album.py       # 1. Album & Sub-Album Initializer (Stage 1)
        ├── stamp.py            # 2. Metadata & Film Roll Stamper (Stage 1)
        ├── cache.py            # 3. Rclone VFS Cache Controller (Stage 1)
        ├── publish.py          # 4. Logical Album Shortcuts & GPhotos Sync (Stage 2)
        ├── ai_analyze.py       # 5. Gemini Vision Film/Photo Analyzer (Stage 2)
        └── maintain.py         # 6. Archive Linter & Cold HDD Backup (Stage 2)
```

Note, I'd also like the album metadata initialiser to support 'camera profiles', to save makes and models of regularly used cameras.

### 5.4 Sample Workflows

#### 5.4.1 Import to Backup

1. **Import:** Plug in SD card. Use an import script to create a dated album in the hot filing system with a `.yaml` metadata, and copy over the desired files, while tagging them with necessary metadata (e.g. an import tag).
2. **Cull:** Cull/rate photos quickly with digiKam, delete rejects.
3. **Edit:** Use ART to edit the selected photos, and export to an `/Exports` subfolder.
4. **Sync:** `rclone`'s FUSE system automatically syncs the photos to Google Drive.
5. **Clean:** We _could_ also remove RAWs from the NVMe post sync to save space, or let an automatic max size rule clear the cache.

### 5.5 AI Analysis Integration

TODO

### 5.7 Setup required

#### 5.7.1 Stage 1: Getting the base system running

- [x] Clear out old unused Windows partition to make space for hot active workspace.
- [ ] Install the necessary software (`rclone`, digiKam, ART)
- [ ] Write scripts for album creation, cache prewarm, and image metadata stamping.
- [ ] Setup the warm master archive in Google Drive and use `rclone` to sync it locally.
- [x] Formally finalise the album- and photo- level metadata and organisation structure. I like the idea of being able to filter by custom hierarchical tags across my entire photo library.
- [ ] Add a few albums to test out the software and filing system and start working on editing photos.

#### 5.7.2 Stage 2: Full usage and AI integration

- [ ] Search for all existing/archived photos I have stored across many cloud services and devices.
- [ ] File all above archived photos into the system.
- [ ] Set up logical album support using Google Drive Shortcuts.
- [ ] Cancel my Lightroom cloud subscription.
- [ ] Work out how I want to access these photos from other devices (copy exports to GPhotos or use an alternative client?). If using GPhotos, can I upload exports programatically to that?
- [ ] Develop and test the AI integration.

## 6. Post-draft notes

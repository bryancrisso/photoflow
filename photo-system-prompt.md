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

## 5. Plan draft

### 5.1 Filing system

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

_note_
I know the album has some fields on gear and generic notes, but it would also be nice for this to be photo specific
(especially if the album is shot on many cameras and over a long time). I know digital cameras will record gear in the EXIF data but this
isn't true for film scans. I don't quite know exactly where to have each type of metadata (in the EXIF or in the sidecar XMP),
and what exactly to include in this metadata and how to interact with it (can I do this directly from digiKam, and add
notes/tags in bulk, etc.?) Help me plan this bit in more detail.
Additionally, for the album yaml files, will this interact with digiKam? Or should I use a different format. Would be nice
to get a full seamless system with digiKam.
Also how does metadata for sub-albums look?

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

TODO

### 5.3 Software choices

- `rclone` for the storage sync with the cloud.
- digiKam for photo management and culling.
- ART (Another RawTherapee) for RAW and film scan editing. I choose to use ART over Darktable as I am using my laptop that has a powerful CPU but a weaker integrated GPU.

### 5.4 Sample Workflows

#### 5.4.1 Import to Backup

1. **Import:** Plug in SD card. Use an import script to create a dated album in the hot filing system with a `.yaml` metadata, and copy over the desired files, while tagging them with necessary metadata (e.g. an import tag). (_note_ it would be nice to have an automated script with a UI to do this too. I can easily conceive of how to do this for the album metadata creation, but less so for the photo selection. Maybe you have a UI to select the chosen photos, and then it copies them over manually, as I don't always want to copy all the files from the SD card over).
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

# Music masters

The 14 source WAVs from Suno are **not versioned**. They are 415 MB, and Git LFS
storage is metered.

- Backup on this machine: `~/Latticefall-masters/`
- Every master's SHA-256 is recorded in `music_manifest.json`, so any copy can be
  verified byte-for-byte against what shipped.
- Only the encoded Ogg in `music/` is committed (28 MB).

To re-ingest after restoring masters into `assets/audio/source/`:

    .venv/bin/python tools/audio/ingest_music.py

If a master is ever lost, the Ogg still plays — but it cannot be re-encoded at a
different quality without the original.

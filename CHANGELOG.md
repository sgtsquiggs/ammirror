# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `ammirror auth apple` signs in to Apple Music through a MusicKit browser flow;
  `ammirror auth ytm` saves YouTube Music credentials from pasted browser headers,
  sanitized before use.
- `ammirror playlists` lists Apple Music playlists, showing which are selected and
  mirrored.
- `ammirror sync` mirrors playlists and likes, with `--dry-run` to preview and
  `--retry-unmatched` to search again for tracks that failed to match.
- Playlist mirrors are owned by ammirror and created private on YouTube Music; track
  order is not mirrored.
- Favorite Songs become likes on YouTube Music: `sync.likes` is `true` (mirror),
  `"add-only"` (never un-like), or `false` (leave likes alone).
- After a sync that liked songs, ammirror checks that the likes stuck and reports any
  that didn't (exit code 1); running `ammirror sync` again re-likes them.
- Conservative matching: live, remix, and cover versions are rejected, and songs
  without an Apple Music catalog link are searched by artist and title.
- `ammirror unmatched` lists skipped tracks and `ammirror pin APPLE_ID VIDEO_ID`
  matches one by hand.
- `-v` and `-vv` global options log progress (verbose) and per-item decisions and
  request details (debug) to stderr.
- Playlist names in the config match regardless of case, spacing, and quote or dash
  style.
- Config keys `sync.playlists`, `sync.likes`, `sync.favorites_playlist`, and
  `sync.mirror_prefix`.

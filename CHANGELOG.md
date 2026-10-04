# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `sync.likes = "add-only"` likes new favorites but never un-likes songs you
  un-favorite. `true` keeps mirroring and `false` leaves likes alone.
- `-v` and `-vv` global options log progress (verbose) and per-item decisions and
  request details (debug) to stderr. Error tracebacks now need `-vv`.
- After a sync that liked songs, ammirror checks that the likes stuck, since YouTube
  Music sometimes silently drops some. Any that didn't are reported as errors (exit
  code 1); running `ammirror sync` again re-likes them.

### Fixed

- Songs without an Apple Music catalog link (uploads and old iTunes-matched
  library entries) are now searched on YouTube Music by title, artist and
  duration instead of being skipped as unmatched.

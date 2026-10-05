# ammirror

Mirror Apple Music playlists and Favorite Songs into YouTube Music, one way.

Apple Music is the source of truth. ammirror creates and maintains matching
playlists and likes on YouTube Music so its recommendations learn from what you
curate in Apple Music. Nothing is ever written back to Apple Music.

## Requirements

- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/)
- A paid Apple Developer Program membership (needed to create a MusicKit key)
- An Apple Music subscription and a YouTube Music account

## Install

The repository is not published yet, so install from a clone:

```sh
git clone <repository-url> ammirror
cd ammirror
uv tool install .
```

## Apple Music setup

ammirror reads your library through the Apple Music API, which needs a MusicKit
private key from your Apple Developer account.

1. In the Developer portal, go to Certificates, Identifiers & Profiles →
   Identifiers and create a **Media ID** with **MusicKit** enabled.
2. Go to **Keys** and create a new key. Enable **Media Services (MusicKit, ...)**
   and configure it with the Media ID from step 1.
3. Download the `.p8` file. Apple lets you download it only once, so keep it safe.
4. Note the **Key ID** (shown on the key page) and your **Team ID** (shown under
   Membership details).
5. Move the downloaded `AuthKey_XXXXXXXXXX.p8` into ammirror's config directory
   and restrict it:

   ```sh
   mkdir -m 700 -p ~/.config/ammirror
   mv /path/to/AuthKey_XXXXXXXXXX.p8 ~/.config/ammirror/
   chmod 600 ~/.config/ammirror/AuthKey_XXXXXXXXXX.p8
   ```

## Configuration

Create `~/.config/ammirror/config.toml` (see [File locations](#file-locations)):

```toml
[apple]
key_path = "~/.config/ammirror/AuthKey_XXXXXXXXXX.p8"
key_id = "XXXXXXXXXX"
team_id = "YYYYYYYYYY"

[sync]
# Apple Music playlist names to mirror, or ["*"] for all. Default: all.
# Names match regardless of case, spacing, and curly vs straight quotes and
# dash styles. ["*"] also selects the Favorite Songs playlist itself, which is
# then mirrored as an ordinary playlist; list names explicitly to avoid that.
playlists = ["*"]
# Favorite Songs as likes on YouTube Music. Default: true.
#   true       mirror: like new favorites, and un-like songs you un-favorite
#   "add-only" like new favorites, but never un-like anything
#   false      leave likes alone
likes = true
# Name of the library playlist Apple generates for your favorites. Apple
# localizes it, so change this if yours is not called "Favorite Songs". The
# name is matched the same forgiving way as `playlists`.
favorites_playlist = "Favorite Songs"
# Text prepended to the name of each mirrored playlist on YouTube Music.
mirror_prefix = ""
```

## Signing in

```sh
ammirror auth apple
```

Opens your browser to sign in to Apple Music and saves the resulting user token.

```sh
ammirror auth ytm
```

Prints the steps for copying request headers from music.youtube.com in your
browser's DevTools, then reads the pasted headers from standard input (press
Ctrl-D on an empty line; Ctrl-Z then Enter on Windows):

- **Firefox:** in DevTools → Network, right-click a `browse` request → Copy
  Value → Copy Request Headers.
- **Chrome or Edge:** in DevTools → Network, select a `browse` request → Headers
  tab → Request Headers, then select all and copy.

ytmusicapi labels this browser authentication as deprecated, but
it works, and the session lasts about two years. ammirror strips the pasted
lines that describe the browser's own request (the HTTP request line, HTTP/2
pseudo-headers such as `:method`, and `content-encoding`/`content-length`),
which would otherwise make YouTube reject every call.

## File locations

- Config, under `$XDG_CONFIG_HOME/ammirror` (default `~/.config/ammirror`):
  `config.toml`, the `.p8` key, `apple-user-token`, and `ytm-browser.json`.
  Credentials are written with mode 0600.
- State, in `$XDG_STATE_HOME/ammirror/state.db` (default
  `~/.local/state/ammirror/state.db`): track matches, pins, playlist mappings,
  and the likes ammirror added.

## Usage

```sh
ammirror playlists          # list Apple Music playlists; shows which are selected and mirrored
ammirror sync --dry-run     # print what would change; never changes YouTube Music
ammirror sync               # mirror playlists and likes
ammirror sync --retry-unmatched   # also search again for tracks that failed to match
ammirror unmatched          # list tracks that could not be matched
ammirror pin APPLE_ID VIDEO_ID    # match a track by hand
```

`--dry-run` never changes YouTube Music, but it still searches for tracks and
caches the match results locally, so the next real sync does not search again.

Exit codes: 0 on success; 1 if anything failed (a playlist was skipped, the
favorites playlist couldn't be read, a search or operation failed, or a like
didn't stick; with `--dry-run`, only skipped playlists, an unreadable
favorites playlist, and failed searches apply); 2 for an
authentication, configuration, or usage problem.

## Logging

Logs go to stderr, so normal command output on stdout is unchanged.

- `ammirror -v sync` (verbose) narrates progress: playlists found and fetched,
  a track-resolution summary, the number of planned operations, each operation as
  it's applied, and the like check.
- `ammirror -vv sync` (debug) adds per-track match decisions and candidate scores,
  per-playlist plan counts, Apple Music and YouTube Music request details, and
  tracebacks for errors.

Tokens, headers, cookies, and key material are never logged; only header names
and request paths are.

## How matching works

Each Apple Music track is looked up on YouTube Music by title and artist, and a
candidate is accepted only when it is a close match. Matching is deliberately
conservative: live, remix, acoustic, and similar versions are not matched to the
studio track, and covers by other artists are not accepted. When nothing
qualifies, the track is skipped and recorded.

Run `ammirror unmatched` to see skipped tracks, with the best guess where there
is one. If you want a specific video, run `ammirror pin APPLE_ID VIDEO_ID`; the
pinned match is used on the next sync.

## Behaviour notes

- Mirrored playlists are owned by ammirror. Tracks you add to them on YouTube
  Music by hand are removed on the next sync.
- Likes work the same way: ammirror only un-likes songs it liked itself.
  Songs you liked on YouTube Music directly are left alone.
- With `likes = "add-only"`, ammirror still likes new favorites but never
  un-likes: songs you un-favorite on Apple Music stay liked on YouTube Music.
  It keeps remembering which likes it added, so switching back to `likes = true`
  un-likes the ones that are no longer favorites on the next sync.
- YouTube Music sometimes silently drops likes when many are sent at once.
  After liking, ammirror checks that they stuck and reports any that didn't;
  running `ammirror sync` again re-likes them.
- If Apple Music returns an empty playlist or no favorites, ammirror treats it as
  a glitch: it warns and removes nothing that run. Emptying a playlist, or all
  your favorites, in Apple Music therefore never clears the mirror.
- Mirrors are created private with the description "Mirrored from Apple Music by
  ammirror". A mirror deleted on YouTube Music while its playlist is still
  selected is recreated, with a warning. Mirror titles follow the Apple Music
  name plus `mirror_prefix`, so a manual rename on YouTube Music is reverted and
  changing the prefix renames all mirrors.
- Track order within a playlist is not mirrored.
- Removing a playlist from `config.toml` stops syncing it but leaves the copy on
  YouTube Music in place. ammirror remembers the copy, so adding the playlist
  back later resumes syncing into it instead of creating a new one.

## Development

```sh
uv sync
prek install
uv run pytest
prek run --all-files
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the commit message format.

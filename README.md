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
5. Move the key into ammirror's config directory and restrict it:

   ```sh
   mkdir -p ~/.config/ammirror
   mv ~/Downloads/AuthKey_XXXXXXXXXX.p8 ~/.config/ammirror/
   chmod 600 ~/.config/ammirror/AuthKey_XXXXXXXXXX.p8
   ```

## Configuration

Create `~/.config/ammirror/config.toml`:

```toml
[apple]
key_path = "~/.config/ammirror/AuthKey_XXXXXXXXXX.p8"
key_id = "XXXXXXXXXX"
team_id = "YYYYYYYYYY"

[sync]
# Exact Apple Music playlist names to mirror, or ["*"] for all. Default: all.
playlists = ["*"]
# Mirror Favorite Songs as likes on YouTube Music. Default: true.
likes = true
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
browser's DevTools, then reads the pasted headers from standard input (finish
with Ctrl-D). ytmusicapi labels this browser authentication as deprecated, but
it works, and the session lasts about two years.

Credentials are stored under `~/.config/ammirror/` with mode 0600.

## Usage

```sh
ammirror playlists          # list Apple Music playlists; shows which are selected and mirrored
ammirror sync --dry-run     # print what would change without touching YouTube Music
ammirror sync               # mirror playlists and likes
ammirror sync --retry-unmatched   # also search again for tracks that failed to match
ammirror unmatched          # list tracks that could not be matched
ammirror pin APPLE_ID VIDEO_ID    # match a track by hand
```

Exit codes: 0 on success, 1 if some operations failed, 2 for an
authentication or configuration problem.

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

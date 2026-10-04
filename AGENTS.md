# AGENTS.md

Guidance for coding agents working in this repository. Read `README.md` first
for what the tool does and how it is installed.

## Layout

| Path | What it is |
|---|---|
| `src/ammirror/cli.py` | The click commands and the `ammirror` console entry point. |
| `src/ammirror/config.py` | Config and path dataclasses, TOML loading, and secret-file writing. |
| `src/ammirror/errors.py` | The `AmmirrorError` hierarchy (`AuthError`, `ConfigError`, ...) mapped to exit codes by the CLI. |
| `src/ammirror/models.py` | Frozen dataclasses for tracks, playlists, candidates, and match results. |
| `src/ammirror/state.py` | SQLite `State`: matches, unmatched tracks, playlist mappings, owned likes. |
| `src/ammirror/apple/token.py` | Builds the Apple developer token (ES256 JWT) from the MusicKit key. |
| `src/ammirror/apple/auth.py` | Local browser flow that obtains the Apple Music user token. |
| `src/ammirror/apple/client.py` | `AppleClient`, the httpx-based Apple Music API reader. |
| `src/ammirror/ytm/client.py` | `YtmusicapiClient`, the ytmusicapi wrapper for YouTube Music. |
| `src/ammirror/match.py` | Pure track-matching and scoring logic. |
| `src/ammirror/sync.py` | Planning (`plan_sync`), applying, and `run_sync` orchestration. |
| `tests/fakes.py` | In-memory `FakeApple` and `FakeYtm` for tests. |
| `tests/factories.py` | Helpers that build synthetic tracks and candidates. |
| `tests/` | pytest suite; no network, synthetic fixtures only. |
| `prek.toml` | Git hooks run by [prek](https://prek.j178.dev): ruff, pyright, file hygiene, commitlint. |
| `commitlint.config.mjs` | Commit message rules (Conventional Commits) for the commitlint hook. |
| `.github/scripts/check-commit-messages.sh` | Runs the commit-msg hook on each commit in a range; used by CI. |
| `.github/workflows/ci.yml` | CI: prek hooks, commit message checks, tests. |
| `CONTRIBUTING.md` | Commit message format and the release process. |
| `CHANGELOG.md` | User-visible changes per release (Keep a Changelog). |
| `.editorconfig` | Indentation and line-ending rules for editors. |

## Conventions

- Python >=3.12, managed with uv. Checks: `uv run pytest`, and
  `prek run --all-files` (ruff, pyright, hygiene).
- Layering: only `apple/client.py` imports httpx among source modules (tests may import it); only `ytm/client.py` and the
  `auth ytm` command import ytmusicapi. `match.py` and `sync.plan_sync` are
  pure; keep I/O out of them.
- Playlist mappings are never dropped. A playlist removed from `sync.playlists`
  keeps its mapping and its YouTube Music copy; selecting it again reuses that
  copy rather than creating a duplicate.
- Tests never touch the network. Fixtures are synthetic, never real library
  data.
- Secrets live in `~/.config/ammirror/` (0600) and never in the repo.
- Commit messages follow `CONTRIBUTING.md`, checked by the prek commit-msg
  hook locally and in CI. Do not bypass hooks with `--no-verify`.
- `docs/superpowers/` holds local design notes and is git-excluded; never
  commit it.

# AGENTS.md

Guidance for coding agents working in this repository. Read `README.md` first
for what the tool does and how it is installed.

## Layout

| Path | What it is |
|---|---|
| `src/ammirror/cli.py` | The click commands and the `ammirror` console entry point. |
| `tests/` | pytest suite; no network, synthetic fixtures only. |
| `prek.toml` | Git hooks run by [prek](https://prek.j178.dev): ruff, pyright, file hygiene, commitlint. |
| `commitlint.config.mjs` | Commit message rules (Conventional Commits) for the commitlint hook. |
| `.github/scripts/check-commit-messages.sh` | Runs the commit-msg hook on each commit in a range; used by CI. |
| `.github/workflows/ci.yml` | CI: prek hooks, commit message checks, tests. |
| `CONTRIBUTING.md` | Commit message format and the release process. |
| `CHANGELOG.md` | User-visible changes per release (Keep a Changelog). |
| `.editorconfig` | Indentation and line-ending rules for editors. |

Planned modules, added by later tasks (extend this table as they land):
`config.py`, `errors.py`, `models.py`, `state.py`, `apple/` (token, auth,
client), `ytm/client.py`, `match.py`, `sync.py`.

## Conventions

- Python >=3.12, managed with uv. Checks: `uv run pytest`, and
  `prek run --all-files` (ruff, pyright, hygiene).
- Layering: only `apple/client.py` imports httpx; only `ytm/client.py` and the
  `auth ytm` command import ytmusicapi. `match.py` and `sync.plan_sync` are
  pure; keep I/O out of them.
- Tests never touch the network. Fixtures are synthetic, never real library
  data.
- Secrets live in `~/.config/ammirror/` (0600) and never in the repo.
- Commit messages follow `CONTRIBUTING.md`, checked by the prek commit-msg
  hook locally and in CI. Do not bypass hooks with `--no-verify`.
- `docs/superpowers/` holds local design notes and is git-excluded; never
  commit it.

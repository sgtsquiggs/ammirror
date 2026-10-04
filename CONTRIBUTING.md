# Contributing

Issues and pull requests are welcome. For anything bigger than a small fix,
open an issue first so we can agree on the approach.

## Development

Install [uv](https://docs.astral.sh/uv/) and [prek](https://prek.j178.dev),
then set up the environment and the Git hooks once per clone:

```sh
uv sync
prek install
```

The hooks run ruff, pyright and file hygiene checks before each commit, and
check the commit message against the rules below. Run the tests with
`uv run pytest`. See [AGENTS.md](AGENTS.md) for the conventions the code
follows. Every behaviour change needs a test.

## Commit messages

Commits follow [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/).
The `commit-msg` hook runs [commitlint](https://commitlint.js.org/) through
prek, with `@commitlint/config-conventional` (see `commitlint.config.mjs`).
CI runs the same hook on every commit in a push or pull request. The hook
needs Node.js; prek uses the one on your system, or installs one.

Besides the format and the types below, commitlint rejects a description that
is empty, starts with a capital letter or ends with a period, an upper-case
type, a header over 100 characters, and body or footer lines over 100
characters. It warns about a missing blank line before the body or footer.

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

- **description**: imperative mood, lower case, no trailing period, header
  under 100 characters, e.g. `fix: stop duplicating tracks on re-run`.
- **body**: separated from the header by a blank line; what changed and why,
  wrapped at 72 characters.
- **footer**: `BREAKING CHANGE: <what breaks and how to migrate>`, or
  references such as `Fixes #12`.

Types:

| Type | Use it for | Version bump |
|---|---|---|
| `feat` | A new feature or setting | minor |
| `fix` | A bug fix | patch |
| `docs` | Documentation only | none |
| `test` | Adding or fixing tests | none |
| `ci` | CI configuration | none |
| `build` | Packaging and dependencies | none |
| `refactor` | Code changes that neither fix a bug nor add a feature | none |
| `perf` | Performance improvements | patch |
| `style` | Formatting only | none |
| `chore` | Maintenance, including releases | none |
| `revert` | Reverting an earlier commit | depends |

A breaking change, marked with `!` after the type (`feat!: ...`) or with a
`BREAKING CHANGE:` footer, means a major version bump. For this project,
breaking changes include renaming or removing a config key, changing the
CLI's commands, flags or exit status contract, and changing the state
database in a way that loses ownership records.

Useful scopes: `apple`, `ytm`, `match`, `sync`, `cli`, `state`, `docs`.
Scopes are optional.

Examples:

```
feat(ytm): retry throttled playlist edits
fix(match): ignore remaster suffixes in album names
docs: explain MusicKit key setup
feat!: rename apple.team_id to apple.team

BREAKING CHANGE: rename apple.team_id to apple.team in ~/.config/ammirror/config.toml.
```

## Releases

The project uses [Semantic Versioning](https://semver.org/). The commit types
since the last release decide the next version, as in the table above.

To release:

1. Move the `Unreleased` entries in `CHANGELOG.md` under a new version heading
   with today's date, and update the comparison links at the bottom.
2. Bump the version in `pyproject.toml` with `uv version <x.y.z>`, which also
   updates `uv.lock`.
3. Commit both with `chore(release): <version>`.
4. Tag the commit `v<version>`, push the tag, and create a GitHub release from
   the changelog section.

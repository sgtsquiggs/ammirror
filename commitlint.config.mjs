// Commit messages follow Conventional Commits; see CONTRIBUTING.md.
// Used by the commitlint commit-msg hook in prek.toml, locally and in CI.
export default {
    extends: ["@commitlint/config-conventional"],
    // Dependabot's headers are set to Conventional Commits in
    // .github/dependabot.yml, but its bodies quote release notes and links
    // past the line-length limits.
    ignores: [(message) => message.includes("Signed-off-by: dependabot[bot]")],
};

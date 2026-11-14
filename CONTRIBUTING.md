# Contributing

## Pull request titles

Pull requests are squash-merged, so the PR title becomes the commit message on
`main` and decides whether a release is published. Titles follow
[Conventional Commits](https://www.conventionalcommits.org/):

```
type(scope): summary
```

The scope is optional, the summary is lowercase and in the imperative mood, and
a CI check rejects anything that does not match.

| Type | Use it for | Release |
| --- | --- | --- |
| `feat` | A new capability | minor |
| `fix` | A bug fix | patch |
| `perf` | A performance improvement | patch |
| `revert` | Reverting an earlier change | patch |
| `docs` | Documentation only | none |
| `refactor` | A change that alters no behavior | none |
| `test` | Tests only | none |
| `build` | Build or dependency changes | none |
| `ci` | Workflow changes | none |
| `style` | Formatting only | none |
| `chore` | Anything else | none |

Add `!` after the type or scope for a breaking change, as in
`feat(extract)!: reject computed property names`, and describe what breaks in the
PR body. A breaking change releases a new major version whatever its type.

## Releases

Merging a `feat`, `fix`, `perf`, `revert`, or breaking change to `main` publishes
the package to npm at the next version and tags it `vX.Y.Z`; other types release
nothing, and several merges can share one release. The version is chosen from the
commit messages since the last tag. The first release publishes the version in
`package.json`.

Each release also attaches system packages to the GitHub release, built by
`scripts/release-package.py`: a `.deb` and an `.rpm` for each of amd64 and arm64, a
`tar.gz` for Homebrew, the Homebrew formula, and `SHA256SUMS`. The `.deb` and `.rpm` carry
the Node.js release pinned in `distribution/node.lock.json`, so they need nothing else
installed. Update that file when Node.js has a new release. The `packages` job builds
the packages on every pull request and installs them in clean Ubuntu, Debian, and Rocky
Linux containers. To do the same locally, with `nfpm` and Docker installed:

```
python3 scripts/release-package.py --version v0.0.0 --output /tmp/packages
scripts/check-packages.sh /tmp/packages
```

After the release, the `notify-homebrew` and `notify-linux-packages` jobs tell the
homebrew-tools and packages repositories. They need the `package-distribution`
environment and a `HOMEBREW_REPO_DISPATCH_TOKEN` secret, and skip with a notice when
the token is missing.

## Local checks

```
npm ci
npm test
npm run build
```

## Test fixtures

`test/fixtures/node_modules` holds the resources of two real generated binding
packages, `@runtimeconditions/common-integrations` and
`@runtimeconditions/env-configuration`, produced by `ci/generate-typescript.py` in
`runtimeconditions/extensions` against core profile schema 0.4.0. Only the files an
installed package carries are copied: `package.json`, the file manifest, and the
`runtimeconditions/` resources. Refresh them together, and the expected profiles under
`test/fixtures/expected` only after reviewing the change. Each workload under
`test/fixtures/workloads` either has an expected profile or is a named rejection in
`test/profiler.test.ts`.

## Maintainer setup

- The release job runs only in `runtimeconditions/ts-rc-profiler`, so a fork or a personal copy never tries to publish.
- Merge with "Squash and merge", using the PR title as the commit message, and
  require the `PR title` and `verify` checks on `main`.
- Publishing uses npm trusted publishing, so there is no token to rotate. The package's
  Trusted Publisher on npmjs.com is organization `runtimeconditions`, repository
  `ts-rc-profiler`, workflow `release.yml`, no environment. If a release ever fails to
  publish, a maintainer can run `npm publish` by hand from a tagged checkout.

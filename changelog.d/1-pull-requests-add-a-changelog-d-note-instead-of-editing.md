### repo: pull requests add a changelog.d/ note instead of editing CHANGELOG.md

Each PR adds one `changelog.d/<pr>-<name>.md` note, so two open PRs never conflict on the changelog; the `changelog` workflow checks it. `tools/changelog.sh release "<heading>"` folds the notes in when a version is cut, and the GitHub release body is that version's section. History is rebuilt into one `### <area>: <change>` entry per change, nothing dropped.

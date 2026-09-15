---
readtime: 3
description: "Sticky's build and release rules — clean worktrees, monotonic labels, receipts over version strings, ancestry before publish. Each one paid for by an incident."
---

# Build & release discipline

Six rules, each purchased with a real incident on nights when two people
shared one checkout. They are structural, not personal: the one who wrote
up the `sed` hazard for others tripped it a day later.

## 1. Publish builds happen in a clean detached worktree

`git worktree add /tmp/sticky-<label> <sha>`, build there, never in the shared
tree. *Incident:* an image built mid-edit shipped as `0.14.2-m12` — a label no
commit carries — answering `page` and `tap` but not `scroll`, with a pre-M10
help string: three eras in one binary. The precise gate is
`git status --porcelain -- firmware/` empty; global `--dirty` trips on tracked
logs that aren't in the image.

## 2. Labels are monotonic, even accidental ones

A published label is burned. The fix ships *above* it (`0.14.3` above the
phantom `0.14.2`), so `fw` in a status reply names at most one image.
`ver_cmp` is numeric: lexicographic ordering would put 0.14.10 below 0.14.9.

## 3. Receipts outrank version strings

A deploy is verified by making the new surface *answer* — probe the verb,
read the JSON, screenshot the glass — never by reading the version string,
which is data inside the image and lies exactly when the build process does.
*Structural fix (0.14.10):* `TINY_FW_COMMIT`, a git sha injected at **build**
time with a `-dirty` suffix, surfaced as `fw_commit` in every status reply.

## 4. Never publish a `-dirty` or unpushed `fw_commit`

The field caught its first offender within hours: the glass was running
`9cdf43cf-dirty` — a fine ancestor plus an unknown delta, most plausibly a
race hunk from the other editor. An artifact that cannot name its exact tree cannot
be audited, rolled back to, or reproduced. Corollary from the same night:
the sha is read from git but the source from the working tree — in a shared
tree those are two different things, hence rule 1.

## 5. Prove ancestry before publishing

A build named 0.14.5 was minutes from publishing *after* 0.14.6 was on glass;
it would have rolled back a security fix while looking routine. The check is
mechanical: `git merge-base --is-ancestor <on-glass> <build>`, where
"on glass" comes from a live `status` probe, never memory. The device-side
twin: the OTA trigger was direction-blind (`strcmp`, not comparison); 0.14.9
made it numeric and fail-closed. Two guards, one on each side of the wire.

## 6. Claim a feature only after string-verifying the artifact

A shared worktree eats hunks between the editor and `git add`: a torn tail
committed with `BUILD_EXIT=1`; a clobbered feed that rollup constant-folded
to `null` and dead-code-eliminated a whole feature out of a *green* bundle.
Sharpened three times since:

- **Test the surface a consumer touches.** A unit test passed while the route
  it guarded was gone — it called the function, never the route.
- **Verify at the right depth.** A crash-loop shipped through an `ast.parse`
  gate: syntactically perfect, ordered wrong. The gate now imports the module.
- **Read back the state after the edit, never the intent before it.** `sed`
  exits 0 whether or not it matched; a version bump silently did nothing and
  two firmwares rode out under one string.

## The invariant underneath

Heartbeat capabilities and the dispatcher's branch table must come from the
same truth. They drifted twice — once in git (caught by review), once on glass
(caught by probes). Advertise exactly what dispatches.

Probe etiquette for a glass someone may be holding: `status` and `sensors`
first (they don't repaint), look at `idle_for_s` and the last `ui_tap`
before painting, restore home after. A reply is not a frame — only a
`screenshot` proves pixels.

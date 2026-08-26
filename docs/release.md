# Releasing and re-verifying — measured 2026-08-26

The marketplace flow was read live on 2026-08-26 from
`HANCORE-linux/omarchy-plugin-marketplace` (`SUBMISSION.md`, `VERIFICATION.md`,
`SECURITY.md`, `registry.json`, and `scripts/security-baseline-*.mjs`). It had
already changed since `docs/plugin-evidence.md` was written the day before.
Re-read it before every submission; the parts that bite are procedural, not
technical.

## The fact that shapes the whole process

**Verification binds to one exact commit, but `omarchy plugin add` clones mutable
HEAD.** The marketplace records a `listingValidatedCommit` and scores the security
baseline against that SHA alone. When a later catalog refresh sees a different
upstream commit, the listing flips to `Update unverified`, and the card shows
`Unverified` — the detail page alone distinguishes the two. Source:
marketplace `VERIFICATION.md` lines 16–24 and 80.

So an ordinary push to `main` costs the badge until the update workflow runs
again. That is not a reason to avoid pushing; it is the reason `main` should move
in deliberate releases rather than continuously.

## Branch policy

- Feature work happens on branches. CI runs `bin/check-repo` on every branch push.
- `main` moves only for a release.
- Every release is tagged `vX.Y.Z`, and `manifest.json`'s `version` matches the
  tag without the `v`. The `version` job in `.github/workflows/ci.yml` enforces
  that on tag pushes; nothing else reads the manifest version, so without the job
  a mismatch stays invisible until the listing already shows the wrong number.

## Before tagging

1. `bin/check-repo` **on an Omarchy machine**. CI runs on `ubuntu-latest`, where
   `omarchy-plugin-validate` and Qt 6 are both absent; both steps print `SKIP`
   there. A green CI badge is not evidence that the plugin validates or that the
   QML type-checks.
2. Install from the public URL, not from a local path, and confirm the widget
   mounts from a clean clone:

       omarchy plugin remove io.github.shllg.walkingpad
       omarchy plugin add https://github.com/shllg/omarchy-walkingpad-control.git

3. Confirm `docs/smoke-test.md` §4b and §4c have been signed off for any release
   that changes motion control. Nothing else in this repository can substitute
   for that.

## Keeping the automatic `passed`

The security baseline is deterministic and its scope is narrow, which makes the
outcome controllable. Of 1,606 records in the marketplace registry on 2026-08-26,
945 scored `passed`, 647 `review-required` and 14 `needs-fixes`. The two most
common capabilities were `package-manager` (265) and `privilege` (264) — almost
always from a README, not from code.

Only `passed` is verified automatically. `review-required` is not a rejection: a
maintainer can still publish it and the badge then reads "maintainer-reviewed".
It just waits on a human.

What is scanned here (`scripts/security-baseline-scope.mjs`):

- the root `README.md`, always, **line by line as if every line were a shell
  command** — prose and inline backticks included, because `isCommentOnly()`
  returns false for the root README;
- every `.py`, `.qml`, `.js`, `.yml` and similar source file outside an excluded
  directory, where a leading `#` comment *is* skipped;
- extensionless files directly under `bin/`;
- every manifest entry point, forced into scope.

`.github/`, `docs/`, `fixtures/` and `tests/` are excluded directories, and `.md`
files other than the root README are not scanned at all. That is why
`docs/design-spec.md` may discuss a package-manager command and `README.md` may
not.

The practical rules, all learned from a real match:

- Never write a literal `sudo`, `pacman -S`, `pip install`, `systemctl` or
  equivalent anywhere in the root README or in a scanned source file. Name the
  package instead: "needs the `python-pytest` package".
- Say "nothing asks for root" rather than "no sudo is required". The scanner does
  parse some negations, but avoiding the word entirely does not depend on the
  exact phrasing it accepts.
- A docstring is not a comment. `walkingpad/transport.py` matched
  `package-manager` from a line inside its module docstring, because only lines
  *starting* with `#` are skipped.
- A filename containing `install`, `setup` or `uninstall` produces the
  `installer` capability on its name alone.

## The release sequence

1. Merge the release branch to `main`.
2. Tag it and push the tag:

       git tag -a vX.Y.Z -m "…"
       git push origin main --follow-tags

3. Take the full 40-character SHA of `main`: `git rev-parse main`.
4. Open the marketplace **Plugin verification** issue form, choose *Verify and
   publish a newer upstream commit*, and give it the plugin id, the repository
   root URL and that SHA. The current listing snapshot stays unchanged until a
   maintainer approves the new one.

First-time submission is a different form and is documented in the marketplace's
`SUBMISSION.md`, which also states that an agent must show the completed issue
body to the owner and create it only on explicit approval. Category `Hardware`;
tags are chosen from a fixed list, at most three.

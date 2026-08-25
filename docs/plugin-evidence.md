# Omarchy plugin evidence — 2026-08-25

This was read from the Omarchy install on this machine at `/usr/share/omarchy`
(`version`: `4.0.0.alpha`) and from the public plugin documentation and marketplace
repository, both of which were reachable on 2026-08-25. The public Omarchy source
was read at commit `b86d4505c11b71f90b7bd14d6a80676863a70775`; the marketplace
source was read at commit `208b70910c9a15acb7596a07c993252b156890f4`.

These are implementation details of a pre-release shell. Re-verify every one
against the installed version and current public documentation before relying on
it for a release or submission.

## Installation and updates

`omarchy plugin add <git-url>` warns that plugin code runs unsandboxed, then clones
the repository into a staging directory. It runs `omarchy-plugin-validate` against
that staging directory, removes the staging tree after clone or validation failure,
checks the manifest id for a collision, and moves a valid clone to
`~/.config/omarchy/plugins/<id>/`. It then calls
`omarchy-shell shell rescanPlugins` and optionally enables the plugin. Sources:
`/usr/share/omarchy/bin/omarchy-plugin-add:96-106,109-168` and public
`manual/32-shell-plugins.md:32-51`.

Updates operate on that installed checkout: `git fetch`, a displayed diff when
interactive, and a fast-forward merge in place, followed by validation and a
rescan. Validation failure rolls the checkout back. Installed plugins are
therefore git checkouts, not copied release bundles. Source:
`/usr/share/omarchy/bin/omarchy-plugin-update:39-79,105-132`.

## Validator contract

The installed validator enforces:

- valid JSON and `schemaVersion` equal to the JSON number `1`;
- presence of `id`, `name`, `version`, `kinds` and `entryPoints`;
- a non-empty id matching `^[A-Za-z0-9][A-Za-z0-9._-]*$`, containing no `..`,
  and not starting with the reserved `omarchy.` namespace;
- a non-empty `kinds` array and an `entryPoints` object;
- when present, `barWidget.defaultSection` equal to `left`, `center` or `right`;
- every entry-point value decoding to a non-empty relative path with no `..` or
  newline, and naming a file that exists inside the plugin;
- the required entry-point key for each declared kind: `bar` → `bar`,
  `bar-widget` → `barWidget`, `menu` → `menu`, `overlay` → `overlay`, `panel` →
  `panel`, and `service` → `service`.

Sources: `/usr/share/omarchy/bin/omarchy-plugin-validate:34-109` and public
`manual/32-shell-plugins.md:73-96`.

The final check rejects any symlink anywhere in the plugin, excluding `.git`
internals. The recorded reason is that a symlink could point a copied plugin at
arbitrary files after the plugin lands in the trusted plugins directory. One
symlink rejects the whole plugin. Source:
`/usr/share/omarchy/bin/omarchy-plugin-validate:111-116`.

## Service singleton and widget access

The singleton is a manifest `service` kind, not QML `pragma Singleton`.
`shell.qml`'s `ensureService()` requires `kinds` to contain `service` and
`entryPoints.service` to exist, creates one object per plugin id under a hidden
`serviceHost`, and injects `omarchyPath`, `shell`, `manifest`,
`barWidgetRegistry` and `pluginRegistry`. `_syncServices()` mounts enabled
services and destroys their objects when their plugin is disabled or removed.
Source: `/usr/share/omarchy/shell/shell.qml:263-346`.

`serviceFor(id)` is the shared lookup. `firstPartyServiceFor(id)` is only a bare
alias to it, with no first-party gate, so a third-party widget uses the same route:
`bar?.shell?.serviceFor(<id>)`. Source:
`/usr/share/omarchy/shell/shell.qml:273-300`. The working combined-kind precedent
is `/usr/share/omarchy/shell/plugins/services/media/manifest.json:1-22`, which
declares `kinds: ["service", "bar-widget"]` and `keepLoaded: true`; its widget
uses the alias at
`/usr/share/omarchy/shell/plugins/services/media/BarWidget.qml:6-10`.

## Enablement and settings

For a third-party plugin, enabled means its id is present in the shell config as
the selected bar, a bar layout item or a top-level plugin entry. A matching
`disabledPlugins[]` entry overrides enablement. Source:
`/usr/share/omarchy/shell/services/PluginRegistry.qml:110-143`.

The bar injects a per-widget `settings` object only when the loaded widget exposes
that property. This happens both for a live inline-settings update and when the
widget item is loaded. Sources:
`/usr/share/omarchy/shell/plugins/bar/Bar.qml:362-386,1744-1753`. The service
loader's injection list has no `settings` property
(`/usr/share/omarchy/shell/shell.qml:283-313`). Choosing the service's
configuration source remains an open Stage 2 decision; this evidence does not
solve it.

## Manifest surface observed

Across the installed first-party manifests, root keys observed were
`schemaVersion`, `id`, `name`, `version`, `author`, `license`, `description`,
`kinds`, `entryPoints`, `keepLoaded`, `activation` and `barWidget`. The
`barWidget` blocks include `displayName`, `description`, `category`,
`allowMultiple`, `defaultSection` (`left`, `center` or `right`) and
`settingsForm`. Sources: `/usr/share/omarchy/shell/plugins/**/manifest.json`, with
the combined service/widget example at
`shell/plugins/services/media/manifest.json:1-22`, section placement at
`shell/plugins/panels/tailscale/manifest.json:15-21`, and the settings-form name at
`shell/plugins/panels/weather/manifest.json:14-20`.

`settingsForm` is forwarded as a symbolic metadata name, not resolved as a path
from the plugin repository (`/usr/share/omarchy/shell/shell.qml:688-700`). It
names a shell-provided form and is not a third-party form extension point.

## Trust boundary

Plugins execute unsandboxed inside the single long-lived shell process. The add
command prints that warning before cloning
(`/usr/share/omarchy/bin/omarchy-plugin-add:96-106`), and the public shell
documentation states that the install path runs no plugin code, install hook or
sudo operation (`shell/README.md:94-110,122-125`).

The public directory states that its automated compatibility and security
baseline checks are limited listing checks, not a security audit or guarantee;
community plugins remain unsandboxed upstream code. Sources: marketplace
`README.md:29-39` and `SECURITY.md:13-33`.

## Marketplace security baseline

The public marketplace security policy was reachable. Its automatic failure
patterns (the deterministic findings) are:

- downloaded content piped directly to a shell;
- `cargo install --git` without a full 40-character `--rev`;
- remote git source built or executed without pinning and checking out a full
  commit;
- dangerous passwordless sudoers rules;
- privileged process control driven by a PID read from predictable shared
  temporary storage.

Source: marketplace `SECURITY.md:49-61`.

Capabilities requiring manual review are `installer`, `package-manager`,
`privilege`, `remote-build`, `bundled-executable-binary`, `service-management`
and `sudoers-modification`. Source: marketplace `SECURITY.md:63-75`.

The scan limits are 1,000 relevant files, 8 MiB of relevant text in total and
512 KiB per relevant text file. Source: marketplace `SECURITY.md:35-47`.

This project's runtime and installation decisions — no daemon, systemd unit,
sudo, bundled binary, installer or network access — clear those findings and
review capabilities. `bin/check-repo` exists to keep that boundary from drifting,
with the structural and runtime-dependency checks in the repository's completion
gate.

## Submission

Submission currently uses the issue-form template at
`HANCORE-linux/omarchy-plugin-marketplace/.github/ISSUE_TEMPLATE/submit-plugin.yml`,
alongside the public directory at `https://omarchyplugins.com/`. A public
repository with a root `manifest.json`, README and licence is expected. Sources:
marketplace `README.md:15-21`, `SUBMISSION.md:5-20`, and
`.github/ISSUE_TEMPLATE/submit-plugin.yml:1-84`.

This is not a direct registry-file pull request. The flow is current as of the
date above and must be re-checked immediately before an actual submission.

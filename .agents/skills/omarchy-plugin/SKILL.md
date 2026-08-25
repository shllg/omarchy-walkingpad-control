---
name: omarchy-plugin
description: Apply the verified Omarchy plugin contract when building, validating, or publishing this repository.
---

# Omarchy plugin contract

This is the working summary. `docs/plugin-evidence.md` is the citation trail and
must be consulted when a loader, validator or publishing detail needs proof.

## Installed artifact

The repository is the plugin users install. `omarchy plugin add` clones it into a
staging directory, validates it, then moves it into the user's plugins directory.
Everything committed ships and is scanned. Any validation failure deletes the
staged clone.

No symlink may be committed. One tracked symlink makes the validator reject the
entire plugin, so anything that has to be a symlink locally -- the runtime skill
trees at `.claude/skills/` and `.codex/skills/` -- is gitignored instead.

## Manifest

`manifest.json` must be valid JSON and requires `schemaVersion`, `id`, `name`,
`version`, `kinds` and `entryPoints`. `schemaVersion` is exactly the JSON number
`1`. The id starts with an alphanumeric character, otherwise contains only
alphanumerics plus `.`, `_` or `-`, contains no `..`, and does not use the
reserved `omarchy.` namespace. Use a globally namespaced id; a bare id may pass
the validator but squats a generic permanent name.

`kinds` is a non-empty array and `entryPoints` is an object. Every declared kind
requires its matching key:

- `bar` → `entryPoints.bar`
- `bar-widget` → `entryPoints.barWidget`
- `menu` → `entryPoints.menu`
- `overlay` → `entryPoints.overlay`
- `panel` → `entryPoints.panel`
- `service` → `entryPoints.service`

Every entry point is a non-empty relative path, contains no `..` or newline, and
names an existing file. Put widget metadata in the `barWidget` block;
`allowMultiple` and `defaultSection` are not top-level keys. `defaultSection`, when
present, is `left`, `center` or `right`. `keepLoaded` is a top-level manifest key;
this combined plugin uses `keepLoaded: true`, while the `service` kind—not that
flag—is what supplies its one service owner.

## One long-lived owner

A plugin needing one long-lived owner declares
`kinds: ["service", "bar-widget"]`. The shell instantiates the service exactly
once per plugin id, and each widget retrieves it through
`bar?.shell?.serviceFor(<id>)`.

QML `pragma Singleton` is not this mechanism. The distinction matters here: the
bar creates a widget per screen, while the pad has only one BLE connection slot.
Putting ownership in the widget can create several competing bridges.

## Theme contract

Build on the shared style and colour singletons. Never put a colour, radius or
size literal in QML, not even as a fallback. Themes swap live, and copied or
hardcoded values do not follow the active theme.

## Development loop

1. Run `omarchy plugin validate <repo>`.
2. Run `qmllint -I "$OMARCHY_PATH/shell" <file>.qml` for each QML file.
3. Run `omarchy-shell shell rescanPlugins`.
4. Use `omarchy-shell shell summon <id> '{}'` and
   `omarchy-shell shell hide <id>` to exercise mounting and teardown.
5. Save a plugin file and confirm the running shell reloads the change.

## Publishing

The public directory expects a public repository with root `manifest.json`,
README and licence files, submitted through its current issue form. Re-check the
flow immediately before submission. Directory checks validate the listing and a
limited static baseline; they do not establish that plugin code is secure.

## Things that are not true

- QML `pragma Singleton` creates the shell-managed service singleton.
- A bar widget or `keepLoaded` alone provides one owner across screens.
- `allowMultiple` or `defaultSection` belongs at manifest top level.
- Services automatically receive the bar widget's `settings` object.
- Plugin installation can install a dependency or run an install hook.
- Acceptance into the public directory is a security audit or guarantee.

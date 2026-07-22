# minecraft-plugin-catalog

The plugin catalog [CraftKeeper](https://github.com/carmelosantana/craftkeeper)
reads. One file is the product:

```
https://raw.githubusercontent.com/carmelosantana/minecraft-plugin-catalog/main/catalog.json
```

That is the URL CraftKeeper ships as its default `CATALOG_CRAFTKEEPER_URL`,
so a stock install finds this catalog with nothing to configure.

## What is in it

Every plugin listed by
[minecraft-plugin-updater](https://github.com/carmelosantana/minecraft-plugin-updater)'s
`plugins.json`. That manifest is the single source of truth for which plugins
this ecosystem ships, so the catalog cannot drift from what the updater
actually installs.

## Where each field comes from

Nothing is hand-written and nothing is defaulted into existence. A plugin
missing any required field is **skipped and reported**, never emitted with a
plausible-looking placeholder.

| Field | Source |
| --- | --- |
| `slug`, `name` | the updater manifest |
| `description`, `minecraftVersions`, `projectUrl`, `dependencies` | `plugin.yml` **inside the released jar** |
| `license`, `sourceRepository` | the GitHub repository |
| `version`, `releasedAt` | the GitHub release |
| `downloadUrl` | the release asset matching the updater's `asset_regex` |
| `sha256` | **computed from the downloaded bytes**, then cross-checked against the release's own `SHA256SUMS.txt` |

Two of those deserve their reasons stated.

**plugin.yml is read from the jar, not the repo.** Maven resource filtering
resolves `${project.description}` and `${project.url}` at package time, so a
repo's source file legitimately still holds the placeholders while the shipped
artifact holds real values. Reading the repo copy produced a catalog entry
containing the literal string `${project.url}`, which the schema rejected. The
jar is also the artifact a server actually runs, so its metadata cannot
disagree with the `downloadUrl` published beside it.

**The checksum is computed, not copied.** Publishing a checksum straight out of
`SHA256SUMS.txt` would mean nobody ever verified it. The generator hashes the
bytes it downloaded and fails loudly on any mismatch — the whole point of the
field is that CraftKeeper refuses an install when it does not match.

## Regenerating

```sh
python3 scripts/build-catalog.py "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > catalog.json
```

Needs the `gh` CLI, authenticated. `ROSTER_FILE=/path/to/plugins.json` reads a
local roster instead of the published one, for testing a roster change before
it merges.

`.github/workflows/build.yml` regenerates daily and on demand, and validates
`catalog.json` against `schema/plugin-catalog.schema.json` on every pull
request.

## The schema is the contract

`schema/plugin-catalog.schema.json` is a copy of CraftKeeper's own
`resources/catalog/plugin-catalog.schema.json`. It is the shared contract
between the two repositories; CraftKeeper validates every fetch against its
copy, and CI here validates every change against this one. If they ever
diverge, CraftKeeper's copy wins — see `docs/architecture/plugin-catalog.md`
in that repository.

`additionalProperties` is deliberately permissive at every level, so a future
catalog can add optional fields without breaking installs pinned to an older
CraftKeeper.

## If this catalog is unreachable

CraftKeeper degrades rather than failing: the Discover page reports the source
as unavailable and continues to show results from Hangar and Modrinth. It never
blocks on this file.

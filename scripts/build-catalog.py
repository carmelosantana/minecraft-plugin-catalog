#!/usr/bin/env python3
"""
Generate catalog.json for carmelosantana/minecraft-plugin-catalog from the
xpfarm plugin roster.

Roster source of truth: minecraft-plugin-updater's plugins.json, so the
catalog can never drift from what the updater actually installs.

Every field comes from a real source. Nothing is defaulted into existence:
a plugin missing any required field is SKIPPED and reported, never emitted
with a plausible-looking placeholder.

  slug              <- updater `destination` minus .jar
  name              <- updater `name`
  description       <- plugin.yml INSIDE THE RELEASED JAR `description`
  projectUrl        <- that same plugin.yml `website`, else the repo URL
  license           <- GitHub repo license SPDX id
  sourceRepository  <- GitHub repo URL
  version           <- release tag, leading "v" stripped
  minecraftVersions <- plugin.yml `api-version`
  platforms         <- ["paper"] (these are Paper plugins; api-version is a
                       Bukkit/Spigot API declaration and Paper is the only
                       platform this ecosystem is built and tested against)
  dependencies      <- that same plugin.yml `depend` (hard deps only;
                       softdepend is optional by definition, not a catalog
                       dependency)

plugin.yml is read from the RELEASED JAR, not from the repo source tree.
Maven resource filtering resolves ${project.description} / ${project.url}
at package time, so the source file legitimately still contains the
placeholders while the shipped artifact has real values. Reading the repo
copy produced a catalog entry with a literal "${project.url}" in it, which
the schema rejected. The jar is also the artifact the server will actually
run, so its metadata cannot disagree with the downloadUrl beside it.

The jar is downloaded to hash it as well: the sha256 published here is
COMPUTED from the bytes and then checked against the release's own
SHA256SUMS.txt. A mismatch skips the plugin loudly rather than publishing
a checksum nobody verified — the entire point of the field is that
CraftKeeper refuses an install when it does not match.
  downloadUrl       <- the release asset matching the updater's asset_regex
  sha256            <- that asset's line in the release's SHA256SUMS.txt
  releasedAt        <- release publishedAt
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

# The roster lives in the updater repo, which is the single source of truth
# for which plugins this ecosystem ships. Fetched over HTTP so CI needs no
# checkout of it; override with ROSTER_URL (or a local path via ROSTER_FILE)
# when testing a change to the roster before it merges.
ROSTER_URL = os.environ.get(
    "ROSTER_URL",
    "https://raw.githubusercontent.com/carmelosantana/minecraft-plugin-updater/main/plugins.json",
)
ROSTER_FILE = os.environ.get("ROSTER_FILE")
PLUGIN_YML_PATHS = [
    "src/main/resources/plugin.yml",
    "src/main/resources/paper-plugin.yml",
]

skipped = []


def gh(*args, raw=False):
    """gh CLI. `raw=True` returns the text as-is: `gh api --jq` prints bare
    strings, not JSON, so json.loads() throws on e.g. base64 content."""
    try:
        out = subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=60, check=True
        ).stdout
    except Exception:
        return None
    if not out.strip():
        return None
    if raw:
        return out.strip()
    try:
        return json.loads(out)
    except Exception:
        return None


def plugin_yml_from_jar(jar_path):
    """The few scalars we need from the jar's own plugin.yml (no YAML dep)."""
    try:
        with zipfile.ZipFile(jar_path) as z:
            names = [n for n in ("plugin.yml", "paper-plugin.yml") if n in z.namelist()]
            if not names:
                return None
            text = z.read(names[0]).decode("utf-8", "replace")
    except Exception:
        return None

    fields = {}
    for key in ("description", "api-version", "website"):
        m = re.search(rf"^{re.escape(key)}:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", text, re.M)
        if m:
            value = m.group(1).strip()
            # An unresolved ${...} would mean the build did not filter it;
            # never publish the literal placeholder.
            if not re.search(r"\$\{[^}]*\}", value):
                fields[key] = value
    m = re.search(r"^depend:\s*\[([^\]]*)\]", text, re.M)
    if m:
        fields["depend"] = [d.strip().strip("'\"") for d in m.group(1).split(",") if d.strip()]
    return fields


def download(url, dest):
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
        return True
    except Exception:
        return False


def sha_for(assets, jar_name):
    """The jar's checksum from the release's own SHA256SUMS.txt."""
    sums = next((a for a in assets if a["name"] == "SHA256SUMS.txt"), None)
    if sums is None:
        return None
    try:
        with urllib.request.urlopen(sums["url"], timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    for line in body.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == jar_name:
            digest = parts[0].strip().lower()
            if re.fullmatch(r"[a-f0-9]{64}", digest):
                return digest
    return None


if ROSTER_FILE:
    roster_doc = json.load(open(ROSTER_FILE))
else:
    with urllib.request.urlopen(ROSTER_URL, timeout=30) as r:
        roster_doc = json.load(r)

roster = roster_doc["plugins"]
plugins = []
workdir = tempfile.mkdtemp(prefix="ck-catalog-")

for entry in roster:
    repo = entry["repo"]
    slug = re.sub(r"\.jar$", "", entry["destination"])
    label = f"{entry['name']} ({repo})"

    meta = gh("api", f"repos/{repo}", "--jq",
              '{url: .html_url, license: .license.spdx_id}')
    if not meta or not meta.get("license"):
        skipped.append(f"{label}: no repo metadata or no license")
        continue

    rel = gh("release", "view", "--repo", repo, "--json",
             "tagName,publishedAt,assets,isDraft,isPrerelease")
    if not rel or rel.get("isDraft"):
        skipped.append(f"{label}: no published release")
        continue

    pattern = re.compile(entry["asset_regex"])
    jar = next((a for a in rel["assets"] if pattern.match(a["name"])), None)
    if jar is None:
        skipped.append(f"{label}: no asset matching {entry['asset_regex']}")
        continue

    jar_path = os.path.join(workdir, jar["name"])
    if not download(jar["url"], jar_path):
        skipped.append(f"{label}: could not download {jar['name']}")
        continue

    # COMPUTED from the bytes, then cross-checked against the release's own
    # SHA256SUMS.txt. Publishing an unverified checksum would defeat the
    # only thing the field is for.
    digest = hashlib.sha256(open(jar_path, "rb").read()).hexdigest()
    declared = sha_for(rel["assets"], jar["name"])
    if declared is None:
        skipped.append(f"{label}: no sha256 for {jar['name']} in SHA256SUMS.txt")
        continue
    if declared != digest:
        skipped.append(
            f"{label}: CHECKSUM MISMATCH — SHA256SUMS.txt says {declared[:12]}…, "
            f"the asset hashes to {digest[:12]}…"
        )
        continue

    yml = plugin_yml_from_jar(jar_path)
    if not yml or "description" not in yml or "api-version" not in yml:
        skipped.append(f"{label}: jar plugin.yml missing description or api-version")
        continue

    plugins.append({
        "slug": slug,
        "name": entry["name"],
        "description": yml["description"],
        "projectUrl": yml.get("website") or meta["url"],
        "license": meta["license"],
        "sourceRepository": meta["url"],
        "releases": [{
            "version": rel["tagName"].lstrip("v"),
            "minecraftVersions": [yml["api-version"]],
            "platforms": ["paper"],
            "dependencies": [{"slug": d.lower(), "required": True}
                             for d in yml.get("depend", [])],
            "downloadUrl": jar["url"],
            "sha256": digest,
            "releasedAt": rel["publishedAt"],
            "withdrawn": False,
        }],
    })
    print(f"  ok  {slug:<22} {rel['tagName']:<10} {digest[:12]}…", file=sys.stderr)

catalog = {
    "catalogVersion": "1.0.0",
    "generatedAt": sys.argv[1] if len(sys.argv) > 1 else None,
    "plugins": sorted(plugins, key=lambda p: p["slug"]),
}
if catalog["generatedAt"] is None:
    del catalog["generatedAt"]

print(json.dumps(catalog, indent=2))

print(f"\n{len(plugins)}/{len(roster)} plugins included", file=sys.stderr)
if skipped:
    # Never silent: a plugin absent from the catalog says why.
    print("SKIPPED:", file=sys.stderr)
    for s in skipped:
        print(f"  - {s}", file=sys.stderr)

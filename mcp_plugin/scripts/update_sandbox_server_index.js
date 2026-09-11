#!/usr/bin/env node
// cSpell:ignore mariadbd sandboxed glibc mkdtemp
//
// Fills in lib/sandbox_server_versions.json from GitHub Actions runs of the
// mariadb-shell repo's Sandbox Server workflow (sandbox-server.yml), instead
// of copying SHA-256 sums by hand off a release page that may not exist yet.
//
// publish-release.yml resolves a "server tag" (e.g. "11.8") to a build by
// matching that workflow's run-name, which is exactly "Sandbox Server
// <tag>" -- the same string as its own mariadb_version input. This script
// does the identical lookup, then downloads that run's six per-platform
// tarballs one at a time, hashes each, and writes the {os, url, sha256sum}
// entries into the index. Nothing is fetched from a release page, so the
// index can be made correct before one is published.
//
// The release tag baked into every package URL is derived the same way
// publish-release.yml derives it for a real publish -- v<major>.<minor>.
// <patch><extra> from MYSQL_VERSION -- except read straight from the
// release/candidate branch via the API rather than from a build run's
// checkout, since at this point no build run for the release exists yet.

"use strict";

const { execFileSync, spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const readline = require("node:readline");

const REPO = "mariadb-corporation/mariadb-shell";
const WORKFLOW = "sandbox-server.yml";
const VERSION_FILE_BRANCH = "release/candidate";
const SUPPORTED_INDEX_VERSION = 1;
const INDEX_PATH = path.join(__dirname, "..", "lib", "sandbox_server_versions.json");

// Each artifact is a per-platform tarball a few hundred MB in size; a slow
// or wedged connection to GitHub's blob storage otherwise hangs the script
// forever with no indication of what it's waiting on. Override via env var
// when a slower link needs more headroom.
const ARTIFACT_DOWNLOAD_TIMEOUT_MS =
  Number(process.env.SANDBOX_DOWNLOAD_TIMEOUT_MS) || 10 * 60 * 1000;
const HEARTBEAT_INTERVAL_MS = 15 * 1000;

// Matches every package name sandbox-server.yml produces, e.g.
// "mariadb-11.8.9-macos26-arm-64bit-sandbox.tar.gz" or
// "mariadb-11.8.9-linux-glibc2.34-x86-64bit-sandbox.tar.gz". The platform
// group is deliberately kept generic (macosNN / windows / linux-glibcX.Y)
// since only whether it starts with "macos"/"linux" or equals "windows"
// decides the index's os key.
const PACKAGE_NAME_PATTERN =
  /^mariadb-(\d+\.\d+\.\d+)-(macos\d+|windows|linux-glibc[\d.]+)-(arm|x86)-64bit-sandbox\.tar\.gz$/;

function platformOf(token) {
  if (token.startsWith("macos")) return "darwin";
  if (token === "windows") return "win32";
  if (token.startsWith("linux")) return "linux";
  return null;
}

function archOf(token) {
  if (token === "arm") return "arm64";
  if (token === "x86") return "x64";
  return null;
}

function ask(rl, question) {
  return new Promise((resolve) => rl.question(question, resolve));
}

async function promptForServerTags() {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  try {
    console.log(
      "List the server tags to look up, one per line -- each is the exact "
      + "'MariaDB Branch/Tag to build' value you gave (or would give) to the "
      + "Sandbox Server workflow, e.g. '11.8' or '12.3'. Blank line to finish.\n"
    );
    const serverTags = [];
    for (;;) {
      const tag = (await ask(rl, `Server tag ${serverTags.length + 1} (blank to finish): `)).trim();
      if (!tag) break;
      serverTags.push(tag);
    }
    return serverTags;
  } finally {
    rl.close();
  }
}

function ghJson(args) {
  const output = execFileSync("gh", args, { encoding: "utf8", maxBuffer: 1024 * 1024 * 32 });
  return JSON.parse(output);
}

// Same formula as publish-release.yml's "Assemble Release Assets" step
// (v<major>.<minor>.<patch><extra> from MYSQL_VERSION), read from the
// release/candidate branch instead of a build run's checkout -- there is no
// build run yet at the point this script is meant to be run.
function resolveReleaseTag() {
  const raw = execFileSync(
    "gh",
    ["api", `repos/${REPO}/contents/MYSQL_VERSION?ref=${VERSION_FILE_BRANCH}`, "-H", "Accept: application/vnd.github.raw"],
    { encoding: "utf8" }
  );

  const fields = {};
  for (const key of ["MAJOR", "MINOR", "PATCH", "EXTRA"]) {
    const match = new RegExp(`^MYSQL_VERSION_${key}=(.*)$`, "m").exec(raw);
    fields[key] = match ? match[1].trim() : "";
  }
  if (!fields.MAJOR || !fields.MINOR || !fields.PATCH) {
    throw new Error(
      `MYSQL_VERSION on ${REPO}@${VERSION_FILE_BRANCH} is missing MAJOR/MINOR/PATCH -- got:\n${raw}`
    );
  }

  return `v${fields.MAJOR}.${fields.MINOR}.${fields.PATCH}${fields.EXTRA}`;
}

// Mirrors publish-release.yml's "Resolve Sandbox Server Runs" step: the
// Actions API has no field exposing a workflow_dispatch run's inputs
// directly, so the tag has to be read back off the run's own display title.
// `gh run list` already returns newest-first, so the first match is the
// latest successful run for that tag.
function findLatestRun(tag) {
  const runs = ghJson([
    "run", "list", "-R", REPO,
    "--workflow", WORKFLOW,
    "--status", "success",
    "--limit", "50",
    "--json", "databaseId,displayTitle,headSha",
  ]);
  return runs.find((run) => run.displayTitle === `Sandbox Server ${tag}`) || null;
}

function findTarballs(dir) {
  const found = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const entryPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      found.push(...findTarballs(entryPath));
    } else if (entry.isFile() && entry.name.endsWith(".tar.gz")) {
      found.push(entryPath);
    }
  }
  return found;
}

function sha256Of(filePath) {
  const digest = crypto.createHash("sha256");
  digest.update(fs.readFileSync(filePath));
  return digest.digest("hex");
}

// The Actions API has no "download all" progress signal, so artifacts are
// listed individually and fetched one at a time -- each gets its own log
// line, a heartbeat while it's in flight, and a timeout that kills it
// instead of leaving the script silently blocked.
function listArtifactNames(run) {
  const data = ghJson(["api", `repos/${REPO}/actions/runs/${run.databaseId}/artifacts`]);
  return (data.artifacts || []).map((artifact) => artifact.name);
}

function downloadArtifact(runId, name, destDir) {
  return new Promise((resolve) => {
    const startedAt = Date.now();
    const elapsedSeconds = () => Math.round((Date.now() - startedAt) / 1000);

    console.log(`    downloading ${name}...`);
    const child = spawn(
      "gh",
      ["run", "download", String(runId), "-R", REPO, "--name", name, "--dir", destDir],
      { stdio: ["ignore", "inherit", "inherit"] }
    );

    const heartbeat = setInterval(() => {
      console.log(`    ...still downloading ${name} (${elapsedSeconds()}s elapsed)`);
    }, HEARTBEAT_INTERVAL_MS);

    const timeout = setTimeout(() => {
      console.warn(
        `    ${name} exceeded ${ARTIFACT_DOWNLOAD_TIMEOUT_MS / 1000}s; killing the download.`
      );
      child.kill("SIGTERM");
    }, ARTIFACT_DOWNLOAD_TIMEOUT_MS);

    child.on("close", (code) => {
      clearInterval(heartbeat);
      clearTimeout(timeout);
      if (code === 0) {
        console.log(`    downloaded ${name} (${elapsedSeconds()}s)`);
      } else {
        console.warn(`    failed to download ${name}: exit code ${code} (${elapsedSeconds()}s)`);
      }
      resolve(code === 0);
    });
  });
}

// Downloads every artifact of one run, hashes each *-sandbox.tar.gz found in
// it, and returns the packages built -- keyed by "major.minor.patch" (there
// is exactly one per run) to a map of os -> {url, sha256sum}. The download is
// removed as soon as it has been hashed: nothing here needs the bytes
// afterwards, and a package is a few hundred MB times six platforms.
async function collectPackages(run, releaseTag) {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "sandbox-server-"));
  try {
    const artifactNames = listArtifactNames(run);
    if (artifactNames.length === 0) {
      console.warn(`  run ${run.databaseId} has no artifacts; skipping.`);
      return null;
    }
    console.log(`  ${artifactNames.length} artifact(s) to download: ${artifactNames.join(", ")}`);

    for (const name of artifactNames) {
      const ok = await downloadArtifact(run.databaseId, name, tmpDir);
      if (!ok) {
        console.warn(`  could not download artifact '${name}' for run ${run.databaseId}; skipping run.`);
        return null;
      }
    }

    const tarballs = findTarballs(tmpDir);
    if (tarballs.length === 0) {
      console.warn(`  run ${run.databaseId} produced no *-sandbox.tar.gz artifacts; skipping.`);
      return null;
    }

    let version = null;
    const packagesByOs = {};
    for (const tarballPath of tarballs) {
      const name = path.basename(tarballPath);
      const match = PACKAGE_NAME_PATTERN.exec(name);
      if (!match) {
        console.warn(`  ${name} does not look like a sandbox server package; skipping it.`);
        continue;
      }

      const [, pkgVersion, platformToken, archToken] = match;
      const platform = platformOf(platformToken);
      const arch = archOf(archToken);
      if (!platform || !arch) {
        console.warn(`  ${name}: could not map '${platformToken}'/'${archToken}' to an os key; skipping it.`);
        continue;
      }
      if (version && version !== pkgVersion) {
        throw new Error(
          `run ${run.databaseId} produced packages for two different versions `
          + `(${version} and ${pkgVersion}) -- that should not happen for one run.`
        );
      }
      version = pkgVersion;

      const sha256sum = sha256Of(tarballPath);
      packagesByOs[`${platform}-${arch}`] = {
        os: `${platform}-${arch}`,
        url: `https://github.com/${REPO}/releases/download/${releaseTag}/${name}`,
        sha256sum,
      };
      console.log(`  ${name} -> ${platform}-${arch}  ${sha256sum}`);
    }

    if (!version) return null;
    return { version, packagesByOs };
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
}

function loadIndex() {
  const raw = fs.readFileSync(INDEX_PATH, "utf8");
  const index = JSON.parse(raw);
  if (index.sandboxServerIndexVersion !== SUPPORTED_INDEX_VERSION) {
    throw new Error(
      `${INDEX_PATH} declares sandboxServerIndexVersion `
      + `${JSON.stringify(index.sandboxServerIndexVersion)}, but this script only `
      + `understands version ${SUPPORTED_INDEX_VERSION}.`
    );
  }
  return index;
}

function mergePackages(index, version, packagesByOs) {
  const [majorStr, minorStr, patchStr] = version.split(".");
  const major = Number(majorStr);
  const minor = Number(minorStr);
  const patch = Number(patchStr);

  let entry = index.serverVersions.find((e) => e.major === major && e.minor === minor);
  if (!entry) {
    entry = { major, minor, latestPatch: patch, patches: [] };
    index.serverVersions.push(entry);
    index.serverVersions.sort((a, b) => a.major - b.major || a.minor - b.minor);
  }

  let patchGroup = entry.patches.find((group) => Object.prototype.hasOwnProperty.call(group, String(patch)));
  if (!patchGroup) {
    patchGroup = { [String(patch)]: [] };
    entry.patches.push(patchGroup);
    entry.patches.sort((a, b) => Number(Object.keys(a)[0]) - Number(Object.keys(b)[0]));
  }

  const packages = patchGroup[String(patch)];
  for (const pkg of Object.values(packagesByOs)) {
    const existingIndex = packages.findIndex((p) => p.os === pkg.os);
    if (existingIndex >= 0) {
      packages[existingIndex] = pkg;
    } else {
      packages.push(pkg);
    }
  }
  packages.sort((a, b) => a.os.localeCompare(b.os));

  entry.latestPatch = Math.max(entry.latestPatch, patch);
}

async function main() {
  const serverTags = await promptForServerTags();
  if (serverTags.length === 0) {
    console.log("No server tags given; nothing to do.");
    return;
  }

  console.log(`Resolving release tag from ${REPO}@${VERSION_FILE_BRANCH}...`);
  const releaseTag = resolveReleaseTag();
  console.log(`Release tag: ${releaseTag}\n`);

  const index = loadIndex();
  let updated = 0;

  for (const tag of serverTags) {
    console.log(`\n== ${tag} ==`);
    const run = findLatestRun(tag);
    if (!run) {
      console.warn(`  no successful Sandbox Server run found with run-name 'Sandbox Server ${tag}'; skipping.`);
      continue;
    }
    console.log(`  found run ${run.databaseId} (commit ${run.headSha})`);

    const collected = await collectPackages(run, releaseTag);
    if (!collected) continue;

    mergePackages(index, collected.version, collected.packagesByOs);
    updated += 1;
    console.log(`  updated MariaDB ${collected.version} (${Object.keys(collected.packagesByOs).length} package(s))`);
  }

  if (updated === 0) {
    console.log("\nNo tags resolved to a usable run; the index was left unchanged.");
    return;
  }

  fs.writeFileSync(INDEX_PATH, `${JSON.stringify(index, null, 4)}\n`);
  console.log(`\nWrote ${INDEX_PATH}`);
}

main().catch((err) => {
  console.error(`\n${err.message}`);
  process.exitCode = 1;
});

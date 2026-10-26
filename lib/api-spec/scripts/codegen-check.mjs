#!/usr/bin/env node
/**
 * Deterministic contract check for the generated API client.
 *
 * 1. Re-runs the Django schema export and compares it with the committed
 *    `backend/openapi/schema.json` (a stale schema is drift).
 * 2. Snapshots the committed Orval output, regenerates it in place, compares
 *    the result, then restores the committed bytes. Exits non-zero and lists
 *    the changed files when either check drifts.
 *
 * The working tree is restored before exit, so a failed check leaves the
 * repository byte-identical.
 */

import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const specDir = path.resolve(here, "..");
const root = path.resolve(specDir, "..", "..");

const clientGenerated = path.join(root, "lib", "api-client-react", "src", "generated");
const zodGenerated = path.join(root, "lib", "api-zod", "src", "generated");
const committedSchema = path.join(root, "backend", "openapi", "schema.json");

const tmpRoot = path.join(root, ".cache", `codegen-check-${process.pid}`);

function logFailure(message) {
  console.error(`codegen:check FAILED - ${message}`);
}

function normalize(text) {
  return text.replace(/\r\n/g, "\n");
}

function hashFile(filePath) {
  return createHash("sha256").update(normalize(fs.readFileSync(filePath, "utf8"))).digest("hex");
}

function snapshotDir(dir) {
  const snapshot = new Map();
  if (!fs.existsSync(dir)) return snapshot;

  const visit = (current, prefix) => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const absolute = path.join(current, entry.name);
      const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
      if (entry.isDirectory()) {
        visit(absolute, relative);
      } else if (entry.isFile()) {
        snapshot.set(relative, hashFile(absolute));
      }
    }
  };

  visit(dir, "");
  return snapshot;
}

function diffSnapshots(committed, regenerated) {
  const changed = [];
  const allKeys = new Set([...committed.keys(), ...regenerated.keys()]);
  for (const key of [...allKeys].sort()) {
    if (committed.get(key) !== regenerated.get(key)) changed.push(key);
  }
  return changed;
}

function findPython() {
  const candidates = [
    path.join(root, ".venv", "Scripts", "python.exe"),
    path.join(root, ".venv", "bin", "python"),
    path.join(root, "backend", ".venv", "Scripts", "python.exe"),
    path.join(root, "backend", ".venv", "bin", "python"),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) ?? null;
}

function stableStringify(value) {
  if (Array.isArray(value)) {
    return `[${value.map(stableStringify).join(",")}]`;
  }
  if (value && typeof value === "object") {
    const keys = Object.keys(value).sort();
    return `{${keys.map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function checkSchemaFreshness() {
  const python = findPython();
  if (!python) {
    console.warn(
      "codegen:check WARNING - no repo virtualenv found; skipping the schema freshness check.",
    );
    return true;
  }

  const generated = path.join(tmpRoot, "schema.json");
  try {
    execFileSync(python, ["manage.py", "export_schema", "--out", generated], {
      cwd: path.join(root, "backend"),
      stdio: "pipe",
    });
  } catch (error) {
    const detail = `${error.stdout ?? ""}${error.stderr ?? ""}`.trim() || error.message;
    logFailure(`manage.py export_schema failed:\n${detail}`);
    return false;
  }

  const expected = JSON.parse(fs.readFileSync(committedSchema, "utf8"));
  const actual = JSON.parse(fs.readFileSync(generated, "utf8"));
  if (stableStringify(expected) !== stableStringify(actual)) {
    logFailure(
      "backend/openapi/schema.json is stale; run " +
        "`cd backend && ..\\.venv\\Scripts\\python.exe manage.py export_schema --out openapi/schema.json`",
    );
    return false;
  }

  return true;
}

function resolveOrvalBin() {
  const require = createRequire(import.meta.url);
  let orvalDir;
  try {
    orvalDir = path.dirname(require.resolve("orval/package.json"));
  } catch {
    orvalDir = path.join(specDir, "node_modules", "orval");
  }

  const manifest = JSON.parse(fs.readFileSync(path.join(orvalDir, "package.json"), "utf8"));
  const bin = typeof manifest.bin === "string" ? manifest.bin : manifest.bin?.orval;
  if (!bin) throw new Error("could not resolve the orval CLI entry point.");
  return path.resolve(orvalDir, bin);
}

function runOrval() {
  execFileSync(
    process.execPath,
    [resolveOrvalBin(), "--config", path.join(specDir, "orval.config.ts")],
    { cwd: specDir, stdio: "pipe" },
  );
}

function restoreDir(backup, target) {
  fs.rmSync(target, { recursive: true, force: true });
  if (fs.existsSync(backup)) {
    fs.cpSync(backup, target, { recursive: true });
  }
}

const backupClient = path.join(tmpRoot, "backup-client");
const backupZod = path.join(tmpRoot, "backup-zod");

let drift = null;
let failure = null;

try {
  fs.rmSync(tmpRoot, { recursive: true, force: true });
  fs.mkdirSync(tmpRoot, { recursive: true });

  if (!checkSchemaFreshness()) {
    failure = "schema";
    throw new Error("schema drift");
  }

  const beforeClient = snapshotDir(clientGenerated);
  const beforeZod = snapshotDir(zodGenerated);
  fs.cpSync(clientGenerated, backupClient, { recursive: true });
  fs.cpSync(zodGenerated, backupZod, { recursive: true });

  try {
    runOrval();
  } catch (error) {
    const detail = `${error.stdout ?? ""}${error.stderr ?? ""}`.trim() || error.message;
    failure = "orval";
    throw new Error(`orval failed:\n${detail}`);
  }

  drift = {
    client: diffSnapshots(beforeClient, snapshotDir(clientGenerated)),
    zod: diffSnapshots(beforeZod, snapshotDir(zodGenerated)),
  };
} catch (error) {
  if (!failure) failure = "run";
  if (failure !== "schema" && !drift) {
    logFailure(error.message);
  }
} finally {
  if (fs.existsSync(backupClient)) restoreDir(backupClient, clientGenerated);
  if (fs.existsSync(backupZod)) restoreDir(backupZod, zodGenerated);
  fs.rmSync(tmpRoot, { recursive: true, force: true });
}

if (failure) {
  process.exit(1);
}

if (drift.client.length > 0 || drift.zod.length > 0) {
  const lines = [
    "codegen:check FAILED - generated client differs from the committed Django schema.",
    ...(drift.client.length
      ? [`lib/api-client-react/src/generated: ${drift.client.join(", ")}`]
      : []),
    ...(drift.zod.length ? [`lib/api-zod/src/generated: ${drift.zod.join(", ")}`] : []),
    "Run `pnpm run codegen` and commit the result.",
  ];
  console.error(lines.join("\n"));
  process.exit(1);
}

console.log("codegen:check OK - schema and generated client are up to date.");

#!/usr/bin/env node
// Deterministic, verified release of TIRE Desk to the isolated STAGING Railway service.
//
//   node scripts/release-staging.mjs --dry-run            verify everything, deploy nothing
//   node scripts/release-staging.mjs [--commit <sha>]     verify, then deploy that commit's desk/ tree
//
// What it guarantees (every check must pass before anything is uploaded):
//  1. The commit is a real commit, is published on origin/<staging branch>, and the
//     local desk/ working tree has no uncommitted changes.
//  2. The uploaded tree is exactly `git archive <sha>:desk` - file list and per-file
//     hashes are re-derived from git and compared; no engine/Python/env/dist files.
//  3. The target is the staging project/service/environment named in release/staging.json,
//     confirmed by name through the Railway API. Every railway command carries explicit
//     IDs and runs from an unlinked temp directory, so the CLI's "linked project" (which
//     could be production) is never consulted. The production TIRE project cannot be targeted.
//  4. Required service variables exist and forbidden ones (proxy secret, Telegram, offline
//     mode...) do not. Only variable NAMES (and two non-secret values) are inspected; values
//     are never printed.
//  5. After upload it waits for the new deployment to reach SUCCESS (the /api/health gate),
//     then checks /api/health and records source SHA + manifest hash + deployment ID.
//
// Exit code 0 only if the deployment is SUCCESS and healthy.

import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { appendFileSync, mkdirSync, mkdtempSync, readFileSync, readdirSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const deskDir = path.resolve(here, '..');
const target = JSON.parse(readFileSync(path.join(deskDir, 'release', 'staging.json'), 'utf8'));
const args = process.argv.slice(2);
const dryRun = args.includes('--dry-run');
const commitArg = args.includes('--commit') ? args[args.indexOf('--commit') + 1] : 'HEAD';

const sh = (cmd, a, opts = {}) => execFileSync(cmd, a, { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe'], ...opts });
const git = (...a) => sh('git', ['-C', deskDir, ...a]).trim();
// ls-tree/show/archive paths are repository-relative; run them from the top level so the cwd prefix cannot filter them.
const repoRoot = git('rev-parse', '--show-toplevel');
const gitRoot = (...a) => sh('git', ['-C', repoRoot, ...a]).trim();
const fail = (m) => {
  console.error(`RELEASE REFUSED: ${m}`);
  process.exit(1);
};
const ok = (m) => console.log(`  ok  ${m}`);

console.log(`TIRE Desk staging release${dryRun ? ' (dry run)' : ''}`);

// ---- 1. source commit ------------------------------------------------------------------------------------------------
let sha;
try {
  sha = git('rev-parse', '--verify', `${commitArg}^{commit}`);
} catch {
  fail(`"${commitArg}" is not a commit.`);
}
git('fetch', 'origin', target.gitBranch);
try {
  git('merge-base', '--is-ancestor', sha, `origin/${target.gitBranch}`);
} catch {
  fail(`${sha.slice(0, 12)} is not published on origin/${target.gitBranch}. Push it first (fast-forward) so the deployed source is reviewable.`);
}
const dirty = git('status', '--porcelain', '--untracked-files=no', '--', '.');
if (dirty) fail(`uncommitted tracked changes under desk/:\n${dirty}`);
ok(`commit ${sha.slice(0, 12)} is published on origin/${target.gitBranch}; desk/ tree is clean`);

// ---- 2. exact archive ------------------------------------------------------------------------------------------------
const prefix = git('rev-parse', '--show-prefix').replace(/\/$/, ''); // "desk" when run in the engine repo
const treeRef = `${sha}:${prefix}`;
const expectedFiles = gitRoot('ls-tree', '-r', '--name-only', treeRef).split('\n').filter(Boolean).sort();
const forbidden = expectedFiles.filter((f) => /\.py$|^pyproject\.toml$|^requirements.*\.txt$|(^|\/)Dockerfile$|(^|\/)\.env(?!\.example$)|^node_modules\/|^dist\/|^dist-server\//.test(f));
if (forbidden.length) fail(`forbidden files in the release tree: ${forbidden.slice(0, 5).join(', ')}`);
for (const need of ['package.json', 'package-lock.json', 'railway.json', 'server/main.ts', 'server/static.ts']) {
  if (!expectedFiles.includes(need)) fail(`required file missing from the release tree: ${need}`);
}
const pkg = JSON.parse(sh('git', ['-C', repoRoot, 'show', `${treeRef}/package.json`]));
if (pkg.name !== 'tire-desk') fail(`package name is "${pkg.name}", expected tire-desk (wrong tree?)`);

const work = mkdtempSync(path.join(os.tmpdir(), `desk-release-${sha.slice(0, 8)}-`));
const tar = path.join(work, 'src.tar');
const tree = path.join(work, 'desk');
mkdirSync(tree);
sh('git', ['-C', repoRoot, 'archive', '--format=tar', '-o', tar, treeRef]);
sh('tar', ['-xf', tar, '-C', tree]);

const walk = (d, rel = '') =>
  readdirSync(d, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(path.join(d, e.name), `${rel}${e.name}/`) : [`${rel}${e.name}`]));
const gotFiles = walk(tree).sort();
if (JSON.stringify(gotFiles) !== JSON.stringify(expectedFiles)) fail('extracted file list differs from git ls-tree');
const manifest = createHash('sha256');
for (const f of gotFiles) {
  const blob = createHash('sha256').update(readFileSync(path.join(tree, f))).digest('hex');
  const fromGit = createHash('sha256').update(sh('git', ['-C', repoRoot, 'show', `${treeRef}/${f}`], { encoding: 'buffer' })).digest('hex');
  if (blob !== fromGit) fail(`content mismatch for ${f}`);
  manifest.update(`${f}\0${blob}\n`);
}
const manifestHash = manifest.digest('hex');
ok(`archive = git tree: ${gotFiles.length} files, manifest ${manifestHash.slice(0, 16)}`);

// ---- 3. target ---------------------------------------------------------------------------------------------------------
const R = target.railway;
const rw = (a, opts = {}) => sh('railway', a, { cwd: work, ...opts });
const q = JSON.parse(
  rw(['api', `{ project(id:"${R.projectId}") { name services { edges { node { id name } } } volumes { edges { node { name } } } environments { edges { node { id name } } } } }`]),
).data?.project;
if (!q || q.name !== R.projectName) fail(`project ${R.projectId} is "${q?.name}", expected "${R.projectName}"`);
if (!q.services.edges.some((e) => e.node.id === R.serviceId && e.node.name === R.serviceName)) fail('service id/name does not match release/staging.json');
if (!q.environments.edges.some((e) => e.node.id === R.environmentId)) fail('environment id does not match release/staging.json');
if (!q.volumes.edges.some((e) => e.node.name === R.volumeName)) fail(`volume ${R.volumeName} not found in the staging project`);
if (!/staging/i.test(q.name) || /^tire$/i.test(q.name)) fail('target project is not a staging project');
ok(`target "${q.name}" / ${R.serviceName} / environment ${R.environmentId.slice(0, 8)} with volume ${R.volumeName}`);

const ids = ['-p', R.projectId, '-s', R.serviceId, '-e', R.environmentId];
const vars = JSON.parse(rw(['variable', 'list', ...ids, '--json']));
const names = new Set(Object.keys(vars));
for (const n of target.requiredVariables) if (!names.has(n)) fail(`required variable missing on the service: ${n}`);
for (const n of target.forbiddenVariables) if (names.has(n)) fail(`forbidden variable present on the service: ${n}`);
for (const [k, v] of Object.entries(target.expectedValues)) if (vars[k] !== v) fail(`variable ${k} does not have its expected value`);
if (!/build:web/.test(vars.RAILPACK_BUILD_CMD) || !/build:server/.test(vars.RAILPACK_BUILD_CMD)) fail('RAILPACK_BUILD_CMD must run build:web and build:server');
if (new URL(vars.DESK_PUBLIC_ORIGIN).origin !== R.origin) fail('DESK_PUBLIC_ORIGIN is not the staging API origin');
ok('service variables: required present, forbidden absent (names only inspected)');

const label = `desk@${sha.slice(0, 12)} manifest:${manifestHash.slice(0, 12)}`;
if (dryRun) {
  console.log(`DRY RUN COMPLETE: would deploy ${label} to ${R.projectName}/${R.serviceName}. Nothing uploaded.`);
  process.exit(0);
}

// ---- 4. deploy and wait ----------------------------------------------------------------------------------------------
const before = new Set(JSON.parse(rw(['deployment', 'list', ...ids, '--limit', '10', '--json'])).map((d) => d.id));
rw(['up', tree, '--path-as-root', '--detach', ...ids, '-m', label], { stdio: ['ignore', 'pipe', 'pipe'] });
console.log(`  uploaded ${label}; waiting for the deployment`);
let dep = null;
const deadline = Date.now() + 12 * 60_000;
while (Date.now() < deadline) {
  await new Promise((r) => setTimeout(r, 10_000));
  const list = JSON.parse(rw(['deployment', 'list', ...ids, '--limit', '10', '--json']));
  dep = list.find((d) => !before.has(d.id)) ?? null;
  if (dep && ['SUCCESS', 'FAILED', 'CRASHED', 'REMOVED'].includes(dep.status)) break;
}
if (!dep || dep.status !== 'SUCCESS') fail(`deployment ${dep?.id ?? '(none appeared)'} ended as ${dep?.status ?? 'unknown'}. Inspect: railway logs ${ids.join(' ')} --build`);
const health = await fetch(`${R.origin}/api/health`).then((r) => r.status, () => 0);
if (health !== 200) fail(`deployment ${dep.id} is SUCCESS but /api/health returned ${health}`);

const record = { at: new Date().toISOString(), sha, manifest: manifestHash, files: gotFiles.length, deploymentId: dep.id, project: R.projectName, service: R.serviceName, health };
const logDir = path.join(os.homedir(), '.tire-desk-releases');
mkdirSync(logDir, { recursive: true });
appendFileSync(path.join(logDir, 'staging.jsonl'), `${JSON.stringify(record)}\n`);
console.log(`RELEASED: ${JSON.stringify(record)}`);

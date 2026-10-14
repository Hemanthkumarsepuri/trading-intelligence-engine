// Operator tool for the off-site encrypted backups. Run with: npx tsx scripts/offsite-restore.ts <command>
//
//   list                          list remote backup objects (names only)
//   verify                        fetch the newest object, decrypt and validate it (writes nothing)
//   restore <latest|objectKey> <newEmptyDir>
//                                 rebuild a Desk data directory from the remote copy
//
// Needs BACKUP_S3_ENDPOINT, BACKUP_S3_BUCKET, BACKUP_S3_ACCESS_KEY_ID, BACKUP_S3_SECRET_ACCESS_KEY
// and BACKUP_ENCRYPTION_KEY in the environment (never on the command line). Prints counts and
// object names only; never prints credentials or backup contents.

import { loadConfig, type Ctx } from '../server/config';
import { MemoryKV } from '../server/kv';
import { S3Client, restoreOffsiteInto, verifyLatestOffsite } from '../server/services/offsite';

async function main(): Promise<void> {
  const [cmd, a, b] = process.argv.slice(2);
  const cfg = loadConfig();
  if (!cfg.offsite) {
    console.error(cfg.offsiteProblems.length ? cfg.offsiteProblems.join('\n') : 'Off-site backup variables are not set.');
    process.exit(2);
  }
  const ctx: Ctx = { cfg, kv: new MemoryKV(), now: () => new Date() };
  if (cmd === 'list') {
    for (const k of await new S3Client(cfg.offsite).list(`${cfg.offsite.prefix}daily/`)) console.log(k);
  } else if (cmd === 'verify') {
    const v = await verifyLatestOffsite(ctx);
    console.log(JSON.stringify(v, null, 2));
    process.exit(v.ok ? 0 : 1);
  } else if (cmd === 'restore' && a && b) {
    const r = await restoreOffsiteInto(ctx, a, b);
    console.log(JSON.stringify(r, null, 2));
    console.log('Next: start a Desk instance on this directory, rotate SESSION_SECRET, and verify before pointing users at it. See OFFSITE_BACKUP_AND_RECOVERY.md.');
  } else {
    console.error('Usage: offsite-restore.ts list | verify | restore <latest|objectKey> <newEmptyDir>');
    process.exit(2);
  }
}
main().catch((e) => {
  console.error(e instanceof Error ? e.message : 'failed');
  process.exit(1);
});

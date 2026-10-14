// Production entry point: the TIRE Desk API + scheduler, run as its own
// Railway service (separate from the TIRE research engine service).
//
//   node dist-server/main.mjs        (built by `npm run build:server`)
//
// Refuses to start on unsafe production configuration (e.g. no volume path,
// demo data, missing proxy secret): a failed health check keeps the previous
// deployment serving instead of silently running a broken one.

import { randomUUID } from 'node:crypto';
import { promises as fs } from 'node:fs';
import { createServer } from 'node:http';
import path from 'node:path';
import { configProblems, fatalProductionProblems, getKV, loadConfig, storageLayoutProblems, type Ctx } from './config';
import { createHandler } from './node-http';
import { Scheduler } from './scheduler';

async function main(): Promise<void> {
  const cfg = loadConfig();
  const fatal = fatalProductionProblems(cfg);
  if (fatal.length > 0) {
    console.error(`[desk] refusing to start:\n - ${fatal.join('\n - ')}`);
    process.exit(1);
  }
  for (const p of configProblems(cfg)) console.warn(`[desk] config: ${p}`);

  if (cfg.storage === 'file') {
    if (cfg.production) {
      const siblings = await fs.readdir(path.dirname(cfg.dataDir)).catch(() => [] as string[]);
      const layout = storageLayoutProblems(cfg.dataDir, siblings);
      if (layout.length > 0) {
        console.error(`[desk] refusing to start:\n - ${layout.join('\n - ')}`);
        process.exit(1);
      }
    }
    await fs.mkdir(cfg.dataDir, { recursive: true });
    // Prove the volume is writable before accepting traffic.
    const probe = `${cfg.dataDir}/.write-probe`;
    await fs.writeFile(probe, new Date().toISOString());
    await fs.unlink(probe);
  }
  if (cfg.webRoot) {
    const index = path.resolve(process.cwd(), cfg.webRoot, 'index.html');
    if (!(await fs.stat(index).then((st) => st.isFile(), () => false))) {
      console.error(`[desk] refusing to start: DESK_INGRESS=railway serves the frontend but ${index} does not exist (run "npm run build:web" in the build command).`);
      process.exit(1);
    }
  }
  const kv = await getKV(cfg);
  const ctx: Ctx = { cfg, kv, now: () => new Date() };

  const port = Number(process.env.PORT ?? 8788);
  const server = createServer(createHandler(async () => ctx, {
      requireProxyKey: cfg.production && cfg.ingress === 'edge-proxy',
      trustPlatformForwardedFor: cfg.ingress === 'railway',
      webRoot: cfg.webRoot ? path.resolve(process.cwd(), cfg.webRoot) : null,
    }));
  server.requestTimeout = 60_000;
  server.headersTimeout = 30_000;

  const instance = `${process.env.RAILWAY_DEPLOYMENT_ID ?? 'local'}:${randomUUID().slice(0, 8)}`;
  const scheduler = cfg.schedulerEnabled ? new Scheduler(ctx, instance) : null;

  server.listen(port, () => {
    console.log(`[desk] API listening on :${port} (storage=${cfg.storage} dir=${cfg.storage === 'file' ? cfg.dataDir : '-'} scheduler=${scheduler ? 'on' : 'off'} offline=${cfg.offline} ingress=${cfg.ingress} web=${cfg.webRoot ? 'on' : 'off'} offsite=${cfg.offsite ? 'on' : 'off'})`);
  });
  if (scheduler) await scheduler.start();

  let closing = false;
  const shutdown = (signal: string) => {
    if (closing) return;
    closing = true;
    console.log(`[desk] ${signal}: draining`);
    const hard = setTimeout(() => process.exit(0), 20_000);
    hard.unref();
    server.close();
    void (scheduler?.stop() ?? Promise.resolve()).finally(() => process.exit(0));
  };
  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));
}

main().catch((e) => {
  console.error('[desk] fatal', e);
  process.exit(1);
});

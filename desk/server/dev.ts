// Local API server for development: same router, adapter and storage as
// production. Usage: npm run dev:api  (DESK_OFFLINE=1 for synthetic demo data)
// The scheduler is off unless DESK_SCHEDULER=on.

import { createServer } from 'node:http';
import { getKV, loadConfig, type Ctx } from './config';
import { createHandler } from './node-http';
import { Scheduler } from './scheduler';

const port = Number(process.env.PORT ?? 8788);
const cfg = loadConfig();
const ctxP: Promise<Ctx> = getKV(cfg).then((kv) => ({ cfg, kv, now: () => new Date() }));

createServer(createHandler(() => ctxP, { requireProxyKey: Boolean(cfg.proxySecret) && process.env.DESK_REQUIRE_PROXY === '1' })).listen(port, () =>
  console.log(`TIRE Desk API on http://localhost:${port}  (offline demo: ${cfg.offline}, scheduler: ${cfg.schedulerEnabled})`),
);
if (cfg.schedulerEnabled) void ctxP.then((ctx) => new Scheduler(ctx, 'dev').start());

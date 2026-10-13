// Runs the local API and the Vite dev server together.
import { spawn } from 'node:child_process';

const procs = [
  spawn('npx', ['tsx', 'server/dev.ts'], { stdio: 'inherit', shell: true, env: process.env }),
  spawn('npx', ['vite'], { stdio: 'inherit', shell: true, env: process.env }),
];
const stop = () => procs.forEach((p) => p.kill());
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
procs.forEach((p) => p.on('exit', (code) => { stop(); process.exit(code ?? 0); }));

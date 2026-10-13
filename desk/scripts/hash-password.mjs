#!/usr/bin/env node
// Usage: npm run hash-password            (prompts, input hidden)
//        npm run hash-password -- 'pass'  (not recommended: lands in shell history)
import { pbkdf2, randomBytes } from 'node:crypto';
import { promisify } from 'node:util';
import readline from 'node:readline';

const pbkdf2Async = promisify(pbkdf2);
const ITER = 600_000;

async function ask(prompt) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: true });
    const write = rl._writeToOutput;
    rl._writeToOutput = (s) => { if (s.includes(prompt)) write.call(rl, s); };
    rl.question(prompt, (answer) => { rl.close(); process.stdout.write('\n'); resolve(answer); });
  });
}

let pw = process.argv[2];
if (!pw) {
  pw = await ask('New password (min 12 characters): ');
  const again = await ask('Repeat password: ');
  if (pw !== again) { console.error('Passwords do not match.'); process.exit(1); }
}
if (pw.length < 12) { console.error('Use at least 12 characters (a passphrase of 4 random words is ideal).'); process.exit(1); }

const salt = randomBytes(16);
const dk = await pbkdf2Async(pw, salt, ITER, 32, 'sha256');
const hash = `pbkdf2$sha256$${ITER}$${salt.toString('base64url')}$${dk.toString('base64url')}`;
console.log('\nAPP_PASSWORD_HASH=' + hash);
console.log('\nSESSION_SECRET=' + randomBytes(32).toString('hex'));
console.log('\nAdd both as variables on the Railway service tire-desk-api (Variables tab). Never put them on Netlify.');
console.log('Keep the password itself out of every file and chat.');

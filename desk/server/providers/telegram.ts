// Telegram Bot API (https://core.telegram.org/bots/api), server-side only.
//
// The bot token is part of the request URL, so nothing in this module ever
// puts a URL, the token, or a raw upstream error into a message: failures are
// reduced to Telegram's own `description` plus a category.

export class TelegramError extends Error {
  constructor(
    public kind: 'NOT_CONFIGURED' | 'RATE_LIMITED' | 'UNAUTHORIZED' | 'BAD_REQUEST' | 'TIMEOUT' | 'NETWORK' | 'UPSTREAM',
    message: string,
    /** seconds Telegram asked us to wait (429) */
    public retryAfterSec: number | null = null,
  ) {
    super(message);
    this.name = 'TelegramError';
  }
}

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

export function escapeHtml(s: string): string {
  return esc(s);
}

interface TgResponse<T> {
  ok?: boolean;
  result?: T;
  description?: string;
  error_code?: number;
  parameters?: { retry_after?: number };
}

const API = 'https://api.telegram.org';
const TIMEOUT_MS = 8000;
/** Wait in-line for a 429 only when Telegram asks for a short pause. */
const MAX_INLINE_RETRY_SEC = 5;

async function call<T>(token: string, method: string, body: unknown, fetchImpl: typeof fetch = fetch, sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))): Promise<T> {
  if (!/^\d+:[A-Za-z0-9_-]{20,}$/.test(token)) throw new TelegramError('NOT_CONFIGURED', 'TELEGRAM_BOT_TOKEN does not look like a bot token (expected "123456:ABC...").');
  for (let attempt = 0; attempt < 2; attempt++) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
    let res: Response;
    try {
      res = await fetchImpl(`${API}/bot${token}/${method}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body ?? {}),
        signal: ctl.signal,
      });
    } catch (e) {
      clearTimeout(timer);
      const aborted = e instanceof Error && /abort/i.test(e.name + e.message);
      throw aborted ? new TelegramError('TIMEOUT', `Telegram did not respond within ${TIMEOUT_MS / 1000}s.`) : new TelegramError('NETWORK', 'Could not reach api.telegram.org.');
    }
    clearTimeout(timer);
    let json: TgResponse<T> | null = null;
    try {
      json = (await res.json()) as TgResponse<T>;
    } catch {
      json = null;
    }
    const desc = (json?.description ?? `HTTP ${res.status}`).slice(0, 200);
    if (res.ok && json?.ok) return json.result as T;
    if (res.status === 429) {
      const wait = json?.parameters?.retry_after ?? null;
      if (attempt === 0 && wait !== null && wait <= MAX_INLINE_RETRY_SEC) {
        await sleep(wait * 1000);
        continue;
      }
      throw new TelegramError('RATE_LIMITED', `Telegram rate limit: ${desc}`, wait);
    }
    if (res.status === 401 || res.status === 404) throw new TelegramError('UNAUTHORIZED', 'Telegram rejected the bot token (unauthorized). Check TELEGRAM_BOT_TOKEN.');
    if (res.status === 400 || res.status === 403) throw new TelegramError('BAD_REQUEST', `Telegram refused the message: ${desc}. Check TELEGRAM_CHAT_ID and that you have sent /start to the bot.`);
    throw new TelegramError('UPSTREAM', `Telegram error: ${desc}`);
  }
  throw new TelegramError('RATE_LIMITED', 'Telegram rate limit persisted after waiting.');
}

export interface BotInfo {
  id: number;
  username?: string;
  first_name?: string;
}

/** Connection test without sending anything: validates the token. */
export async function telegramGetMe(token: string, fetchImpl?: typeof fetch): Promise<BotInfo> {
  const me = await call<BotInfo | undefined>(token, 'getMe', {}, fetchImpl);
  if (!me || typeof me.id !== 'number') throw new TelegramError('UPSTREAM', 'Telegram returned an unexpected getMe response.');
  return me;
}

export async function sendTelegram(token: string, chatId: string, text: string, fetchImpl?: typeof fetch, sleep?: (ms: number) => Promise<void>): Promise<void> {
  await call<unknown>(token, 'sendMessage', { chat_id: chatId, text: text.slice(0, 3900), parse_mode: 'HTML', disable_web_page_preview: true }, fetchImpl, sleep);
}

# Telegram setup for TIRE Desk

Alerts, the pre-market brief and the end-of-day summary are sent by the **Railway API service** through the official Telegram Bot API (https://core.telegram.org/bots/api). The bot token is a password for your bot:

- Keep it only in the Railway service variables.
- Never put it in Netlify, in the website, in a chat message (including with an assistant), in a screenshot or in git.

## 1. Create the bot (in the Telegram app)

1. Open a chat with **@BotFather** (check for the blue verified tick).
2. Send `/newbot`, then choose a display name and a username ending in `bot`.
3. BotFather replies with the **bot token**, which looks like `123456789:AA…`. Copy it straight into Railway (step 3). Do not paste it anywhere else.

## 2. Find your chat id

1. In Telegram, open your new bot and press **Start** (or send `/start`). A bot cannot message you until you have done this.
2. On your own computer, open this address in **your own browser**, replacing `<TOKEN>` with your token:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. In the JSON, find `"chat":{"id":…`. That number is your chat id. Private chats have a positive number; groups have a negative one.
4. Close the tab and clear the address from your browser history if the computer is shared.

## 3. Configure Railway (the only place these values go)

In Railway → project → `tire-desk-api` service → **Variables**, add:

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the token from BotFather |
| `TELEGRAM_CHAT_ID` | the number from step 2 |

Railway redeploys the service. Nothing changes on Netlify.

## 4. Test it yourself

TIRE Desk → **Settings → Telegram alerts → Send test message**. Pressing that button:

- checks the token with `getMe`,
- sends one message to your chat ("TIRE Desk test message, requested by you at HH:MM:SS IST"),
- records the result, which the card then shows as the last successful send.

The test can be sent at most once every 30 seconds. Nothing is ever sent automatically during setup or testing. Settings → Run self-test only checks the token with `getMe` and sends nothing.

## How delivery works

- **Exactly one fire per alert.** An alert is claimed with a compare-and-set before anything is sent. The event is written to the log first, then sent, then marked sent or failed.
- **Retries.** Failed notifications are retried up to **3 attempts within 30 minutes**. Each attempt is leased first, so overlapping runs never send the same event twice. A crash in the instant after Telegram accepted a message, but before the success was recorded, can cause one duplicate. That is the only known duplicate path.
- **Rate limits.** If Telegram answers 429 with a short `retry_after` (5 s or less), the send waits and retries once. Otherwise the event is retried after the time Telegram asked for, and never sooner than 60 s. Messages are spaced about 1 per second per chat.
- **Stale data never alerts.** Price, day-change and VIX alerts are skipped while the market is live if the quote's exchange timestamp is older than 5 minutes. RSI and volume alerts are skipped if the daily history is a stale cached copy. F&O-ban alerts are skipped unless the ban file is dated for today. Skips appear under Alerts → Last scheduled check.
- **Times.** Every message and log entry is shown in Asia/Kolkata time.
- **Status.** Settings → Telegram alerts shows the last successful send, the last test and the 10 most recent failures (category and Telegram's own description; never the token).

## Common errors

| Shown | Meaning | Fix |
|---|---|---|
| `UNAUTHORIZED` | the token was rejected | copy the token again from BotFather (`/mybots` → API Token); replace the Railway variable |
| `BAD_REQUEST … chat not found` | wrong chat id, or you never pressed Start | repeat step 2 |
| `RATE_LIMITED` | Telegram throttled the bot | wait; retries are automatic |
| `TIMEOUT` / `NETWORK` | Telegram unreachable from Railway | automatic retries; check https://status.telegram.org |
| `Not configured` | variables missing on Railway | step 3 |

## Rotating the token

1. In BotFather, `/revoke` for the bot to get a new token.
2. Replace `TELEGRAM_BOT_TOKEN` on Railway.
3. Send a test message.

The old token stops working immediately.

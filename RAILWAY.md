# Deploying the live demo to Railway

The live telehealth co-pilot server (`server/`) runs as a persistent process. Railway is
a good fit (HTTPS by default — required for the browser mic/camera — and WebSockets work
out of the box). The repo already contains everything Railway needs:

- `nixpacks.toml` — pins Python 3.12, installs `server/requirements.txt`, runs uvicorn
- `railway.json` — `/healthz` healthcheck + restart-on-failure
- `Procfile` — start command (`uvicorn server.app:app --host 0.0.0.0 --port $PORT`)

## One-time setup (Railway dashboard)

1. **Create the project** — [railway.app](https://railway.app) → **New Project** →
   **Deploy from GitHub repo** → pick `amendezp/testBrioCare` (authorize Railway for the
   repo if prompted). Railway detects `nixpacks.toml` and builds automatically.

2. **Add API keys** — service → **Variables** → **New Variable** (each save redeploys):
   | Variable | Enables | Get it from |
   |---|---|---|
   | `DAILY_API_KEY` | the human video/audio call | [dashboard.daily.co](https://dashboard.daily.co) → Developers → API keys |
   | `ANTHROPIC_API_KEY` | AI session notes + shared-prompt wording | [console.anthropic.com](https://console.anthropic.com) → API keys |

   Both are optional — without them the app still runs (no video; notes show the raw
   transcript) — but you'll want both for a full demo.

3. **Get a public URL** — service → **Settings → Networking** → **Generate Domain**.
   You'll get `https://<name>.up.railway.app`.

## Verify

- `https://<domain>/healthz` → `{"ok":true,"notes":true,"video":true}`
  (`notes`/`video` are `true` only when the matching key is set).
- `https://<domain>/` → the role chooser.

## Run a session

The therapist and the child each open `https://<domain>/` **on their own device**, pick
their role, and enter the **same session code** (default `demo`). The therapist clicks
**Start**. Transcription is **Chrome/Edge only** (browser Web Speech); the Daily video
call works in any browser Daily supports.

## Redeploys

Railway auto-deploys on every push to `main`. To change the activities, edit
`server/scripts/solo_checkin.yaml` and push.

## Notes on cost

- **Daily** and **Anthropic** both have free tiers sufficient for demos.
- **Railway** gives a small monthly credit, then bills usage for an always-on service.
- The Anthropic note-taker uses `claude-sonnet-4-6`, debounced (a refresh every ~6
  utterances + one summary at the end), so token usage per session is modest.

# Academic Countdown & Study Planner

Local, zero-hosting Python app for **Windows and Mac** built for the Handshake *"Your Deadline, Your Design: Create a Countdown Lock Screen"* challenge.

## Architecture
- **Google Calendar** — Truth for time (classes, plans, blocked days).
- **Google Tasks** — Truth of progress (one task per study day; completion stays synced across Windows and Mac).
- **Display (HTML Page + Headless Screenshot)** — Ephemeral derived view rebuilt anytime from Calendar + Tasks; powers live ticking desktop wallpapers (**Lively Wallpaper / Wallpaper Engine** on Windows, **Plash** on Mac) and static lock screen screenshots refreshed every morning and after every replan.
- **Pure Python Planner** — Deterministic capacity rules (`free = 1.0`, `class <= 3.5h = 0.25`, `blocked = 0.0`) with zero LLM arithmetic.
- **Gemini (Google AI Studio API)** — Strictly two jobs: parse plain language into Calendar events / Task ticks, and explain the newly computed plan in words.

## Slice 1 Setup (Login & Calendar Read)
1. In Google Cloud Console, enable **Google Calendar API** and **Google Tasks API**, create an **OAuth 2.0 Client ID (Desktop App)**, and save it **outside the repo** at:
   - Windows: `%USERPROFILE%\.config\countdown-planner\credentials.json`
   - Mac: `~/.config/countdown-planner/credentials.json`
   *(Optional: override the folder with `COUNTDOWN_SECRETS_DIR`.)*
2. Activate `.venv` and install dependencies:
   - Windows: `.venv\Scripts\pip install -r requirements.txt`
   - Mac: `.venv/bin/pip install -r requirements.txt`
3. Run Slice 1 verification from your own terminal (approves both Calendar and Tasks scopes in a single browser login, writes `token.json` next to `credentials.json`, and prints your next 10 Calendar events):
   ```bash
   .venv/Scripts/python cli.py check-calendar
   ```

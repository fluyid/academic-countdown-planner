# Academic Countdown & Study Planner

Local, zero-hosting Python app for **Windows and Mac** built for the Handshake *"Your Deadline, Your Design: Create a Countdown Lock Screen"* challenge.

## How It Works
- **Google Calendar** — Truth for time across your personal (`"Kai Nath"`) and university (`"IU"`) calendars.
- **Google Tasks** — Truth of progress (one task per study day; completion syncs across Windows and Mac).
- **Pure Python Planner** — Deterministic capacity rules (`free = 1.0u`, `class <= 3.5h = 0.25u`, `blocked = 0.0u`) with zero LLM arithmetic. Under load, optional paper reading drops first so exam units stay protected.
- **Single-Page Display** — Ephemeral HTML (`output/index.html`) over a Gemini-generated cyberpunk night-city background (`assets/background.jpg`) with a dark glassmorphic HUD (`Cyberway Riders`, `Orbitron`, and `Rajdhani` bundled in `assets/fonts/`), Windows and macOS lock-screen safe zones, and concrete dates (no `T-minus` labels).
  - **Live desktop wallpaper** (ticking seconds): **Lively Wallpaper** / **Wallpaper Engine** on Windows, **Plash** on Mac.
  - **Static lock screen** (days left + today's task): headless Chromium screenshot (`output/lockscreen.png`) refreshed every morning and after changes.
- **Gemini (`cli.py say`)** — Strictly two jobs: (1) parse plain language into a Calendar event or an exact Google Task tick (echoing what it ticked), and (2) explain the pure-Python recomputed diff. Never called from scheduled refreshes; capped at 25 calls/day on Flash-only models with zero retries.

---

## Setup (Windows & Mac)

### 1. Python environment
Requires Python 3.11+ and a Chromium-based browser (Chrome, Edge, or Brave) for static lock-screen screenshots.

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

**Mac (Terminal):**
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Secrets (strictly outside the repo)
Store all OAuth credentials, tokens, and API keys outside this repository in:
- **Windows:** `%USERPROFILE%\.config\countdown-planner\`
- **Mac:** `~/.config/countdown-planner/`
*(Override the folder with `COUNTDOWN_SECRETS_DIR` if needed.)*

1. **Google Calendar & Tasks OAuth (free — no billing account needed):**
   In Google Cloud Console, enable **Google Calendar API** and **Google Tasks API**, create an **OAuth 2.0 Client ID (Desktop App)**, and save the JSON as `credentials.json` inside `~/.config/countdown-planner/`.
   The first CLI run opens one browser login approving both scopes and saves `token.json` next to it.
2. **Gemini API key (only required for `cli.py say`):**
   Create `~/.config/countdown-planner/.env` with:
   ```env
   GEMINI_API_KEY=your_google_ai_studio_key
   ```
   *(Local daily call counts are tracked next to it in `gemini_usage.json`, hard-capped at 25/day.)*

### 3. Configure targets & calendars
Edit [`config/targets.json`](config/targets.json) to set your calendar names, Google Tasks list name, exam/assignment dates (set `"date": null` until published), and study units.

---

## Usage

Replace `<python>` with `.venv\Scripts\python` on **Windows** or `.venv/bin/python` on **Mac**.

| Step | Command | What it does |
| :--- | :--- | :--- |
| **1. Verify login & calendars** | `<python> cli.py check-calendar` | Single OAuth login; prints next 10 merged events (`-n N`). |
| **2. Preview study plan** | `<python> cli.py plan` | Runs the pure-Python planner and prints the schedule. |
| **3. Sync to Google Tasks** | `<python> cli.py sync-tasks` | Idempotently syncs one task per study day, preserving completed tasks (`--refresh` also updates the lock screen). |
| **4. Build display page** | `<python> cli.py build-display` | Renders `output/index.html` and `output/plan.json` (live view + `?mode=static`). |
| **5. Capture & apply lock screen** | `<python> cli.py refresh-screen` | Renders display, captures `output/lockscreen.png` at native resolution, and applies the Windows/macOS lock screen. |
| **6. Live desktop wallpaper** | `<python> cli.py setup-wallpaper` | Points **Lively Wallpaper** (Windows) or **Plash** (Mac) to `output/index.html`. |
| **7. Schedule refreshes** | `<python> cli.py schedule install` | Registers Windows Task Scheduler / macOS `launchd` for morning (`--time 07:00`) + on-change (`--poll-minutes 30`, `--if-changed`) refreshes. Use `show` to inspect or `uninstall` to remove. |
| **8. Plain-language replan** | `<python> cli.py say "I have plans on the 28th"` | Uses Gemini to add a Calendar event or tick an open Google Task, recomputes in pure Python, syncs Tasks, refreshes the lock screen, and explains what moved. |

### Useful Flags & Customization
- **Custom background:** Uses `assets/background.jpg` by default. Swap that file in place, set `"display": {"background": "..."}` in `config/targets.json`, or pass `--background <path-or-url>` to `build-display`, `refresh-screen`, or `say` (`--background ""` falls back to the dark gradient).
- **Dry-run lock screen:** Add `--skip-apply` to `refresh-screen` or `say` to capture `output/lockscreen.png` without changing your OS lock screen.
- **Custom resolution / browser:** Pass `--size 2560x1600` to `refresh-screen`, or set `COUNTDOWN_BROWSER` to specify the Chromium executable.

---

## Tests

```bash
# Windows
.venv\Scripts\python -m unittest discover -s tests -v

# Mac
.venv/bin/python -m unittest discover -s tests -v
```

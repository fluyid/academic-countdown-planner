# Academic Countdown & Study Planner (`v2.0`)

Local, zero-hosting Python app for **Windows and Mac** that connects **Google Calendar**, **Google Tasks**, a **pure-Python deterministic study planner**, a **glassmorphic live wallpaper + lock-screen HUD**, and an optional **offline `"Hey Gemini"` voice replanner**.

## How It Works
- **Google Calendar (Truth for Time):** Reads your personal and university calendars (`config/targets.json`) to classify each day as **free** (`1.0 unit`), **class** (`<= 3.5h` -> `0.25 units`), or **blocked** (`> 3.5h`, or trip/exam/date -> `0.0 units`).
- **Google Tasks (Truth of Progress):** Syncs one task per study day with concrete due dates. Checking off a task on any device preserves completed progress across Windows and Mac.
- **Pure-Python Planner (Zero LLM Arithmetic):** Deterministic scheduling across three strict priority tiers:
  1. **Priority 1 (Protected):** Exam study units (`1 unit/day`).
  2. **Priority 2:** Required paper reading (tracked in **papers**, e.g. `0/4 papers` and `0/3 papers`, with optional assignment work units) under a hard **40 pages/day** reading cap across all subjects combined.
  3. **Priority 3 (Drops First When Tight):** Optional paper reading (abstract + intro + note).
  - Supports configurable **`blocked_date_ranges`** (e.g., exam-period blackouts) so study plans never overflow into exam weeks.
- **Single-Page Display (One Design -> Two Surfaces):** Ephemeral HTML (`output/index.html`) over a Gemini-generated cyberpunk night-city background (`assets/background.jpg`) with translucent glassmorphic HUD cards (`card_opacity`, bundled `Cyberway Riders`, `Orbitron`, and `Rajdhani` fonts), Windows/macOS lock-screen safe zones, concrete dates (no `T-minus` labels), and 5 combined milestones.
  - **Live desktop wallpaper** (ticking seconds + auto-reload): **Lively Wallpaper** (standard & Microsoft Store) / **Wallpaper Engine** on Windows, **Plash** on Mac — reloads automatically on plan updates via CLI reload + `output/plan_version.js`.
  - **Static lock screen** (days left + today's task): headless Chromium screenshot (`output/lockscreen.png`) refreshed every morning and on changes.
- **Strictly Scoped Gemini Layer (`say` / `voice` / `listen`):** Two jobs only — (1) map text or a short voice clip (echoing verbatim `Heard: "..."`) into a Calendar event, an exact open Google Task tick, a partial/custom **unit or paper progress log**, or one or more **target due-date updates** (synced to `config/targets.json` and your university calendar), and (2) explain the pure-Python recomputed diff.
  - **Voice + Offline Wake Word:** Push-to-talk (`cli.py voice`) or always-on offline **`"Hey Gemini"`** (`cli.py listen` via grammar-constrained local `vosk` — **\$0 and 0 Gemini calls while idle**, silent wake by default with optional `--chime`), with natural **\$0 neural readout** via `edge-tts` (`en-US-AriaNeural` on Windows & Mac, automatic offline OS TTS fallback).
  - **Zero-Accidental-Billing Guardrails:** Never called from scheduled refreshes; Flash-only (`gemini-3.5-flash-lite` for text/explanations, `gemini-3.5-flash` for voice transcription), `thinkingLevel: "MINIMAL"`, `max_output_tokens <= 512`, `<= 15s` / `<= 2 MB` audio cap, hard **25 calls/day** local cap (`gemini_usage.json`), and zero retries.

---

## Quick Start (Windows & Mac)

Requires **Python 3.11+** (3.12 recommended) and a Chromium-based browser (Chrome, Edge, or Brave).

### 1. Run the one-command setup
This creates `.venv`, installs dependencies, creates your outside-repo secrets folder, copies your Google OAuth JSON, optionally saves your Gemini API key, and configures your calendar/task names:

**Windows (PowerShell):**
```powershell
.\setup.ps1
```

**Mac (Terminal):**
```bash
chmod +x setup.sh && ./setup.sh
```

*(You can re-run the interactive wizard anytime with `<python> cli.py setup`, or check your environment without making changes via `<python> cli.py setup --check`.)*

### 2. Where secrets & offline models live (strictly outside the repo)
Nothing sensitive is ever stored inside this repository. All credentials, tokens, API keys, usage caps, and the ~40 MB offline Vosk wake-word model live in:
- **Windows:** `%USERPROFILE%\.config\countdown-planner\`
- **Mac:** `~/.config/countdown-planner/`
*(Override with `COUNTDOWN_SECRETS_DIR` if needed.)*

| File | Needed for | How to get it |
| :--- | :--- | :--- |
| `credentials.json` | Calendar + Tasks (**\$0**) | Google Cloud Console -> enable **Calendar API** & **Tasks API** -> create **OAuth 2.0 Client ID (Desktop App)** -> download JSON. |
| `token.json` | Saved OAuth login | Created automatically on first `cli.py check-calendar` browser sign-in. |
| `.env` (`GEMINI_API_KEY=...`) | Optional (`say` / `voice` / `listen`) | Create a key in **Google AI Studio** (saved via `cli.py setup`). |
| `vosk-model-small-en-us-0.15/` | Optional (`listen`) | Auto-downloaded once (~40 MB) on first `cli.py listen` run. |

### 3. Customize your targets
Edit [`config/targets.json`](config/targets.json) to set your own exams, assignments, required papers, dates (`null` until published), and `display.card_opacity` (`0.2`–`0.95`).

---

## Usage

Replace `<python>` with `.venv\Scripts\python` on **Windows** or `.venv/bin/python` on **Mac**.

| Step | Command | What it does |
| :--- | :--- | :--- |
| **0. Setup / health check** | `<python> cli.py setup [--check]` | Interactive onboarding wizard or non-destructive prerequisite check. |
| **1. Verify login & calendars** | `<python> cli.py check-calendar` | Single OAuth login; prints next 10 merged events (`-n N`). |
| **2. Preview study plan** | `<python> cli.py plan` | Runs the pure-Python planner and prints the full schedule. |
| **3. Sync to Google Tasks** | `<python> cli.py sync-tasks` | Idempotently syncs one task per study day, preserving completed tasks (`--refresh` also updates the lock screen). |
| **4. Build display page** | `<python> cli.py build-display` | Renders `output/index.html` and `output/plan.json` (live view + `?mode=static`). |
| **5. Capture & apply lock screen** | `<python> cli.py refresh-screen` | Renders display, captures `output/lockscreen.png` at native resolution, and applies the Windows/macOS lock screen. |
| **6. Live desktop wallpaper** | `<python> cli.py setup-wallpaper` | Points **Lively Wallpaper** (Windows) or **Plash** (Mac) to `output/index.html`. |
| **7. Schedule refreshes** | `<python> cli.py schedule install` | Registers Windows Task Scheduler / macOS `launchd` for morning (`--time 07:00`) + on-change (`--poll-minutes 30`, `--if-changed`) refreshes. Use `show` or `uninstall` to manage. |
| **8. Text replan** | `<python> cli.py say "I have plans on the 28th"` | Uses Gemini to add a Calendar event, tick a Task, log partial units/papers (e.g., `"I read half a paper for Ethics Essay"`), or update target due dates, then recomputes in pure Python, syncs Tasks, reloads live wallpaper/lock screen, and explains what moved. |
| **9. Push-to-talk voice replan** | `<python> cli.py voice --speak` | Records from your mic (auto-stops on silence, max 10s / clamped `<= 15s`), prints verbatim `Heard: "..."`, runs Gemini voice replan, refreshes screen + wallpaper, and speaks the reply via `en-US-AriaNeural`. Pass `--file clip.wav` to replay a local WAV. |
| **10. Always-on `"Hey Gemini"`** | `<python> cli.py listen --speak` | Runs 100% offline wake-word detection (**\$0, 0 Gemini calls while idle**); triggers cleanly on `"hey gemini"`, records your command, replans, and speaks back. Optional flags: `--once`, `--chime`, `--wake-phrase "..."`. |

### Useful Flags & Customization
- **Custom background:** Uses `assets/background.jpg` by default. Swap that file, set `"display": {"background": "..."}` in `config/targets.json`, or pass `--background <path-or-url>` (`--background ""` falls back to the dark gradient).
- **Card translucency:** Adjust `"display": {"card_opacity": 0.28}` in `config/targets.json` (clamped `0.2`–`0.95`).
- **Dry-run lock screen:** Add `--skip-apply` to `refresh-screen`, `say`, `voice`, or `listen` to capture `output/lockscreen.png` without changing your OS lock screen.
- **Custom resolution / browser:** Pass `--size 2560x1600` to `refresh-screen`, or set `COUNTDOWN_BROWSER` to specify the Chromium executable.

---

## Tests

All tests are 100% mocked (zero live Google/Gemini API calls or microphone access):

```bash
# Windows
.venv\Scripts\python -m unittest discover -s tests -v

# Mac
.venv/bin/python -m unittest discover -s tests -v
```

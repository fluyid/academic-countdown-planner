# Mission Plan: Academic Countdown Lock Screen App

> **Target Submission:** **Saturday, November 21, 2026** | **Buffer:** **Sunday, November 22** | **Handshake Hard Deadline:** **Monday, November 23, 2026 at 11:59 p.m. PT**.

## Goal & Core App Concept
Build a local, zero-hosting **Python academic countdown & study-planner app** for **Windows and Mac** that reads **Google Calendar** (truth for time), tracks progress in **Google Tasks** (truth of progress), and renders **one small HTML webpage** (a cozy Gemini-generated background + dark glassmorphic code-drawn HUD) that feeds both the **live ticking desktop wallpaper** (Lively Wallpaper / Wallpaper Engine on Windows, Plash on Mac) and **refreshed static lock screens** on both OSes.

---

## Academic Targets & Scheduling Rules

### 5 Targets Inside the Window
| # | Type | Target | Date | Study Units / Scope |
| :- | :--- | :--- | :--- | :--- |
| 1 | Exam | **Introduction to NLP** | **Dec 14, 2026** | 5 units (1 unit/day = 5 days) |
| 2 | Exam | **Introduction to Robotics** | *TBD (add when published)* | 5 units (1 unit/day = 5 days) |
| 3 | Exam | **Linear Algebra** | *TBD (add when published)* | 5 units (1 unit/day = 5 days) |
| 4 | Assignment | **Ethics Essay** | *TBD (editable)* | Remaining capacity |
| 5 | Assignment | **Computer Vision Assignment** | *TBD (editable)* | Remaining capacity |

### Study Load & Priority Logic
- **Fixed Exam Load:** `3 exams × 5 units × 1 unit/day = 15 study days total`.
- **Remaining Capacity:** Filled by **Assignment work** (Ethics Essay & Computer Vision) and **Optional Paper Reading** (read **abstract + introduction** and **keep a note**).
- **Load-Shedding Rule (when days get tight):**
  1. **Priority 1 (Protected):** Exam study units (1 unit/day)
  2. **Priority 2:** Assignment milestones (Ethics Essay, Computer Vision)
  3. **Priority 3 (First to drop):** Optional paper reading (abstract + intro + note)
- **Dynamic Date Handling:** Only **Intro to NLP (Dec 14, 2026)** is published so far; remaining exam and assignment dates live in `config/targets.json` and can be updated anytime.

---

## System Architecture

### 1. Three Architectural Places (Each Owns Exactly One Role)
| Place | Role / Single Kind of Truth |
| :--- | :--- |
| **Google Calendar** | **Truth for time** — classes, plans, and blocked days live here across **`"Kai Nath"`** (personal) and **`"IU"`** (university) |
| **Google Tasks** | **Truth of progress** — one task per study day; "done" lives here so closing a laptop loses nothing and both Windows and Mac stay in agreement |
| **The Display (Lock Screen)** | **Derived view only (ephemeral)** — holds no independent truth; can be deleted and rebuilt from Google Calendar and Google Tasks at any time |

### 2. Ordinary-Code Planner in the Middle (Pure Python — No AI)
- All scheduling math, day classification, unit spreading, load shedding (dropping optional paper reading first), and reshuffling of unfinished units live in **pure Python**—never delegated to an LLM.

#### Pure Python Capacity Rule
| Day Type | Condition | Units That Fit |
| :--- | :--- | :---: |
| **Free day** | No classes or blocking events | `1.0` |
| **Class day** | Class / scheduled load `<= 3.5 hours` | `0.25` |
| **Blocked day** | Load `> 3.5 hours`, **or** day has a trip, exam, or date | `0.0` |

### 3. Strictly Scoped Gemini Layer (Two Jobs Only — Zero Arithmetic)
1. **Sentence → Structured Action:** Translates plain-language utterances into a **Google Calendar event** or **which Google Task to tick**.
2. **Plan → Plain-Language Explanation:** Explains the newly computed schedule back to the user in words.
- **Hard Guardrail:** Gemini **never** does the arithmetic—pure Python computes the schedule so no unit is ever quietly dropped.
- **Task-Tick Confirmation:** After ticking a task in Google Tasks, the app **explicitly states which task it ticked**, so any wrong match can be corrected immediately.

---

## Design: One Design → Two Outputs

### Single Small Webpage Architecture
- Build the display as **one small webpage**: a **background image** with a **code-rendered dark glassmorphic text/HUD layer** on top that reads the computed plan data.
- **Why a webpage instead of re-generating images:** (1) code-drawn text is always spelled right, (2) replans cost $0 because only text changes, (3) desktop countdown ticks every second, (4) swappable personal or Gemini-generated background art.

### What Each Surface Shows (Windows & Mac)
| Surface | Live Ticking Seconds? | What It Shows | Implementation |
| :--- | :---: | :--- | :--- |
| **Windows Desktop** | Yes | Full live ticking countdown + today's task | **Lively Wallpaper** (free) or **Wallpaper Engine** |
| **Mac Desktop** | Yes | Full live ticking countdown + today's task | **Plash** |
| **Windows Lock Screen** | No | **Days left** + **today's task** (static) | Scheduled headless screenshot (morning + after every replan) |
| **Mac Lock Screen** | No | **Days left** + **today's task** (static) | Same static screenshot set as wallpaper/lock screen |

### Handshake Validity & Cozy Background
- Keep the entry 100% valid by using **Gemini with uploaded cozy reference images** to create the cozy background art underneath the dark glassmorphic HUD.

### Layout Rules
1. **Clean OS Safe Zones (Windows & Mac):** Leave unobstructed space where the **system clock** and **login fields** sit on both Windows and macOS.
2. **Responsive Aspect-Ratio Scaling:** Match each screen's native aspect ratio across devices.
3. **Concrete Dates, Not `T-minus` Labels:** Milestones show actual dates (e.g., `Dec 14`) so nothing goes stale.
4. **Max 5–6 Milestones in Date Order:** Cap displayed milestones at 5–6 in chronological order.

---

## Tech Stack & Zero-Accidental-Billing Safeguards

| Piece | Choice | Note |
| :--- | :--- | :--- |
| **Language** | Latest stable Python | Planner, Google API calls, scheduling glue |
| **Calendar & Tasks** | Google APIs with OAuth login | Both scopes approved in one login; secrets stored outside repo at `~/.config/countdown-planner/` |
| **Language understanding** | Gemini via Google AI Studio API key | ~€10 credit loaded; used **only** in Slice 6 |
| **Display** | One HTML page + headless screenshot | Feeds both live desktop wallpaper and static lock screens |
| **Scheduling** | `launchd` (Mac), Task Scheduler (Windows) | Every morning and whenever something changes |

### Zero-Accidental-Billing Safeguards
- **Slices 1–5 cost $0:** Google Calendar API and Google Tasks API are completely free (no billing account needed, never charged).
- **Slice 6 Gemini guardrails (enforced in code):**
  - **Never** call Gemini from scheduled morning refreshes, display rebuilds, or retry loops—only on explicit user plain-language commands.
  - Default to the cheapest Flash model (`gemini-2.5-flash-lite` / `gemini-2.5-flash`) with tight `max_output_tokens`.
  - Enforce a hard local daily request cap in code (e.g., max 25 calls/day) and fail fast on errors (no blind retry storms).
  - Keep the Gemini API key outside the repo (`~/.config/countdown-planner/.env`) alongside `credentials.json` and `token.json`.

---

## Building Slices (7 Vertical Slices)

> **Rule:** Test and commit after every slice so a bad slice costs one step back instead of a whole week.

| Slice | Name | Done Criteria (Verification Gate) | Status |
| :---: | :--- | :--- | :---: |
| **1** | **Login & Calendar Read** | Single OAuth login (Calendar + Tasks) prints next 10 events across `"Kai Nath"` and `"IU"`. | Done |
| **2** | **Planner** | Pure Python study plan prints as plain text and every unit lands on a day with capacity. | Next |
| **3** | **Tasks** | Plan appears in Google Tasks test list—one task per day with due dates. | Pending |
| **4** | **Display Page** | Single HTML page shows countdown + today's task over placeholder background, respecting OS safe zones. | Pending |
| **5** | **Lock Screen & Wallpaper** | Scheduled screenshot becomes lock screen on Mac, then Windows; Plash & Lively show live page. | Pending |
| **6** | **Gemini Replan** | Plain-language input (e.g., *"I have plans on the 28th"*) adds event, reshuffles units, ticks/echoes tasks, explains plan, refreshes screen. | Pending |
| **7** | **Polish & Feature Freeze** | Gemini-made cozy background in place, every word proofread, layout checked on both screens. | Pending |

---

## Submission & Rules for the Week
- **Saturday, Nov 21, 2026:** Submit on Handshake (title, cover image, description, link — user handles submission).
- **Sunday, Nov 22, 2026:** Buffer day before Monday, Nov 23, 11:59 p.m. PT hard deadline.
- **Scope Rule:** Any new idea goes on the **Version 2 Parking Lot** below—never into the code.

### Version 2 Parking Lot
- *(Empty)*

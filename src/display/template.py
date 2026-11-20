"""Single-file dark glassmorphic HTML display renderer with Windows + macOS safe zones."""

from __future__ import annotations

import html
import json
from typing import Any


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _safe_css_url(url: str | None) -> str:
    if not url:
        return ""
    cleaned = url.replace("\r", "").replace("\n", "").strip()
    lower = cleaned.lower()
    if not lower.startswith(("http://", "https://", "file://", "data:image/")):
        return ""
    escaped = cleaned.replace("\\", "/").replace('"', "%22").replace("'", "%27").replace("(", "%28").replace(")", "%29")
    return f'background-image: linear-gradient(180deg, rgba(10, 13, 20, 0.34), rgba(10, 13, 20, 0.56)), url("{escaped}");'


def _render_today_body(today: dict[str, Any]) -> str:
    items = today.get("items") or []
    if items:
        rows = []
        for item in items:
            opt_cls = " task-item--optional" if item.get("is_optional") else ""
            rows.append(
                f'<li class="task-item{opt_cls}">'
                f'<span class="task-name">{_esc(item["name"])}</span>'
                f'<span class="task-meta">{_esc(item["units"])} &middot; {_esc(item["progress"])}</span>'
                f"</li>"
            )
        detail = f'<p class="subtle-line">{_esc(today["detail"])}</p>' if today.get("detail") else ""
        return f'<ul class="task-list">{"".join(rows)}</ul>{detail}'

    headline = f'<p class="today-state">{_esc(today.get("headline") or "All caught up")}</p>'
    detail = f'<p class="subtle-line">{_esc(today["detail"])}</p>' if today.get("detail") else ""
    nxt = f'<p class="next-line">{_esc(today["next_preview"])}</p>' if today.get("next_preview") else ""
    return f"{headline}{detail}{nxt}"


def _render_milestones(milestones: list[dict[str, Any]]) -> str:
    if not milestones:
        return '<p class="subtle-line">No milestones configured.</p>'
    rows = []
    for m in milestones:
        kind_cls = "kind-exam" if m["kind"] == "Exam" else "kind-assignment"
        done_cls = " milestone--done" if m.get("done") else ""
        pct = max(0, min(100, int(m.get("percent", 0))))
        rows.append(
            f'<li class="milestone{done_cls}">'
            f'<div class="milestone-top">'
            f'<span class="milestone-name">{_esc(m["name"])}</span>'
            f'<span class="milestone-date">{_esc(m["date_label"])}</span>'
            f"</div>"
            f'<div class="milestone-sub">'
            f'<span class="kind-tag {kind_cls}">{_esc(m["kind"])}</span>'
            f'<div class="bar"><span class="bar-fill" style="width:{pct}%"></span></div>'
            f'<span class="milestone-prog">{_esc(m["progress_label"])}</span>'
            f"</div>"
            f"</li>"
        )
    return f'<ul class="milestone-list">{"".join(rows)}</ul>'


def render_display_html(data: dict[str, Any]) -> str:
    cd = data["countdown"]
    today = data["today"]
    status_cls = f"badge--{_esc(today.get('status', 'open'))}"
    bg_style = _safe_css_url(data.get("background_url"))
    note_html = f'<p class="status-note">{_esc(data["status_note"])}</p>' if data.get("status_note") else ""
    hms_html = '<span class="hms" id="countdown-hms">--:--:--</span>' if cd.get("target_iso") else ""
    milestone_count = len(data["milestones"])
    target_word = "target" if milestone_count == 1 else "targets"

    # Escape "</" so embedded JSON can never close the <script> tag.
    payload_json = json.dumps(
        {"target_iso": cd.get("target_iso"), "days_left": cd.get("days_left", 0)},
        separators=(",", ":"),
    ).replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(cd["label"])} &mdash; Study Countdown</title>
<style>
  :root {{
    /* Windows + macOS lock-screen safe zones: top clock & bottom login stay clear. */
    --safe-top: clamp(148px, 22vh, 240px);
    --safe-bottom: clamp(108px, 18vh, 200px);
    --safe-side: clamp(24px, 4.5vw, 72px);
    --card-w: min(400px, 36vw);
    --glass-bg: rgba(13, 17, 25, 0.62);
    --glass-border: rgba(255, 244, 228, 0.14);
    --ink: #f5f2eb;
    --ink-soft: rgba(245, 242, 235, 0.7);
    --ink-faint: rgba(245, 242, 235, 0.48);
    --accent: #f2b872;
    --exam: #f2b872;
    --assign: #8ab8e6;
    --done: #7fd1a0;
    --blocked: #e58f85;
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  html, body {{
    width: 100%;
    height: 100%;
    overflow: hidden;
    color: var(--ink);
    font-family: "SF Pro Display", "Segoe UI Variable", "Segoe UI", system-ui, -apple-system, sans-serif;
    background-color: #0b0e14;
    background-image:
      radial-gradient(ellipse 68% 58% at 50% 42%, rgba(168, 102, 52, 0.34), transparent 70%),
      radial-gradient(ellipse 52% 48% at 18% 82%, rgba(44, 62, 104, 0.42), transparent 72%),
      radial-gradient(ellipse 55% 45% at 84% 24%, rgba(86, 56, 46, 0.3), transparent 70%),
      linear-gradient(160deg, #0d111b 0%, #141723 48%, #181416 100%);
    background-size: cover;
    background-position: center;
    background-repeat: no-repeat;
    {bg_style}
  }}

  /* Split left/right columns leave top clock, bottom login, and center focal art unobstructed. */
  .hud {{
    width: 100%;
    height: 100%;
    padding: var(--safe-top) var(--safe-side) var(--safe-bottom);
    display: grid;
    grid-template-columns: minmax(0, var(--card-w)) minmax(0, var(--card-w));
    justify-content: space-between;
    align-content: center;
    gap: clamp(16px, 2.5vw, 32px);
  }}

  .panel {{
    background: var(--glass-bg);
    border: 1px solid var(--glass-border);
    border-radius: 20px;
    padding: clamp(18px, 2.1vh, 26px) clamp(18px, 2vw, 26px);
    backdrop-filter: blur(18px) saturate(130%);
    -webkit-backdrop-filter: blur(18px) saturate(130%);
    box-shadow: 0 22px 48px rgba(0, 0, 0, 0.42);
  }}

  .col-left {{
    display: flex;
    flex-direction: column;
    gap: clamp(12px, 1.8vh, 18px);
  }}

  .eyebrow {{
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--accent);
  }}

  .countdown-title {{
    margin-top: 6px;
    font-size: clamp(1.1rem, 1.55vw, 1.4rem);
    font-weight: 600;
    line-height: 1.25;
  }}

  .countdown-date {{
    margin-top: 2px;
    font-size: 0.88rem;
    color: var(--ink-soft);
  }}

  .countdown-row {{
    margin-top: clamp(10px, 1.6vh, 16px);
    display: flex;
    align-items: baseline;
    gap: 12px;
    flex-wrap: wrap;
    font-variant-numeric: tabular-nums;
  }}

  .days-num {{
    font-size: clamp(2.8rem, 4.6vw, 4.2rem);
    font-weight: 700;
    line-height: 0.95;
    letter-spacing: -0.03em;
  }}

  .days-unit {{
    font-size: 0.82rem;
    font-weight: 600;
    letter-spacing: 0.14em;
    color: var(--ink-soft);
  }}

  .hms {{
    margin-left: auto;
    font-size: clamp(1rem, 1.35vw, 1.25rem);
    font-weight: 500;
    letter-spacing: 0.04em;
    color: var(--accent);
    font-variant-numeric: tabular-nums;
  }}

  body.static-mode .hms {{ display: none; }}

  .section-head {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 12px;
  }}

  .section-title {{
    font-size: 0.76rem;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--ink-soft);
  }}

  .badge {{
    font-size: 0.72rem;
    font-weight: 600;
    padding: 3px 9px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.09);
    color: var(--ink);
    white-space: nowrap;
  }}
  .badge--study {{ color: var(--accent); background: rgba(242, 184, 114, 0.14); }}
  .badge--done {{ color: var(--done); background: rgba(127, 209, 160, 0.15); }}
  .badge--blocked {{ color: var(--blocked); background: rgba(229, 143, 133, 0.15); }}

  .task-list, .milestone-list {{
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }}

  .task-item {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 10px;
    padding-bottom: 8px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  }}
  .task-item:last-child {{ padding-bottom: 0; border-bottom: none; }}
  .task-item--optional .task-name {{ color: var(--ink-soft); }}
  .task-name {{ font-size: 0.95rem; font-weight: 500; line-height: 1.3; }}
  .task-meta {{ font-size: 0.8rem; color: var(--ink-soft); white-space: nowrap; font-variant-numeric: tabular-nums; }}

  .today-state {{ font-size: 1rem; font-weight: 600; }}
  .subtle-line {{ margin-top: 6px; font-size: 0.82rem; color: var(--ink-soft); line-height: 1.35; }}
  .next-line {{ margin-top: 8px; font-size: 0.8rem; color: var(--ink-faint); }}

  .milestone {{
    display: flex;
    flex-direction: column;
    gap: 5px;
    padding-bottom: 9px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  }}
  .milestone:last-child {{ padding-bottom: 0; border-bottom: none; }}
  .milestone--done .milestone-name {{ color: var(--done); }}

  .milestone-top {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 10px;
  }}
  .milestone-name {{ font-size: 0.92rem; font-weight: 500; line-height: 1.25; }}
  .milestone-date {{ font-size: 0.82rem; font-weight: 600; color: var(--accent); white-space: nowrap; }}

  .milestone-sub {{
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .kind-tag {{
    font-size: 0.66rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    min-width: 72px;
  }}
  .kind-exam {{ color: var(--exam); }}
  .kind-assignment {{ color: var(--assign); }}

  .bar {{
    flex: 1;
    height: 4px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.12);
    overflow: hidden;
  }}
  .bar-fill {{
    display: block;
    height: 100%;
    border-radius: 999px;
    background: linear-gradient(90deg, var(--accent), #f8d49d);
  }}
  .milestone-prog {{
    font-size: 0.74rem;
    color: var(--ink-soft);
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }}

  .status-note {{
    margin-top: 12px;
    padding-top: 10px;
    border-top: 1px solid rgba(255, 255, 255, 0.08);
    font-size: 0.76rem;
    color: var(--blocked);
  }}

  @media (max-width: 920px), (max-aspect-ratio: 4/3) {{
    :root {{
      --safe-top: clamp(120px, 19vh, 190px);
      --safe-bottom: clamp(92px, 16vh, 150px);
      --card-w: min(460px, 88vw);
    }}
    .hud {{
      grid-template-columns: minmax(0, var(--card-w));
      justify-content: center;
      overflow-y: auto;
    }}
  }}
</style>
</head>
<body>
  <main class="hud" aria-label="Academic Study Countdown">
    <div class="col-left">
      <section class="panel" aria-label="Countdown">
        <p class="eyebrow">Next Deadline</p>
        <h1 class="countdown-title">{_esc(cd["label"])}</h1>
        <p class="countdown-date">{_esc(cd["date_label"])}</p>
        <div class="countdown-row">
          <span class="days-num" id="countdown-days">{_esc(cd["days_left"])}</span>
          <span class="days-unit" id="countdown-unit">{_esc(cd["days_unit"])} LEFT</span>
          {hms_html}
        </div>
      </section>

      <section class="panel" aria-label="Today's Study Task">
        <div class="section-head">
          <span class="section-title">Today &middot; {_esc(today["date_label"])}</span>
          <span class="badge {status_cls}">{_esc(today["badge"])}</span>
        </div>
        {_render_today_body(today)}
      </section>
    </div>

    <section class="panel" aria-label="Upcoming Milestones">
      <div class="section-head">
        <span class="section-title">Milestones</span>
        <span class="badge">{milestone_count} {target_word}</span>
      </div>
      {_render_milestones(data["milestones"])}
      {note_html}
    </section>
  </main>

<script id="plan-data" type="application/json">{payload_json}</script>
<script>
(() => {{
  const params = new URLSearchParams(window.location.search);
  const staticMode = params.get("mode") === "static";
  if (staticMode) {{
    document.body.classList.add("static-mode");
    return;
  }}
  const data = JSON.parse(document.getElementById("plan-data").textContent || "{{}}");
  if (!data.target_iso) return;
  const [y, m, d] = data.target_iso.split("-").map(Number);
  const target = new Date(y, m - 1, d, 0, 0, 0).getTime();
  const daysEl = document.getElementById("countdown-days");
  const unitEl = document.getElementById("countdown-unit");
  const hmsEl = document.getElementById("countdown-hms");
  const pad = (n) => String(n).padStart(2, "0");

  function tick() {{
    const diff = Math.max(0, target - Date.now());
    const totalSec = Math.floor(diff / 1000);
    const days = Math.floor(totalSec / 86400);
    const hours = Math.floor((totalSec % 86400) / 3600);
    const mins = Math.floor((totalSec % 3600) / 60);
    const secs = totalSec % 60;
    daysEl.textContent = String(days);
    unitEl.textContent = (days === 1 ? "DAY" : "DAYS") + " LEFT";
    hmsEl.textContent = `${{pad(hours)}}:${{pad(mins)}}:${{pad(secs)}}`;
  }}

  tick();
  setInterval(tick, 1000);
}})();
</script>
</body>
</html>
"""

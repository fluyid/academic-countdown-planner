"""Single-file dark glassmorphic HTML display renderer with Windows + macOS safe zones."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from src.display.model import clamp_card_opacity

DEFAULT_FONTS_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"

_FONT_SPECS = (
    ("Cyberway Riders", "CyberwayRiders.otf", "opentype", "font/otf", "400"),
    ("Orbitron", "Orbitron-VariableFont_wght.ttf", "truetype", "font/ttf", "400 900"),
    ("Rajdhani", "Rajdhani-Medium.ttf", "truetype", "font/ttf", "500"),
    ("Rajdhani", "Rajdhani-SemiBold.ttf", "truetype", "font/ttf", "600"),
    ("Rajdhani", "Rajdhani-Bold.ttf", "truetype", "font/ttf", "700"),
)


def _file_to_data_uri(path: Path, default_mime: str) -> str:
    mime = mimetypes.guess_type(path.name)[0] or default_mime
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _build_font_faces(fonts_dir: Path = DEFAULT_FONTS_DIR) -> str:
    # Inline fonts as data URIs so Lively Wallpaper / Plash virtual hosts never block file:// loads.
    rules = []
    for family, filename, fmt, mime, weight in _FONT_SPECS:
        path = fonts_dir / filename
        if path.is_file():
            uri = _file_to_data_uri(path, mime)
            rules.append(
                f'  @font-face {{ font-family: "{family}"; src: url("{uri}") format("{fmt}"); '
                f"font-weight: {weight}; font-style: normal; font-display: block; }}"
            )
    return "\n".join(rules)


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _safe_css_url(url: str | None) -> str:
    if not url:
        return ""
    cleaned = url.replace("\r", "").replace("\n", "").strip()
    lower = cleaned.lower()
    if not lower.startswith(("http://", "https://", "file://", "data:image/")):
        return ""
    if lower.startswith("file://"):
        parsed = urlparse(cleaned)
        local = Path(url2pathname(unquote(parsed.path)))
        if local.is_file():
            cleaned = _file_to_data_uri(local, "image/jpeg")
    escaped = cleaned.replace("\\", "/").replace('"', "%22").replace("'", "%27").replace("(", "%28").replace(")", "%29")
    return f'background-image: linear-gradient(180deg, rgba(10, 13, 20, 0.08), rgba(10, 13, 20, 0.16)), url("{escaped}");'


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


def plan_version(data: dict[str, Any]) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def render_display_html(data: dict[str, Any]) -> str:
    cd = data["countdown"]
    today = data["today"]
    status_cls = f"badge--{_esc(today.get('status', 'open'))}"
    bg_style = _safe_css_url(data.get("background_url"))
    note_html = f'<p class="status-note">{_esc(data["status_note"])}</p>' if data.get("status_note") else ""
    hms_html = '<span class="hms" id="countdown-hms">--:--:--</span>' if cd.get("target_iso") else ""
    milestone_count = len(data["milestones"])
    target_word = "target" if milestone_count == 1 else "targets"
    card_opacity = clamp_card_opacity(data.get("card_opacity"))
    font_faces = _build_font_faces()
    version = plan_version(data)

    # Escape "</" so embedded JSON can never close the <script> tag.
    payload_json = json.dumps(
        {"target_iso": cd.get("target_iso"), "days_left": cd.get("days_left", 0), "version": version},
        separators=(",", ":"),
    ).replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(cd["label"])} &mdash; Study Countdown</title>
<style>
{font_faces}
  :root {{
    /* Windows + macOS lock-screen safe zones: top clock & bottom login stay clear. */
    --safe-top: clamp(148px, 22vh, 240px);
    --safe-bottom: clamp(108px, 18vh, 200px);
    --safe-side: clamp(24px, 4.5vw, 72px);
    --card-w: min(410px, 36vw);
    --font-display: "Cyberway Riders", "Orbitron", system-ui, sans-serif;
    --font-tech: "Orbitron", "Rajdhani", system-ui, sans-serif;
    --font-body: "Rajdhani", "Segoe UI Variable", "Segoe UI", system-ui, -apple-system, sans-serif;
    --glass-bg: rgba(7, 10, 20, {card_opacity:g});
    --glass-border: rgba(0, 240, 255, 0.28);
    --ink: #f2f8ff;
    --ink-soft: rgba(226, 240, 255, 0.9);
    --ink-faint: rgba(202, 224, 246, 0.78);
    --accent: #00f0ff;
    --exam: #fcee0a;
    --assign: #00f0ff;
    --done: #38f2a8;
    --blocked: #ff3b7a;
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  html, body {{
    width: 100%;
    height: 100%;
    overflow: hidden;
    color: var(--ink);
    font-family: var(--font-body);
    font-weight: 500;
    background-color: #070a14;
    background-image:
      radial-gradient(ellipse 68% 58% at 50% 42%, rgba(0, 240, 255, 0.16), transparent 70%),
      radial-gradient(ellipse 52% 48% at 18% 82%, rgba(255, 42, 109, 0.2), transparent 72%),
      radial-gradient(ellipse 55% 45% at 84% 24%, rgba(56, 72, 148, 0.28), transparent 70%),
      linear-gradient(160deg, #070a14 0%, #0d1224 48%, #120c1a 100%);
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
    border-radius: 16px;
    padding: clamp(18px, 2.1vh, 26px) clamp(18px, 2vw, 26px);
    backdrop-filter: blur(6px) saturate(130%);
    -webkit-backdrop-filter: blur(6px) saturate(130%);
    box-shadow: 0 14px 36px rgba(0, 0, 0, 0.36), inset 0 1px 0 rgba(0, 240, 255, 0.22);
    text-shadow: 0 1px 8px rgba(4, 7, 15, 0.92), 0 0 2px rgba(4, 7, 15, 0.95);
  }}

  .col-left {{
    display: flex;
    flex-direction: column;
    gap: clamp(12px, 1.8vh, 18px);
  }}

  .eyebrow {{
    font-family: var(--font-display);
    font-size: 1.05rem;
    font-weight: 400;
    letter-spacing: 0.08em;
    word-spacing: 0.28em;
    text-transform: uppercase;
    color: var(--accent);
    text-shadow: 0 0 14px rgba(0, 240, 255, 0.35);
  }}

  .countdown-title {{
    margin-top: 6px;
    font-family: var(--font-tech);
    font-size: clamp(1.05rem, 1.4vw, 1.3rem);
    font-weight: 700;
    letter-spacing: 0.02em;
    line-height: 1.25;
  }}

  .countdown-date {{
    margin-top: 4px;
    font-size: 0.98rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: var(--ink-soft);
  }}

  .countdown-row {{
    margin-top: clamp(12px, 1.6vh, 16px);
    display: flex;
    align-items: baseline;
    gap: 12px;
    flex-wrap: wrap;
    font-variant-numeric: tabular-nums;
  }}

  .days-num {{
    font-family: var(--font-tech);
    font-size: clamp(2.6rem, 4.4vw, 4rem);
    font-weight: 800;
    line-height: 0.95;
    letter-spacing: -0.02em;
    text-shadow: 0 0 20px rgba(0, 240, 255, 0.22);
  }}

  .days-unit {{
    font-family: var(--font-tech);
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.14em;
    color: var(--ink-soft);
  }}

  .hms {{
    margin-left: auto;
    font-family: var(--font-tech);
    font-size: clamp(0.95rem, 1.25vw, 1.18rem);
    font-weight: 600;
    letter-spacing: 0.06em;
    color: var(--accent);
    text-shadow: 0 0 12px rgba(0, 240, 255, 0.32);
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
    display: flex;
    align-items: baseline;
    gap: 8px;
  }}

  .section-heading {{
    font-family: var(--font-display);
    font-size: 1.05rem;
    font-weight: 400;
    letter-spacing: 0.08em;
    word-spacing: 0.28em;
    text-transform: uppercase;
    color: var(--accent);
    text-shadow: 0 0 12px rgba(0, 240, 255, 0.28);
  }}

  .section-date {{
    font-size: 0.92rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--ink-soft);
  }}

  .badge {{
    font-size: 0.82rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    padding: 2px 10px;
    border-radius: 999px;
    background: rgba(0, 240, 255, 0.1);
    border: 1px solid rgba(0, 240, 255, 0.22);
    color: var(--ink);
    white-space: nowrap;
  }}
  .badge--study {{ color: var(--exam); background: rgba(252, 238, 10, 0.12); border-color: rgba(252, 238, 10, 0.3); }}
  .badge--done {{ color: var(--done); background: rgba(56, 242, 168, 0.14); border-color: rgba(56, 242, 168, 0.32); }}
  .badge--blocked {{ color: var(--blocked); background: rgba(255, 59, 122, 0.14); border-color: rgba(255, 59, 122, 0.34); }}

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
    border-bottom: 1px solid rgba(0, 240, 255, 0.1);
  }}
  .task-item:last-child {{ padding-bottom: 0; border-bottom: none; }}
  .task-item--optional .task-name {{ color: var(--ink-soft); }}
  .task-name {{ font-size: 1.06rem; font-weight: 600; line-height: 1.25; }}
  .task-meta {{ font-size: 0.92rem; font-weight: 600; color: var(--ink-soft); white-space: nowrap; font-variant-numeric: tabular-nums; }}

  .today-state {{ font-family: var(--font-tech); font-size: 1rem; font-weight: 600; }}
  .subtle-line {{ margin-top: 6px; font-size: 0.94rem; font-weight: 500; color: var(--ink-soft); line-height: 1.35; }}
  .next-line {{ margin-top: 8px; font-size: 0.9rem; font-weight: 600; color: var(--ink-faint); }}

  .milestone {{
    display: flex;
    flex-direction: column;
    gap: 5px;
    padding-bottom: 9px;
    border-bottom: 1px solid rgba(0, 240, 255, 0.1);
  }}
  .milestone:last-child {{ padding-bottom: 0; border-bottom: none; }}
  .milestone--done .milestone-name {{ color: var(--done); }}

  .milestone-top {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 10px;
  }}
  .milestone-name {{ font-size: 1.04rem; font-weight: 600; line-height: 1.2; }}
  .milestone-date {{ font-size: 0.94rem; font-weight: 700; letter-spacing: 0.04em; color: var(--exam); white-space: nowrap; }}

  .milestone-sub {{
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .kind-tag {{
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    min-width: 78px;
  }}
  .kind-exam {{ color: var(--exam); }}
  .kind-assignment {{ color: var(--assign); }}

  .bar {{
    flex: 1;
    height: 4px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.14);
    overflow: hidden;
  }}
  .bar-fill {{
    display: block;
    height: 100%;
    border-radius: 999px;
    background: linear-gradient(90deg, var(--accent), var(--exam));
  }}
  .milestone-prog {{
    font-size: 0.86rem;
    font-weight: 600;
    color: var(--ink-soft);
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }}

  .status-note {{
    margin-top: 12px;
    padding-top: 10px;
    border-top: 1px solid rgba(0, 240, 255, 0.12);
    font-size: 0.88rem;
    font-weight: 600;
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
          <span class="section-title"><span class="section-heading">Today</span><span class="section-date">&middot; {_esc(today["date_label"])}</span></span>
          <span class="badge {status_cls}">{_esc(today["badge"])}</span>
        </div>
        {_render_today_body(today)}
      </section>
    </div>

    <section class="panel" aria-label="Upcoming Milestones">
      <div class="section-head">
        <span class="section-heading">Milestones</span>
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

  // Auto-reload live wallpaper via <script> tag (works on file:// URLs where fetch() is CORS-blocked).
  const currentVersion = data.version || "";
  function checkUpdate() {{
    if (!currentVersion) return;
    const tag = document.createElement("script");
    tag.src = "plan_version.js?_=" + Date.now();
    tag.onload = () => {{
      tag.remove();
      if (window.__PLAN_VERSION__ && window.__PLAN_VERSION__ !== currentVersion) {{
        window.location.reload();
      }}
    }};
    tag.onerror = () => tag.remove();
    document.head.appendChild(tag);
  }}
  setInterval(checkUpdate, 5000);
}})();
</script>
</body>
</html>
"""

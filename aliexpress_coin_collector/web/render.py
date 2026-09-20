"""HTML und SVG erzeugen. Reine Funktionen, keine Abhaengigkeit auf FastAPI.

Das Diagramm wird hier als SVG zusammengesetzt statt von einer JavaScript-Bibliothek gezeichnet.
Das kostet Hover und Zoom, spart aber jede fremde Abhaengigkeit und macht die Darstellung testbar.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from ..store import Attempt
from .data import DayPoint, outcome_kind, outcome_label

STYLE = """
:root {
  --bg: #f6f7f9; --card: #ffffff; --text: #1a1d21; --muted: #6b7280; --line: #e3e6ea;
  --ok: #1a7f4b; --warn: #a76a00; --bad: #b3261e; --accent: #2b5fd9;
  --coin: #f0b429; --coin-dark: #8a5a00;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14171a; --card: #1d2125; --text: #e7eaee; --muted: #9aa3ad; --line: #2c3238;
    --ok: #4ac07e; --warn: #e0a33a; --bad: #f2695e; --accent: #7ba2f5;
    --coin: #f5c451; --coin-dark: #5c3d00;
  }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 16px; background: var(--bg); color: var(--text);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
.wrap { max-width: 900px; margin: 0 auto; }
h1 { font-size: 1.25rem; margin: 0 0 16px; display: flex; align-items: center; gap: 10px; }
.logo { flex: none; }
footer { color: var(--muted); font-size: .8rem; text-align: center; padding: 8px 0 16px; }
h2 { font-size: 1rem; margin: 0 0 12px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 16px; margin-bottom: 16px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.tile .label { color: var(--muted); font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; }
.tile .value { font-size: 1.5rem; font-weight: 600; margin-top: 2px; }
.tile .sub { color: var(--muted); font-size: .85rem; }
.ok { color: var(--ok); } .warn { color: var(--warn); } .bad { color: var(--bad); }
table { width: 100%; border-collapse: collapse; font-size: .9rem; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: .78rem; text-transform: uppercase; }
td.msg { color: var(--muted); }
button { font: inherit; padding: 10px 16px; border-radius: 8px; border: 1px solid var(--line);
  background: var(--accent); color: #fff; cursor: pointer; }
button[disabled] { background: var(--line); color: var(--muted); cursor: not-allowed; }
button.secondary { background: transparent; color: var(--text); }
input[type=time], input[type=password] { font: inherit; padding: 8px; border-radius: 8px;
  border: 1px solid var(--line); background: var(--bg); color: var(--text); }
.row { display: flex; gap: 12px; flex-wrap: wrap; align-items: end; }
.note { color: var(--muted); font-size: .85rem; }
.banner { border-left: 4px solid var(--bad); padding-left: 12px; }
form.inline { display: inline; }
svg { width: 100%; height: auto; display: block; }
"""


# Muenze als Inline-SVG: kein Bild im Repo, keine externe Quelle, skaliert verlustfrei
# und nimmt die Farben der Oberflaeche an.
LOGO = (
    '<svg class="logo" viewBox="0 0 24 24" width="28" height="28" aria-hidden="true">'
    '<circle cx="12" cy="12" r="10" fill="var(--coin)"/>'
    '<circle cx="12" cy="12" r="7.5" fill="none" stroke="var(--coin-dark)" stroke-width="1.2"/>'
    '<path d="M15.7 8.3A5.2 5.2 0 1 0 15.7 15.7M7.6 11h6.8M7.6 13h6" fill="none" '
    'stroke="var(--coin-dark)" stroke-width="1.5" stroke-linecap="round"/>'
    "</svg>"
)

# Dasselbe Motiv als Favicon. Als data-URI eingebettet, damit keine zweite Anfrage noetig ist.
FAVICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E"
    "%3Ccircle cx='12' cy='12' r='10' fill='%23f0b429'/%3E"
    "%3Ccircle cx='12' cy='12' r='7.5' fill='none' stroke='%238a5a00' stroke-width='1.2'/%3E"
    "%3Cpath d='M15.7 8.3A5.2 5.2 0 1 0 15.7 15.7M7.6 11h6.8M7.6 13h6' fill='none' "
    "stroke='%238a5a00' stroke-width='1.5' stroke-linecap='round'/%3E%3C/svg%3E"
)


def _e(value: object) -> str:
    return escape(str(value), quote=True)


def page(title: str, body: str, version: str = "") -> str:
    footer = f"<footer>aliexpress-coin-collector {_e(version)}</footer>" if version else ""
    return (
        "<!doctype html>\n"
        '<html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<link rel="icon" href="{FAVICON}">'
        f"<title>{_e(title)}</title><style>{STYLE}</style></head>"
        f'<body><div class="wrap">{body}{footer}</div></body></html>'
    )


def login_page(error: str = "", version: str = "") -> str:
    warning = f'<p class="bad">{_e(error)}</p>' if error else ""
    return page(
        "Anmeldung",
        f"""<h1>{LOGO}AliExpress Coin Collector</h1>
        <div class="card">
          {warning}
          <form method="post" action="/login">
            <div class="row">
              <label>Passwort<br><input type="password" name="password" autofocus required></label>
              <button type="submit">Anmelden</button>
            </div>
          </form>
        </div>""",
        version,
    )


def coin_chart(points: list[DayPoint], width: int = 860, height: int = 220) -> str:
    """Muenzstand als Liniendiagramm. Gibt bei zu wenigen Daten einen Hinweis statt einer Linie."""
    if len(points) < 2:
        return '<p class="note">Noch zu wenige Daten fuer einen Verlauf. Ab dem zweiten Tag mit erkanntem Stand.</p>'

    pad_l, pad_r, pad_t, pad_b = 48, 12, 12, 28
    inner_w = width - pad_l - pad_r
    inner_h = height - pad_t - pad_b

    values = [p.coins for p in points]
    lo, hi = min(values), max(values)
    if hi == lo:  # waagerechte Linie in der Mitte statt Division durch null
        lo, hi = lo - 1, hi + 1

    def x_at(i: int) -> float:
        return pad_l + (inner_w * i / (len(points) - 1))

    def y_at(value: int) -> float:
        return pad_t + inner_h - (inner_h * (value - lo) / (hi - lo))

    line = " ".join(f"{x_at(i):.1f},{y_at(p.coins):.1f}" for i, p in enumerate(points))
    area = f"{pad_l:.1f},{pad_t + inner_h:.1f} {line} {x_at(len(points) - 1):.1f},{pad_t + inner_h:.1f}"

    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Muenzstand von {points[0].day} bis {points[-1].day}">'
    ]
    for frac in (0.0, 0.5, 1.0):  # drei waagerechte Hilfslinien mit Beschriftung
        value = round(lo + (hi - lo) * frac)
        y = y_at(value)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" stroke="var(--line)"/>')
        parts.append(
            f'<text x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="var(--muted)">{value}</text>'
        )
    parts.append(f'<polygon points="{area}" fill="var(--accent)" opacity="0.12"/>')
    parts.append(f'<polyline points="{line}" fill="none" stroke="var(--accent)" stroke-width="2"/>')
    for label, i, anchor in ((points[0].day, 0, "start"), (points[-1].day, len(points) - 1, "end")):
        parts.append(
            f'<text x="{x_at(i):.1f}" y="{height - 8}" text-anchor="{anchor}" '
            f'font-size="11" fill="var(--muted)">{label:%d.%m.}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def status_tiles(
    last: Attempt | None,
    next_at: datetime | None,
    now: datetime,
    coins: int | None,
    streak: int,
    alive: bool,
) -> str:
    if last is None:
        last_html = '<div class="value">–</div><div class="sub">noch kein Lauf</div>'
    else:
        cls = outcome_kind(last.outcome)
        last_html = (
            f'<div class="value {cls}">{_e(outcome_label(last.outcome))}</div>'
            f'<div class="sub">{last.ts:%d.%m. %H:%M} ({_e(last.kind)})</div>'
        )

    if next_at is None:
        next_html = '<div class="value">–</div><div class="sub">keiner geplant</div>'
    else:
        minutes = max(0, int((next_at - now).total_seconds()) // 60)
        rest = f"in {minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"in {minutes} min"
        next_html = f'<div class="value">{next_at:%H:%M}</div><div class="sub">{next_at:%d.%m.}, {rest}</div>'

    service = '<div class="value ok">läuft</div>' if alive else '<div class="value bad">läuft nicht</div>'
    coins_html = f'<div class="value">{coins}</div>' if coins is not None else '<div class="value">–</div>'

    return f"""<div class="tiles">
      <div class="tile"><div class="label">Letzter Lauf</div>{last_html}</div>
      <div class="tile"><div class="label">Nächster Lauf</div>{next_html}</div>
      <div class="tile"><div class="label">Münzstand</div>{coins_html}<div class="sub">zuletzt erkannt</div></div>
      <div class="tile"><div class="label">Streak</div><div class="value">{streak}</div>
        <div class="sub">abgeleitet, nicht aus der App</div></div>
      <div class="tile"><div class="label">Dienst</div>{service}</div>
    </div>"""


def history_table(attempts: list[Attempt], shots: dict[str, str]) -> str:
    """Laufhistorie, neueste zuerst. 'shots' bildet einen Zeitstempel auf einen Screenshot-Namen ab."""
    if not attempts:
        return '<p class="note">Noch keine Läufe gespeichert.</p>'

    rows = []
    for a in sorted(attempts, key=lambda x: x.ts, reverse=True):
        coins = "–"
        if a.coins_before is not None and a.coins_after is not None and a.coins_after != a.coins_before:
            coins = f"{a.coins_before} → {a.coins_after}"
        elif a.coins_after is not None:
            coins = str(a.coins_after)
        shot = shots.get(a.ts.strftime("%Y%m%d-%H%M%S"))
        message = _e(a.message)
        if shot:
            message += f' <a href="/shot/{_e(shot)}">Screenshot</a>'
        rows.append(
            f"<tr><td>{a.ts:%d.%m. %H:%M}</td><td>{_e(a.kind)}</td>"
            f'<td class="{outcome_kind(a.outcome)}">{_e(outcome_label(a.outcome))}</td>'
            f"<td>{coins}</td><td>{a.duration_s:.0f} s</td>"
            f'<td class="msg">{message}</td></tr>'
        )
    return (
        "<table><thead><tr><th>Zeit</th><th>Art</th><th>Ergebnis</th>"
        "<th>Münzen</th><th>Dauer</th><th>Meldung</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )

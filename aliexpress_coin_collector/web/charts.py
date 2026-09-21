"""Diagramme als SVG, in Python zusammengesetzt.

Bewusst keine JavaScript-Bibliothek: das kostet Hover und Zoom, spart aber jede fremde
Abhaengigkeit und macht die Darstellung ohne Browser testbar.

Die Datei kennt weder FastAPI noch Jinja -- reine Funktionen von Daten auf Text.
"""

from __future__ import annotations

from markupsafe import Markup

from .data import DayPoint

# Die Klasse traegt die Skalierung: eine Regel fuer alle svg wuerde auch das Logo aufblasen.
_OPEN = '<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{label}">'


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def coin_chart(points: list[DayPoint], width: int = 950, height: int = 220) -> Markup:
    """Muenzstand als Linie. Bei weniger als zwei Punkten ein Hinweis statt einer Linie.

    Das Ergebnis ist als sicher markiert, weil es hier vollstaendig selbst gebaut wird. Alles,
    was aus der Datenbank kommt, sind Zahlen und Datumsangaben; der einzige freie Text ist die
    Beschriftung, und die geht durch _escape. Ohne die Markierung wuerde Jinja das SVG
    maskieren und als Text auf die Seite schreiben.
    """
    if len(points) < 2:
        return Markup(
            '<p class="note" style="margin-top: 0;">Noch zu wenige Daten für einen Verlauf. '
            "Ab dem zweiten Tag mit erkanntem Stand.</p>"
        )

    pad_l, pad_r, pad_t, pad_b = 46, 10, 12, 26
    inner_w = width - pad_l - pad_r
    inner_h = height - pad_t - pad_b

    values = [p.coins for p in points]
    lo, hi = min(values), max(values)
    if hi == lo:  # waagerechte Linie in der Mitte statt Division durch null
        lo, hi = lo - 1, hi + 1

    last = len(points) - 1

    def x_at(i: int) -> float:
        return pad_l + inner_w * i / last

    def y_at(value: int) -> float:
        return pad_t + inner_h - inner_h * (value - lo) / (hi - lo)

    line = " ".join(f"{x_at(i):.1f},{y_at(p.coins):.1f}" for i, p in enumerate(points))
    area = f"{pad_l},{pad_t + inner_h} {line} {x_at(last):.1f},{pad_t + inner_h}"

    label = _escape(
        f"Münzstand von {points[0].day:%d.%m.%Y} bis {points[-1].day:%d.%m.%Y}, {values[0]} auf {values[-1]}"
    )
    parts = [_OPEN.format(w=width, h=height, label=label)]

    for frac in (0.0, 0.5, 1.0):  # drei Hilfslinien mit Beschriftung
        value = round(lo + (hi - lo) * frac)
        y = y_at(value)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" stroke="var(--line)"/>')
        parts.append(
            f'<text x="{pad_l - 9}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="var(--muted)">{value}</text>'
        )

    parts.append(f'<polygon points="{area}" fill="var(--brand)" opacity="0.10"/>')
    parts.append(
        f'<polyline points="{line}" fill="none" stroke="var(--brand)" stroke-width="2.2" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
    )
    parts.append(
        f'<circle cx="{x_at(last):.1f}" cy="{y_at(values[-1]):.1f}" r="4" fill="var(--brand)" '
        'stroke="var(--surface)" stroke-width="2"/>'
    )
    for day, i, anchor in ((points[0].day, 0, "start"), (points[-1].day, last, "end")):
        parts.append(
            f'<text x="{x_at(i):.1f}" y="{height - 7}" text-anchor="{anchor}" font-size="11" '
            f'fill="var(--muted)">{day:%d.%m.}</text>'
        )
    parts.append("</svg>")
    return Markup("".join(parts))

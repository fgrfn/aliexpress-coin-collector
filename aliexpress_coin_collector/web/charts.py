"""Diagramme als SVG, in Python zusammengesetzt.

Bewusst keine JavaScript-Bibliothek: das kostet Hover und Zoom, spart aber jede fremde
Abhaengigkeit und macht die Darstellung ohne Browser testbar.

Die Datei kennt weder FastAPI noch Jinja -- reine Funktionen von Daten auf Text.
"""

from __future__ import annotations

from markupsafe import Markup

from .data import BatteryPoint, DayPoint, DayShare

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


def weekday_chart(days: list[DayShare], width: int = 620, height: int = 170) -> Markup:
    """Erfolge je Wochentag als Saeulen.

    Die Saeulenhoehe zeigt die Quote, nicht die absolute Zahl: bei sieben Wochentagen und
    ungleich vielen Tagen je Tag waere die absolute Zahl irrefuehrend.
    """
    if not days or all(d.total == 0 for d in days):
        return Markup('<p class="note" style="margin-top: 0;">Noch keine Läufe in diesem Zeitraum.</p>')

    pad_b, pad_t = 34, 18
    inner_h = height - pad_t - pad_b
    slot = width / len(days)
    bar_w = slot * 0.52

    parts = [_OPEN.format(w=width, h=height, label=_escape("Erfolgsquote je Wochentag"))]
    for i, day in enumerate(days):
        cx = slot * (i + 0.5)
        bar_h = max(2.0, inner_h * day.percent / 100) if day.total else 2.0
        y = pad_t + inner_h - bar_h
        fill = "var(--brand)" if day.total else "var(--line)"
        opacity = 0.35 + 0.6 * (day.percent / 100) if day.total else 1.0
        parts.append(
            f'<rect x="{cx - bar_w / 2:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" '
            f'rx="4" fill="{fill}" opacity="{opacity:.2f}"/>'
        )
        if day.total:
            parts.append(
                f'<text x="{cx:.1f}" y="{y - 6:.1f}" text-anchor="middle" font-size="11" '
                f'fill="var(--muted)">{day.percent} %</text>'
            )
        parts.append(
            f'<text x="{cx:.1f}" y="{height - 14}" text-anchor="middle" font-size="11" '
            f'fill="var(--muted)">{day.name}</text>'
        )
        parts.append(
            f'<text x="{cx:.1f}" y="{height - 2}" text-anchor="middle" font-size="10" '
            f'fill="var(--muted)" opacity="0.7">{day.good}/{day.total}</text>'
        )
    parts.append("</svg>")
    return Markup("".join(parts))


def gain_chart(points: list[DayPoint], width: int = 950, height: int = 150) -> Markup:
    """Zuwachs je Tag als Saeulen. Zeigt, ob die App unterschiedlich viel herausrueckt."""
    # gain ist None, wenn der Vortag keinen erkannten Stand hatte -- das ist kein Nullzuwachs.
    usable = [p for p in points if p.gain is not None and p.gain > 0]
    if len(usable) < 2:
        return Markup('<p class="note" style="margin-top: 0;">Noch zu wenige Tage mit erkanntem Zuwachs.</p>')

    pad_l, pad_r, pad_t, pad_b = 46, 10, 14, 24
    inner_w = width - pad_l - pad_r
    inner_h = height - pad_t - pad_b
    top = max(p.gain for p in usable)
    slot = inner_w / len(usable)
    bar_w = min(18.0, slot * 0.7)

    parts = [
        _OPEN.format(
            w=width,
            h=height,
            label=_escape(f"Zuwachs je Tag, {min(p.gain for p in usable)} bis {top} Münzen"),
        )
    ]
    for value in (0, top):
        y = pad_t + inner_h - (inner_h * value / top if top else 0)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" stroke="var(--line)"/>')
        parts.append(
            f'<text x="{pad_l - 9}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="var(--muted)">{value}</text>'
        )
    for i, point in enumerate(usable):
        cx = pad_l + slot * (i + 0.5)
        bar_h = max(2.0, inner_h * point.gain / top)
        parts.append(
            f'<rect x="{cx - bar_w / 2:.1f}" y="{pad_t + inner_h - bar_h:.1f}" width="{bar_w:.1f}" '
            f'height="{bar_h:.1f}" rx="3" fill="var(--brand)" opacity="0.75"/>'
        )
    for point, i, anchor in ((usable[0], 0, "start"), (usable[-1], len(usable) - 1, "end")):
        x = pad_l + slot * (i + 0.5)
        parts.append(
            f'<text x="{x:.1f}" y="{height - 6}" text-anchor="{anchor}" font-size="11" '
            f'fill="var(--muted)">{point.day:%d.%m.}</text>'
        )
    parts.append("</svg>")
    return Markup("".join(parts))


def battery_chart(points: list[BatteryPoint], low_pct: int = 25, width: int = 950, height: int = 180) -> Markup:
    """Ladestand als Linie, feste Skala von 0 bis 100.

    Anders als beim Muenzstand waere eine mitwachsende Skala hier irrefuehrend: ein Verlauf
    zwischen 98 und 100 Prozent saehe dann aus wie ein Absturz. Die Schwelle, ab der gewarnt
    wird, steht als Linie mit drin -- sonst sagt die Kurve allein wenig.
    """
    if len(points) < 2:
        return Markup(
            '<p class="note" style="margin-top: 0;">Noch zu wenige Messwerte für einen Verlauf. '
            "Der Akkustand wird seit Version 0.14.0 mitgeschrieben.</p>"
        )

    pad_l, pad_r, pad_t, pad_b = 46, 10, 12, 26
    inner_w, inner_h = width - pad_l - pad_r, height - pad_t - pad_b
    last = len(points) - 1

    def x_at(i: int) -> float:
        return pad_l + inner_w * i / last

    def y_at(value: float) -> float:
        return pad_t + inner_h - inner_h * max(0.0, min(100.0, value)) / 100

    line = " ".join(f"{x_at(i):.1f},{y_at(p.level):.1f}" for i, p in enumerate(points))
    area = f"{pad_l},{pad_t + inner_h} {line} {x_at(last):.1f},{pad_t + inner_h}"
    label = _escape(
        f"Ladestand von {points[0].day:%d.%m.%Y} bis {points[-1].day:%d.%m.%Y}, "
        f"{points[0].level} auf {points[-1].level} Prozent"
    )
    parts = [_OPEN.format(w=width, h=height, label=label)]

    for value in (0, 50, 100):
        y = y_at(value)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" stroke="var(--line)"/>')
        parts.append(
            f'<text x="{pad_l - 9}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="var(--muted)">{value}</text>'
        )

    y_low = y_at(low_pct)
    parts.append(
        f'<line x1="{pad_l}" y1="{y_low:.1f}" x2="{width - pad_r}" y2="{y_low:.1f}" stroke="var(--bad)" '
        'stroke-width="1" stroke-dasharray="4 4" opacity="0.7"/>'
    )
    # Linksbuendig: rechts endet die Kurve, und dort lag die Beschriftung genau darueber.
    parts.append(
        f'<text x="{pad_l + 4}" y="{y_low - 5:.1f}" text-anchor="start" font-size="10" '
        f'fill="var(--bad)">Warnschwelle {low_pct} %</text>'
    )

    parts.append(f'<polygon points="{area}" fill="var(--ok)" opacity="0.10"/>')
    parts.append(
        f'<polyline points="{line}" fill="none" stroke="var(--ok)" stroke-width="2.2" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
    )
    parts.append(
        f'<circle cx="{x_at(last):.1f}" cy="{y_at(points[-1].level):.1f}" r="4" fill="var(--ok)" '
        'stroke="var(--surface)" stroke-width="2"/>'
    )
    for day, i, anchor in ((points[0].day, 0, "start"), (points[-1].day, last, "end")):
        parts.append(
            f'<text x="{x_at(i):.1f}" y="{height - 7}" text-anchor="{anchor}" font-size="11" '
            f'fill="var(--muted)">{day:%d.%m.}</text>'
        )
    parts.append("</svg>")
    return Markup("".join(parts))

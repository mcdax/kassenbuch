"""
Small server-side SVG charts: no JavaScript library, no external requests.
Hovering a bar or point shows its value (SVG <title>). Colours come from CSS classes.
"""

from datetime import date
from html import escape

from markupsafe import Markup

WIDTH, HEIGHT = 720, 240
PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 56, 12, 12, 28


def euro(cents: int, decimals: int = 2) -> str:
    text = f"{cents / 100:,.{decimals}f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{text} €"


def _nice_max(value: float) -> int:
    """Round the axis maximum up to a 'round' number, so the chart uses most of its height."""
    if value <= 0:
        return 100
    magnitude = 10 ** (len(str(int(value))) - 1)
    for step in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if value <= step * magnitude:
            return round(step * magnitude)
    return 10 * magnitude


def _axis(max_value: int, y) -> list[str]:
    # Below 10 € the labels need cents, e.g. 1,25 €
    decimals = 2 if max_value < 1000 else 0
    parts = []
    for fraction in (0, 0.5, 1):
        value = max_value * fraction
        parts.append(f'<line class="grid" x1="{PAD_LEFT}" x2="{WIDTH - PAD_RIGHT}" y1="{y(value):.1f}" y2="{y(value):.1f}"/>')
        parts.append(f'<text class="axis" x="{PAD_LEFT - 6}" y="{y(value) + 4:.1f}" text-anchor="end">'
                     f'{escape(euro(round(value), decimals))}</text>')
    return parts


def bar_chart(bars: list[tuple[str, int, str]]) -> Markup:
    """bars: (x label, value in cents, tooltip) per bar."""
    if not bars:
        return Markup("")
    max_value = _nice_max(max(v for _, v, _ in bars))
    plot_h = HEIGHT - PAD_TOP - PAD_BOTTOM
    plot_w = WIDTH - PAD_LEFT - PAD_RIGHT
    slot = plot_w / len(bars)

    def y(value):
        return PAD_TOP + plot_h * (1 - value / max_value)

    parts = _axis(max_value, y)
    label_every = max(1, round(len(bars) / 12))
    for n, (label, value, tooltip) in enumerate(bars):
        x = PAD_LEFT + n * slot + slot * 0.15
        parts.append(f'<rect class="bar" x="{x:.1f}" y="{y(value):.1f}" width="{slot * 0.7:.1f}" '
                     f'height="{max(0, y(0) - y(value)):.1f}"><title>{escape(tooltip)}</title></rect>')
        if n % label_every == 0:
            parts.append(f'<text class="axis" x="{x + slot * 0.35:.1f}" y="{HEIGHT - 8}" '
                         f'text-anchor="middle">{escape(label)}</text>')
    return Markup(f'<svg class="chart" viewBox="0 0 {WIDTH} {HEIGHT}" role="img">{"".join(parts)}</svg>')


def line_chart(series: list[tuple[str, list[tuple[str, int, str]], bool]]) -> Markup:
    """
    series: (css class, points, draw line) per series; points are (ISO date, value in cents, tooltip),
    plotted by date so gaps between purchases show as such. Without a line only the points are drawn.
    """
    points_all = [p for _, points, _ in series for p in points]
    if not points_all:
        return Markup("")
    days =[date.fromisoformat(d[:10]).toordinal() for d, _, _ in points_all]
    first_day, last_day = min(days), max(days)
    span = max(1, last_day - first_day)
    max_value = _nice_max(max(v for _, v, _ in points_all) * 1.1)
    plot_h = HEIGHT - PAD_TOP - PAD_BOTTOM
    plot_w = WIDTH - PAD_LEFT - PAD_RIGHT

    def x(iso):
        return PAD_LEFT + plot_w * (date.fromisoformat(iso[:10]).toordinal() - first_day) / span

    def y(value):
        return PAD_TOP + plot_h * (1 - value / max_value)

    parts = _axis(max_value, y)
    for css_class, points, draw_line in series:
        if not points:
            continue
        if draw_line:
            # Step line: a price stays the same until the next purchase
            path = [f"M{x(points[0][0]):.1f},{y(points[0][1]):.1f}"]
            for day, value, _ in points[1:]:
                path.append(f"H{x(day):.1f}V{y(value):.1f}")
            parts.append(f'<path class="line {css_class}" d="{"".join(path)}"/>')
        for day, value, tooltip in points:
            parts.append(f'<circle class="point {css_class}" cx="{x(day):.1f}" cy="{y(value):.1f}" r="3.5">'
                         f'<title>{escape(tooltip)}</title></circle>')
    for iso in (min(points_all)[0], max(points_all)[0]):
        anchor = "start" if iso == min(points_all)[0] else "end"
        parts.append(f'<text class="axis" x="{x(iso):.1f}" y="{HEIGHT - 8}" text-anchor="{anchor}">'
                     f'{escape(iso[8:10] + "." + iso[5:7] + "." + iso[:4])}</text>')
    return Markup(f'<svg class="chart" viewBox="0 0 {WIDTH} {HEIGHT}" role="img">{"".join(parts)}</svg>')

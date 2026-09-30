"""Presentation-only template helpers for the portal UI.

Nothing here touches the database or changes behaviour: the helpers derive
stable visual values (thumbnail art, track colours) from existing data.
"""
import hashlib
import re

from django import template

register = template.Library()


def _digest(*parts):
    return hashlib.md5(":".join(str(p) for p in parts).encode("utf-8")).digest()


@register.simple_tag
def art_vars(kind, key):
    """CSS custom properties for a deterministic abstract thumbnail.

    The same (kind, key) pair always produces the same art, so each project
    or event keeps its look across page loads. Hues stay in the
    blue-violet-pink band used by the design.
    """
    d = _digest(kind, key)
    h1 = 215 + d[0] % 95            # 215..309  blue -> violet
    h2 = 255 + d[1] % 80            # 255..334  violet -> pink
    h3 = 200 + d[2] % 70            # 200..269  sky -> indigo
    x1, y1 = 12 + d[3] % 56, 10 + d[4] % 50
    x2, y2 = 40 + d[5] % 50, 45 + d[6] % 50
    rot = d[7] % 360
    bx, by = -18 + d[8] % 44, -14 + d[9] % 46
    br = d[10] % 360
    return (
        f"--h1:{h1};--h2:{h2};--h3:{h3};"
        f"--x1:{x1}%;--y1:{y1}%;--x2:{x2}%;--y2:{y2}%;"
        f"--rot:{rot}deg;--bx:{bx}%;--by:{by}%;--br:{br}deg"
    )


# Glyphs available in templates/includes/icon.html, used as event icons.
EVENT_GLYPHS = (
    "rocket", "sparkles", "trophy", "compass", "globe",
    "layers", "award", "calendar-check", "zap", "code",
)


@register.simple_tag
def event_glyph(slug):
    """Stable icon name for an event, picked from EVENT_GLYPHS by its slug."""
    return EVENT_GLYPHS[_digest("glyph", slug)[0] % len(EVENT_GLYPHS)]


@register.filter
def track_hue(track):
    """Stable hue (0-359) for a track, derived from its name."""
    name = getattr(track, "name", track) or ""
    # 24 evenly spaced slots keep neighbouring tracks visibly distinct.
    return (_digest("track", name.strip().lower())[0] % 24) * 15


@register.filter
def initials(text):
    """Up to two initials for a thumbnail, e.g. "Dry Harbour" -> "DH".

    Uses the first letters of the first two words; a one-word title gives its
    first two characters. Letters and digits only, so punctuation is skipped.
    """
    words = re.findall(r"[^\W_]+", str(text or ""))
    if not words:
        return ""
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


@register.filter
def placeholder(field, text):
    """Render a bound form field with a placeholder attribute.

    Uses BoundField.as_widget so Django still supplies name, id, required,
    maxlength, autocomplete and aria-* attributes.
    """
    return field.as_widget(attrs={"placeholder": text})

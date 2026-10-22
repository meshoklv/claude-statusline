"""Tiny ANSI SGR -> HTML converter (foreground colours only, which is all the status line uses)."""
import html
import re

SGR = re.compile(r"\033\[([0-9;]*)m")

# VS Code "Dark+" terminal palette
BASE16 = ["#000000", "#cd3131", "#0dbc79", "#e5e510", "#2472c8", "#bc3fbc", "#11a8cd", "#e5e5e5",
          "#666666", "#f14c4c", "#23d18b", "#f5f543", "#3b8eea", "#d670d6", "#29b8db", "#ffffff"]


def xterm256(n):
    if n < 16:
        return BASE16[n]
    if n < 232:
        n -= 16
        lv = [0, 95, 135, 175, 215, 255]
        return "#%02x%02x%02x" % (lv[n // 36], lv[(n // 6) % 6], lv[n % 6])
    v = 8 + (n - 232) * 10
    return "#%02x%02x%02x" % (v, v, v)


def convert(text):
    out, pos, color, open_span = [], 0, None, False

    def emit(chunk):
        if not chunk:
            return
        esc = html.escape(chunk)
        out.append('<span style="color:%s">%s</span>' % (color, esc) if color else esc)

    for m in SGR.finditer(text):
        emit(text[pos:m.start()])
        pos = m.end()
        codes = [int(c) if c else 0 for c in m.group(1).split(";")]
        i = 0
        while i < len(codes):
            c = codes[i]
            if c == 0 or c == 39:
                color = None
            elif 30 <= c <= 37:
                color = BASE16[c - 30]
            elif 90 <= c <= 97:
                color = BASE16[c - 90 + 8]
            elif c == 38 and i + 2 < len(codes) and codes[i + 1] == 5:
                color = xterm256(codes[i + 2]); i += 2
            elif c == 38 and i + 4 < len(codes) and codes[i + 1] == 2:
                color = "#%02x%02x%02x" % tuple(codes[i + 2:i + 5]); i += 4
            i += 1
    emit(text[pos:])
    return "".join(out)

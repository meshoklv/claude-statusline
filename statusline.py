#!/usr/bin/env python3
"""claude-statusline: a configurable status line for Claude Code.

One process per render, no network. Everything comes from the JSON that Claude
Code writes to stdin (model, context window, rate limits, cost, effort...) plus
a few cheap local lookups (git branch, MCP config files, the session transcript).
Standard library only, Python 3.8+.

Config: ~/.config/claude-statusline/config.json  (see config.example.json)
Env:    CLAUDE_STATUSLINE_CONFIG, CLAUDE_STATUSLINE_LANG, CLAUDE_STATUSLINE_PRESET,
        CLAUDE_STATUSLINE_STATE_DIR, NO_COLOR
"""
import copy
import json
import os
import subprocess
import sys
import time

VERSION = "0.1.0"
HOME = os.path.expanduser("~")
RESET = "\033[0m"

ALL_SEGMENTS = ["model", "context", "five_hour", "weekly", "mode", "tokens",
                "cost", "dir", "git", "mcps", "uptime"]

PRESETS = {
    "full": ["model", "context", "five_hour", "weekly", "mode", "cost",
             "dir", "git", "mcps", "uptime"],
    "minimal": ["model", "context", "five_hour"],
    "compact": ["context", "five_hour", "weekly", "cost", "dir"],
}

DEFAULTS = {
    "lang": "en",
    "preset": "full",
    "segments": None,            # explicit list overrides the preset
    "separator": " | ",
    "bar": {"width": 10, "filled": "━", "empty": "━"},
    "colors": {"ok": "green", "warn": "yellow", "crit": "red", "dim": "#5a5a5a"},
    "thresholds": {
        "context_red_tokens": 300000,   # absolute token load that turns the bar red
        "cost_red_usd": 10.0,           # session cost shown red from here
        "limit_warn_left": 50,          # % left of a limit: below -> warn colour
        "limit_crit_left": 20,          # % left of a limit: below -> crit colour
        "burn_min_span_s": 300,         # samples needed before projecting
    },
    "labels": {"five_hour": "H", "weekly": "W"},
    "burn_projection": True,
    "git_cache_s": 5,
    "mcp_cache_s": 60,
}

I18N = {
    "en": {"session": "session", "tok": "tok", "out": "out", "mcps": "MCPs"},
    "ru": {"session": "сессия", "tok": "ток",
           "out": "вых", "mcps": "MCP"},
}

NAMED = {"black": 0, "red": 1, "green": 2, "yellow": 3, "blue": 4, "magenta": 5,
         "cyan": 6, "white": 7, "gray": 8, "grey": 8}


# ---------------------------------------------------------------- config
def deep_merge(base, extra):
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def config_path():
    env = os.environ.get("CLAUDE_STATUSLINE_CONFIG")
    if env:
        return env
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")
    return os.path.join(base, "claude-statusline", "config.json")


def load_config():
    cfg = copy.deepcopy(DEFAULTS)
    try:
        with open(config_path(), encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            deep_merge(cfg, user)
    except Exception:
        pass                              # missing/broken config -> defaults
    if os.environ.get("CLAUDE_STATUSLINE_LANG"):
        cfg["lang"] = os.environ["CLAUDE_STATUSLINE_LANG"]
    if os.environ.get("CLAUDE_STATUSLINE_PRESET"):
        cfg["preset"] = os.environ["CLAUDE_STATUSLINE_PRESET"]
        cfg["segments"] = None
    return cfg


def state_dir():
    d = os.environ.get("CLAUDE_STATUSLINE_STATE_DIR")
    if d:
        return d
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(HOME, ".cache")
    return os.path.join(base, "claude-statusline")


def now():
    try:
        return float(os.environ["CLAUDE_STATUSLINE_NOW"])   # deterministic tests
    except Exception:
        return time.time()


# ---------------------------------------------------------------- colours
TRUECOLOR = os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")
NO_COLOR = bool(os.environ.get("NO_COLOR"))


def _hex_to_256(r, g, b):
    if abs(r - g) < 10 and abs(g - b) < 10:
        return 232 + min(23, max(0, (r - 8) * 24 // 247))
    q = lambda v: min(5, max(0, (v - 35) // 40 if v >= 35 else 0))
    return 16 + 36 * q(r) + 6 * q(g) + q(b)


def sgr(spec):
    """Colour spec -> SGR escape. Accepts a name, '#rrggbb' or a 0-255 number."""
    if NO_COLOR or spec in (None, "", "none"):
        return ""
    try:
        if isinstance(spec, int):
            return f"\033[38;5;{spec}m"
        spec = str(spec).strip().lower()
        if spec.startswith("#") and len(spec) == 7:
            r, g, b = (int(spec[i:i + 2], 16) for i in (1, 3, 5))
            if TRUECOLOR:
                return f"\033[38;2;{r};{g};{b}m"
            return f"\033[38;5;{_hex_to_256(r, g, b)}m"
        if spec.isdigit():
            return f"\033[38;5;{int(spec)}m"
        if spec in NAMED:
            n = NAMED[spec]
            return f"\033[{30 + n}m" if n < 8 else "\033[90m"
    except Exception:
        pass
    return ""


class Ctx:
    """Everything a segment needs, bundled."""

    def __init__(self, data, cfg, state):
        self.data, self.cfg, self.state = data, cfg, state
        self.dirty = False
        self.t = cfg["thresholds"]
        self.tr = I18N.get(cfg["lang"], I18N["en"])

    def col(self, role, text):
        s = sgr(self.cfg["colors"].get(role, role))
        return f"{s}{text}{RESET}" if s else str(text)


# ---------------------------------------------------------------- state
def read_state():
    try:
        with open(os.path.join(state_dir(), "state.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_state(state):
    try:
        os.makedirs(state_dir(), mode=0o700, exist_ok=True)
        path = os.path.join(state_dir(), "state.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except Exception:
        pass


# ---------------------------------------------------------------- helpers
def fmt_k(n):
    return f"{n // 1000}K" if n >= 1000 else str(n)


def fmt_tok(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    return fmt_k(n)


def hhmm(seconds):
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}h{(seconds % 3600) // 60}m"
    return f"{max(seconds, 0) // 60}m"


def limit_role(cx, left):
    if left > cx.t["limit_warn_left"]:
        return "ok"
    return "warn" if left > cx.t["limit_crit_left"] else "crit"


# ---------------------------------------------------------------- segments
def seg_model(cx):
    name = (cx.data.get("model") or {}).get("display_name")
    return f"[{name or '?'}]"


def seg_context(cx):
    ctx = cx.data.get("context_window") or {}
    used_pct = float(ctx.get("used_percentage") or 0)
    size = int(ctx.get("context_window_size") or 200000)
    tokens = int(used_pct * size / 100)
    red_at = min(size * 0.8, cx.t["context_red_tokens"]) or 1
    role = "ok" if tokens < red_at / 2 else ("warn" if tokens < red_at else "crit")

    bar = cx.cfg["bar"]
    width = max(1, int(bar.get("width", 10)))
    filled = min(max(int(round(used_pct / 100 * width)), 0), width)
    if filled == 0 and used_pct > 0:          # never an empty bar for a live session
        filled = 1
    out = cx.col(role, str(bar.get("filled", "━")) * filled)
    if filled < width:
        out += cx.col("dim", str(bar.get("empty", "━")) * (width - filled))
    return f"{out} {int(round(used_pct))}% ({fmt_k(tokens)}/{fmt_k(size)})"


def _rl(cx, key):
    return ((cx.data.get("rate_limits") or {}).get(key)) or {}


def seg_five_hour(cx):
    five = _rl(cx, "five_hour")
    used = five.get("used_percentage")
    if used is None:
        return None
    left = int(100 - float(used))
    reset_at = five.get("resets_at")
    t = now()
    text = f"{cx.cfg['labels']['five_hour']}:{left}%"
    if reset_at and reset_at - t > 0:
        text += f" {hhmm(reset_at - t)}"
    out = cx.col(limit_role(cx, left), text)
    if cx.cfg["burn_projection"]:
        proj = record_and_project(cx, float(used), reset_at)
        if proj and reset_at and proj < reset_at:
            out += " " + cx.col("crit", f"\U0001F525{hhmm(proj - t)}")
    return out


def seg_weekly(cx):
    week = _rl(cx, "seven_day")
    used = week.get("used_percentage")
    if used is None:
        return None
    left = int(100 - float(used))
    return cx.col(limit_role(cx, left), f"{cx.cfg['labels']['weekly']}:{left}%")


def seg_mode(cx):
    d = cx.data
    effort = (d.get("effort") or {}).get("level")
    bits = []
    if effort:
        bits.append({"low": "L", "medium": "M", "high": "H",
                     "xhigh": "XH", "max": "MAX"}.get(effort, effort))
    if (d.get("thinking") or {}).get("enabled"):
        bits.append("+t")
    if d.get("fast_mode"):
        bits.append("+fast")
    return "⚙" + "".join(bits) if bits else None


def seg_cost(cx):
    usd = (cx.data.get("cost") or {}).get("total_cost_usd")
    if not isinstance(usd, (int, float)) or usd <= 0:
        return None
    role = "crit" if usd >= cx.t["cost_red_usd"] else "warn"
    return f"{cx.tr['session']} {cx.col(role, f'${usd:.2f}')}"


def seg_dir(cx):
    cwd = cx.cwd
    return os.path.basename(cwd.rstrip("/")) or cwd


def seg_git(cx):
    cache = cx.state.get("git", {})
    entry, t = cache.get(cx.cwd), now()
    if entry and t - entry.get("t", 0) < cx.cfg["git_cache_s"]:
        branch = entry.get("branch")
    else:
        branch = git_branch(cx.cwd)
        cache[cx.cwd] = {"t": t, "branch": branch}
        if len(cache) > 20:
            for k, _ in sorted(cache.items(), key=lambda kv: kv[1]["t"])[:-20]:
                cache.pop(k, None)
        cx.state["git"], cx.dirty = cache, True
    return f"git:({branch})" if branch else None


def git_branch(cwd):
    try:
        return subprocess.run(
            ["git", "-c", "core.useReplaceRefs=false", "-c", "gc.auto=0",
             "branch", "--show-current"],
            cwd=cwd, capture_output=True, text=True, timeout=2,
        ).stdout.strip() or None
    except Exception:
        return None


def claude_home():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")


def seg_mcps(cx):
    cache = cx.state.get("mcps")
    if cache is not None and now() - cache.get("k", 0) < cx.cfg["mcp_cache_s"] \
            and cache.get("dir") == cx.project_dir:
        n = cache.get("n", 0)
    else:
        names = set()
        ch = claude_home()
        for f in (os.path.join(ch, "settings.json"),
                  os.path.join(ch, "settings.local.json"),
                  os.path.join(cx.project_dir, ".claude", "settings.json"),
                  os.path.join(cx.project_dir, ".claude", "settings.local.json")):
            try:
                with open(f, encoding="utf-8") as fh:
                    names.update((json.load(fh).get("mcpServers") or {}).keys())
            except Exception:
                pass
        try:   # project-level .mcp.json and plugin-provided servers
            with open(os.path.join(cx.project_dir, ".mcp.json"), encoding="utf-8") as fh:
                names.update((json.load(fh).get("mcpServers") or {}).keys())
        except Exception:
            pass
        try:
            for e in os.scandir(os.path.join(ch, "plugins", "cache")):
                try:
                    with open(os.path.join(e.path, ".mcp.json"), encoding="utf-8") as fh:
                        names.update(json.load(fh).keys())
                except Exception:
                    pass
        except Exception:
            pass
        n = len(names)
        cx.state["mcps"] = {"k": now(), "n": n, "dir": cx.project_dir}
        cx.dirty = True
    return f"{n} {cx.tr['mcps']}" if n else None


# --- session uptime: start = first timestamp after the last >30 min gap in the transcript
def scan_transcript_start(path):
    if not path or not os.path.isfile(path):
        return None
    from datetime import datetime, timezone
    gap, prev, start = 30 * 60, None, None
    try:
        size = os.path.getsize(path)
        with open(path, encoding="utf-8", errors="replace") as f:
            if size > 5_000_000:                  # only the tail of huge transcripts
                f.seek(size - 5_000_000)
                f.readline()
            for line in f:
                try:
                    ts_str = json.loads(line).get("timestamp")
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except Exception:
                    continue
                ts = ts.replace(tzinfo=ts.tzinfo or timezone.utc).timestamp()
                if prev is None or (ts - prev) > gap:
                    start = ts
                prev = ts
    except Exception:
        return None
    return start


def seg_uptime(cx):
    sid = cx.data.get("session_id") or "?"
    cache = cx.state.get("session_start", {})
    start = cache.get(sid)
    if start is None:
        start = scan_transcript_start(cx.data.get("transcript_path"))
        if start is None:
            return "⏱ 0m"
        cache[sid] = start
        if len(cache) > 20:
            for k, _ in sorted(cache.items(), key=lambda kv: kv[1])[:-20]:
                cache.pop(k, None)
        cx.state["session_start"], cx.dirty = cache, True
    mins = max(int((now() - start) / 60), 0)
    return "⏱ " + (f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m")


# --- tokens: sum over the session transcript + its sub-agent transcripts (opt-in)
def _vec(u):
    g = lambda k: int(u.get(k) or 0) if isinstance(u, dict) else 0
    return [g("input_tokens"), g("cache_creation_input_tokens"),
            g("cache_read_input_tokens"), g("output_tokens")]


def _scan_file(path, entry):
    """Incremental read. One message is logged on several lines with the same id:
    count it once (the last line wins)."""
    size = os.path.getsize(path)
    if entry and entry.get("size") == size:
        return entry, False
    if not entry or size < entry.get("off", 0):
        entry = {"off": 0, "tot": [0] * 4, "lid": None, "lu": [0] * 4}
    tot, lid, lu = list(entry["tot"]), entry.get("lid"), list(entry.get("lu") or [0] * 4)
    off = entry["off"]
    with open(path, "rb") as f:
        f.seek(off)
        for raw in f:
            if not raw.endswith(b"\n"):
                break
            off += len(raw)
            if b'"usage"' not in raw:
                continue
            try:
                d = json.loads(raw)
            except Exception:
                continue
            m = d.get("message")
            if d.get("type") != "assistant" or not isinstance(m, dict):
                continue
            vec, mid = _vec(m.get("usage")), m.get("id")
            if mid and mid == lid:
                tot = [t - a + b for t, a, b in zip(tot, lu, vec)]
            else:
                tot = [t + b for t, b in zip(tot, vec)]
            lid, lu = mid, vec
    return {"off": off, "size": size, "tot": tot, "lid": lid, "lu": lu, "t": now()}, True


def seg_tokens(cx):
    tp = cx.data.get("transcript_path")
    if not tp or not os.path.isfile(tp):
        return None
    files = [tp]
    sub = os.path.join(tp[:-6] if tp.endswith(".jsonl") else tp, "subagents")
    try:
        files += [e.path for e in os.scandir(sub) if e.name.endswith(".jsonl") and e.is_file()]
    except Exception:
        pass
    cache, total = cx.state.get("tok", {}), [0] * 4
    for p in files:
        try:
            entry, d = _scan_file(p, cache.get(p))
        except Exception:
            continue
        if d:
            cache[p], cx.dirty = entry, True
        total = [a + b for a, b in zip(total, entry["tot"])]
    if len(cache) > 60:
        for k, _ in sorted(cache.items(), key=lambda kv: kv[1].get("t", 0))[:-60]:
            cache.pop(k, None)
    cx.state["tok"] = cache
    if sum(total) == 0:
        return None
    return f"\U0001FA99 {fmt_tok(sum(total))} {cx.tr['tok']} · {cx.tr['out']} {fmt_tok(total[3])}"


# --- burn rate: project when the 5h window runs dry, from measured samples only
def record_and_project(cx, five_used, reset_at):
    path = os.path.join(state_dir(), "trend.jsonl")
    t, samples = now(), []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    s = json.loads(line)
                except Exception:
                    continue
                if isinstance(s, dict) and "t" in s and "u" in s:
                    samples.append(s)
    except Exception:
        samples = []
    if reset_at:
        samples = [s for s in samples if s.get("r") == reset_at]
    last = samples[-1] if samples else None
    if last is None or last.get("u") != five_used or (t - last.get("t", 0)) > 600:
        samples.append({"t": t, "u": five_used, "r": reset_at})
        try:
            os.makedirs(state_dir(), mode=0o700, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": t, "u": five_used, "r": reset_at}) + "\n")
            trim_file(path, 300)
        except Exception:
            pass
    if len(samples) < 2:
        return None
    first = samples[0]
    span, delta = t - first["t"], five_used - first["u"]
    if span < cx.t["burn_min_span_s"] or delta <= 0 or five_used >= 100:
        return None
    return t + (100 - five_used) / (delta / span)


def trim_file(path, keep):
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > keep:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines[-keep:])
    except Exception:
        pass


SEGMENTS = {
    "model": seg_model, "context": seg_context, "five_hour": seg_five_hour,
    "weekly": seg_weekly, "mode": seg_mode, "tokens": seg_tokens, "cost": seg_cost,
    "dir": seg_dir, "git": seg_git, "mcps": seg_mcps, "uptime": seg_uptime,
}


# ---------------------------------------------------------------- main
def render(data, cfg, state):
    cx = Ctx(data, cfg, state)
    ws = data.get("workspace") or {}
    cx.cwd = ws.get("current_dir") or data.get("cwd") or HOME
    cx.project_dir = ws.get("project_dir") or cx.cwd
    order = cfg.get("segments") or PRESETS.get(cfg.get("preset"), PRESETS["full"])
    parts = []
    for name in order:
        fn = SEGMENTS.get(name)
        if not fn:
            continue
        try:
            s = fn(cx)
        except Exception:
            s = None                           # a broken segment must never break the line
        if s:
            parts.append(s)
    return cfg["separator"].join(parts), cx


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-V", "--version"):
        print(f"claude-statusline {VERSION}")
        return
    try:
        data = json.load(sys.stdin)
        if not isinstance(data, dict):
            return
    except Exception:
        return
    cfg = load_config()
    state = read_state()
    line, cx = render(data, cfg, state)
    if cx.dirty:
        write_state(state)
    sys.stdout.write(line + "\n")


if __name__ == "__main__":
    main()

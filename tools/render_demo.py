#!/usr/bin/env python3
"""Build docs/demo.html: run the real statusline.py on synthetic input and convert its ANSI to HTML.

    python3 tools/render_demo.py
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from ansi2html import convert  # noqa: E402

NOW = 1_800_000_000


def base(**kw):
    d = {
        "session_id": "demo",
        "model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
        "workspace": {"current_dir": "/home/you/my-app", "project_dir": "/home/you/my-app"},
        "context_window": {"context_window_size": 1000000, "used_percentage": 12},
        "cost": {"total_cost_usd": 2.84},
        "effort": {"level": "medium"}, "thinking": {"enabled": True},
        "rate_limits": {"five_hour": {"used_percentage": 9, "resets_at": NOW + 4 * 3600 + 21 * 60},
                        "seven_day": {"used_percentage": 27, "resets_at": NOW + 3 * 86400}},
    }
    d.update(kw)
    return d


SCENARIOS = [
    ("Full", "Everything on, plenty of headroom.", base(), {}, None),
    ("Minimal", "preset: minimal - model, context, 5-hour limit.", base(),
     {"preset": "minimal"}, None),
    ("Russian", "lang: ru - labels are localised, the rest stays compact.",
     base(cost={"total_cost_usd": 18.40}), {"lang": "ru"}, None),
    ("Running dry", "420K tokens in a 1M window, 5-hour limit almost gone, burn-rate flame.",
     base(context_window={"context_window_size": 1000000, "used_percentage": 42},
          cost={"total_cost_usd": 296.47}, effort={"level": "high"},
          rate_limits={"five_hour": {"used_percentage": 93, "resets_at": NOW + 2 * 3600 + 10 * 60},
                       "seven_day": {"used_percentage": 84, "resets_at": NOW + 86400}}),
     {}, {"t": NOW - 1800, "u": 78.0, "r": NOW + 2 * 3600 + 10 * 60}),
]

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>claude-statusline demo</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 48px 20px; background: #0d1117; color: #c9d1d9;
         font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
  main { max-width: 980px; margin: 0 auto; }
  h1 { font-size: 28px; margin: 0 0 6px; color: #f0f6fc; }
  .sub { color: #8b949e; margin: 0 0 36px; }
  .card { margin: 0 0 30px; }
  .card h2 { font-size: 15px; margin: 0 0 2px; color: #f0f6fc; }
  .card p { margin: 0 0 10px; color: #8b949e; font-size: 13px; }
  .term { background: #1e1e1e; border: 1px solid #30363d; border-radius: 10px; overflow: hidden;
          box-shadow: 0 10px 30px rgba(0,0,0,.45); }
  .bar { display: flex; gap: 7px; align-items: center; padding: 10px 14px; background: #2b2b2b;
         border-bottom: 1px solid #1a1a1a; }
  .dot { width: 12px; height: 12px; border-radius: 50%; }
  .bar .title { margin-left: 10px; color: #9a9a9a; font-size: 12px; }
  pre { margin: 0; padding: 16px 18px 18px; overflow-x: auto; color: #d4d4d4;
        font: 14px/1.6 Menlo, "SF Mono", Consolas, "DejaVu Sans Mono", monospace; }
  .muted { color: #6a6a6a; }
  .prompt { color: #d4d4d4; }
  .box { border: 1px solid #555; border-radius: 6px; padding: 2px 10px; margin-bottom: 8px; }
  .line { white-space: pre; }
</style></head>
<body><main>
<h1>claude-statusline</h1>
<p class="sub">Real output of <code>statusline.py</code>, ANSI colours converted to HTML. Four presets of the same line.</p>
%(cards)s
</main></body></html>
"""

CARD = """<section class="card">
  <h2>%(title)s</h2><p>%(note)s</p>
  <div class="term">
    <div class="bar"><span class="dot" style="background:#ff5f56"></span><span class="dot" style="background:#ffbd2e"></span><span class="dot" style="background:#27c93f"></span><span class="title">claude - ~/my-app</span></div>
<pre><span class="muted">...</span>
<div class="box prompt">&gt; <span class="muted">Try "refactor the parser"</span></div><div class="line">%(line)s</div></pre>
  </div>
</section>"""


def main():
    cards = []
    for title, note, data, cfg, trend in SCENARIOS:
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "state"))
        if trend:
            with open(os.path.join(tmp, "state", "trend.jsonl"), "w") as f:
                f.write(json.dumps(trend) + "\n")
        with open(os.path.join(tmp, "config.json"), "w") as f:
            json.dump(cfg, f)
        tp = os.path.join(tmp, "transcript.jsonl")        # session started ~1h 12m "ago"
        with open(tp, "w") as f:
            for back in (4320, 3000, 1500, 60):
                f.write(json.dumps({"timestamp": datetime.fromtimestamp(NOW - back, timezone.utc).isoformat()}) + "\n")
        data = dict(data, transcript_path=tp)
        env = {"PATH": os.environ["PATH"], "HOME": tmp, "COLORTERM": "truecolor",
               "CLAUDE_STATUSLINE_NOW": str(NOW),
               "CLAUDE_STATUSLINE_STATE_DIR": os.path.join(tmp, "state"),
               "CLAUDE_STATUSLINE_CONFIG": os.path.join(tmp, "config.json")}
        out = subprocess.run([sys.executable, os.path.join(ROOT, "statusline.py")],
                             input=json.dumps(data), capture_output=True, text=True, env=env,
                             check=True).stdout.rstrip("\n")
        cards.append(CARD % {"title": title, "note": note, "line": convert(out)})
    path = os.path.join(ROOT, "docs", "demo.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(PAGE.replace("%(cards)s", "\n".join(cards)))
    print("wrote", path)


if __name__ == "__main__":
    main()

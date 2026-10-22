"""Run statusline.py on synthetic input shaped like Claude Code's stdin JSON."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "statusline.py")
NOW = 1_800_000_000
ANSI = re.compile(r"\033\[[0-9;]*m")


def run(data, env=None, config=None, tmp=None):
    tmp = tmp or tempfile.mkdtemp()
    e = {"PATH": os.environ["PATH"], "HOME": tmp, "CLAUDE_STATUSLINE_NOW": str(NOW),
         "CLAUDE_STATUSLINE_STATE_DIR": os.path.join(tmp, "state"),
         "CLAUDE_STATUSLINE_CONFIG": os.path.join(tmp, "config.json")}
    e.update(env or {})
    if config is not None:
        with open(e["CLAUDE_STATUSLINE_CONFIG"], "w") as f:
            json.dump(config, f)
    p = subprocess.run([sys.executable, SCRIPT], input=json.dumps(data),
                       capture_output=True, text=True, env=e, timeout=15)
    assert p.returncode == 0, p.stderr
    return p.stdout


def plain(s):
    return ANSI.sub("", s).strip()


def payload(**kw):
    d = {
        "session_id": "s1",
        "model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
        "workspace": {"current_dir": "/tmp/demo-project", "project_dir": "/tmp/demo-project"},
        "context_window": {"context_window_size": 1000000, "used_percentage": 12},
        "cost": {"total_cost_usd": 3.5},
        "effort": {"level": "medium"}, "thinking": {"enabled": True},
        "rate_limits": {"five_hour": {"used_percentage": 9, "resets_at": NOW + 4 * 3600 + 21 * 60},
                        "seven_day": {"used_percentage": 73, "resets_at": NOW + 86400}},
    }
    d.update(kw)
    return d


class Basics(unittest.TestCase):
    def test_full_line_en(self):
        out = plain(run(payload()))
        self.assertEqual(
            out, "[Opus 5.5] | ━━━━━━━━━━ 12% (120K/1000K)"
                 " | H:91% 4h21m | W:27% | ⚙M+t | session $3.50 | demo-project | ⏱ 0m")

    def test_ru(self):
        out = plain(run(payload(), env={"CLAUDE_STATUSLINE_LANG": "ru"}))
        self.assertIn("сессия $3.50", out)
        self.assertNotIn("session", out)

    def test_ru_from_config(self):
        out = plain(run(payload(), config={"lang": "ru"}))
        self.assertIn("сессия", out)

    def test_no_color_has_no_escapes(self):
        self.assertNotIn("\033", run(payload(), env={"NO_COLOR": "1"}))

    def test_colors_present_by_default(self):
        self.assertIn("\033[", run(payload()))


class Context(unittest.TestCase):
    def ctx(self, pct, size=1000000, **env):
        d = payload(context_window={"context_window_size": size, "used_percentage": pct})
        return run(d, env=env)

    def test_low_context_is_green_with_one_cell(self):
        out = self.ctx(0.2)
        self.assertIn("\033[32m━\033[0m", out)
        self.assertEqual(plain(out).split(" | ")[1].count("━"), 10)

    def test_absolute_tokens_turn_bar_red_even_on_1m_window(self):
        out = self.ctx(42)              # 420K tokens of 1M
        self.assertIn("\033[31m", out)
        self.assertIn("42% (420K/1000K)", plain(out))

    def test_full_small_window(self):
        out = plain(self.ctx(95, size=200000))
        self.assertIn("95% (190K/200K)", out)

    def test_bar_width_config(self):
        d = payload(context_window={"context_window_size": 200000, "used_percentage": 50})
        out = plain(run(d, config={"bar": {"width": 20}}))
        self.assertEqual(out.split(" | ")[1].split(" ")[0], "━" * 20)

    def test_missing_context_does_not_crash(self):
        d = payload()
        d.pop("context_window")
        self.assertIn("0% (0/200K)", plain(run(d)))


class Limits(unittest.TestCase):
    def test_no_rate_limits_hides_segments(self):
        d = payload()
        d.pop("rate_limits")
        out = plain(run(d))
        self.assertNotIn("H:", out)
        self.assertNotIn("W:", out)

    def test_almost_exhausted_is_red(self):
        d = payload(rate_limits={"five_hour": {"used_percentage": 96, "resets_at": NOW + 1500},
                                 "seven_day": {"used_percentage": 91, "resets_at": NOW + 9999}})
        out = run(d)
        self.assertIn("\033[31mH:4% 25m\033[0m", out)
        self.assertIn("\033[31mW:9%\033[0m", out)

    def test_mid_limit_is_yellow(self):
        d = payload(rate_limits={"five_hour": {"used_percentage": 70, "resets_at": NOW + 600}})
        self.assertIn("\033[33mH:30% 10m\033[0m", run(d))

    def test_only_week_limit_present_with_no_five_hour(self):
        d = payload(rate_limits={"seven_day": {"used_percentage": 10}})
        out = plain(run(d))
        self.assertIn("W:90%", out)
        self.assertNotIn("H:", out)

    def test_burn_projection_shows_flame(self):
        tmp = tempfile.mkdtemp()
        reset = NOW + 4 * 3600
        os.makedirs(os.path.join(tmp, "state"))
        with open(os.path.join(tmp, "state", "trend.jsonl"), "w") as f:
            f.write(json.dumps({"t": NOW - 1800, "u": 40.0, "r": reset}) + "\n")
        d = payload(rate_limits={"five_hour": {"used_percentage": 60, "resets_at": reset}})
        out = plain(run(d, tmp=tmp))       # 20% per 30 min -> dry in 1h
        self.assertIn("\U0001F525" + "1h0m", out)

    def test_burn_projection_off_by_config(self):
        tmp = tempfile.mkdtemp()
        reset = NOW + 4 * 3600
        os.makedirs(os.path.join(tmp, "state"))
        with open(os.path.join(tmp, "state", "trend.jsonl"), "w") as f:
            f.write(json.dumps({"t": NOW - 1800, "u": 40.0, "r": reset}) + "\n")
        d = payload(rate_limits={"five_hour": {"used_percentage": 60, "resets_at": reset}})
        self.assertNotIn("\U0001F525", run(d, config={"burn_projection": False}, tmp=tmp))

    def test_no_flame_without_history(self):
        self.assertNotIn("\U0001F525", run(payload()))


class Segments(unittest.TestCase):
    def test_segment_order_and_selection(self):
        out = plain(run(payload(), config={"segments": ["dir", "model"]}))
        self.assertEqual(out, "demo-project | [Opus 5.5]")

    def test_presets(self):
        out = plain(run(payload(), env={"CLAUDE_STATUSLINE_PRESET": "minimal"}))
        self.assertEqual(len(out.split(" | ")), 3)
        self.assertNotIn("session", out)

    def test_separator(self):
        out = plain(run(payload(), config={"segments": ["model", "dir"], "separator": " · "}))
        self.assertEqual(out, "[Opus 5.5] · demo-project")

    def test_cost_hidden_at_zero_and_red_when_high(self):
        self.assertNotIn("session", plain(run(payload(cost={"total_cost_usd": 0}))))
        self.assertIn("\033[31m$296.47", run(payload(cost={"total_cost_usd": 296.47})))
        self.assertIn("\033[33m$3.50", run(payload()))

    def test_mode_flags(self):
        d = payload(effort={"level": "xhigh"}, thinking={"enabled": False}, fast_mode=True)
        self.assertIn("⚙XH+fast", plain(run(d)))
        d = payload()
        d.pop("effort"); d.pop("thinking")
        self.assertNotIn("⚙", plain(run(d)))

    def test_custom_colors_and_labels(self):
        out = run(payload(), config={"colors": {"ok": "cyan"}, "labels": {"five_hour": "5h"}})
        self.assertIn("\033[36m5h:91%", out)

    def test_unknown_segment_and_broken_config_are_ignored(self):
        out = plain(run(payload(), config={"segments": ["nope", "model"]}))
        self.assertEqual(out, "[Opus 5.5]")
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "config.json"), "w") as f:
            f.write("{not json")
        self.assertIn("[Opus 5.5]", plain(run(payload(), tmp=tmp)))

    def test_garbage_stdin_prints_nothing(self):
        e = {"PATH": os.environ["PATH"], "HOME": tempfile.mkdtemp()}
        p = subprocess.run([sys.executable, SCRIPT], input="not json", capture_output=True,
                           text=True, env=e)
        self.assertEqual((p.returncode, p.stdout), (0, ""))


class Files(unittest.TestCase):
    def test_mcp_count_from_project_mcp_json_and_settings(self):
        proj = tempfile.mkdtemp()
        with open(os.path.join(proj, ".mcp.json"), "w") as f:
            json.dump({"mcpServers": {"a": {}, "b": {}}}, f)
        os.makedirs(os.path.join(proj, ".claude"))
        with open(os.path.join(proj, ".claude", "settings.json"), "w") as f:
            json.dump({"mcpServers": {"b": {}, "c": {}}}, f)
        d = payload(workspace={"current_dir": proj, "project_dir": proj})
        self.assertIn("3 MCPs", plain(run(d)))

    def test_git_branch(self):
        proj = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", "-b", "feature-x", proj], check=True)
        d = payload(workspace={"current_dir": proj, "project_dir": proj})
        self.assertIn("git:(feature-x)", plain(run(d)))

    def test_uptime_from_transcript_gap(self):
        tmp = tempfile.mkdtemp()
        tp = os.path.join(tmp, "t.jsonl")
        from datetime import datetime, timezone
        iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()
        with open(tp, "w") as f:       # old burst, a long gap, then the real session start 2h05m ago
            for t in (NOW - 90000, NOW - 89900, NOW - 7500, NOW - 6000, NOW - 4500, NOW - 3000, NOW - 1500, NOW - 60):
                f.write(json.dumps({"timestamp": iso(t)}) + "\n")
        out = plain(run(payload(transcript_path=tp), tmp=tmp))
        self.assertIn("⏱ 2h 5m", out)

    def test_tokens_segment_counts_message_once_and_en_ru(self):
        tmp = tempfile.mkdtemp()
        tp = os.path.join(tmp, "t.jsonl")
        msg = lambda i, inp, out: json.dumps({"type": "assistant", "message": {
            "id": i, "usage": {"input_tokens": inp, "cache_creation_input_tokens": 0,
                               "cache_read_input_tokens": 100000, "output_tokens": out}}})
        with open(tp, "w") as f:       # same id twice (streamed lines): only the last counts
            f.write(msg("m1", 10, 5) + "\n" + msg("m1", 10, 2000) + "\n" + msg("m2", 20, 3000) + "\n")
            f.write(msg("m3", 20, 3000) + "\n")
        cfg = {"segments": ["tokens"]}
        self.assertEqual(plain(run(payload(transcript_path=tp), config=cfg, tmp=tmp)),
                         "\U0001FA99 308K tok · out 8K")
        cfg["lang"] = "ru"
        self.assertEqual(plain(run(payload(transcript_path=tp), config=cfg, tmp=tmp)),
                         "\U0001FA99 308K ток · вых 8K")


if __name__ == "__main__":
    unittest.main()

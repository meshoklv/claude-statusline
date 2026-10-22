"""install.sh against a throw-away HOME: install, backup, refuse-to-clobber, restore."""
import glob
import json
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL = os.path.join(ROOT, "install.sh")


class Install(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.cdir = os.path.join(self.home, ".claude")
        self.settings = os.path.join(self.cdir, "settings.json")
        self.env = {"PATH": os.environ["PATH"], "HOME": self.home}

    def sh(self, *args):
        return subprocess.run(["bash", INSTALL, *args], capture_output=True, text=True, env=self.env)

    def read(self):
        with open(self.settings) as f:
            return json.load(f)

    def write(self, obj):
        os.makedirs(self.cdir, exist_ok=True)
        with open(self.settings, "w") as f:
            json.dump(obj, f)

    def test_fresh_install_and_uninstall(self):
        p = self.sh()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.cdir, "claude-statusline.py")))
        sl = self.read()["statusLine"]
        self.assertEqual(sl["type"], "command")
        self.assertIn("claude-statusline.py", sl["command"])
        self.assertTrue(os.path.isfile(os.path.join(self.home, ".config/claude-statusline/config.json")))
        # the configured command really renders
        out = subprocess.run(sl["command"], shell=True, input='{"model":{"display_name":"T"}}',
                             capture_output=True, text=True, env=self.env).stdout
        self.assertIn("[T]", out)
        p = self.sh("--uninstall")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("statusLine", self.read())
        self.assertFalse(os.path.exists(os.path.join(self.cdir, "claude-statusline.py")))

    def test_keeps_other_settings_and_makes_backup(self):
        self.write({"theme": "dark", "permissions": {"allow": ["Bash(ls)"]}})
        self.assertEqual(self.sh().returncode, 0)
        s = self.read()
        self.assertEqual(s["theme"], "dark")
        self.assertEqual(s["permissions"], {"allow": ["Bash(ls)"]})
        self.assertEqual(len(glob.glob(self.settings + ".bak-*")), 1)

    def test_refuses_to_replace_foreign_statusline_without_force(self):
        old = {"type": "command", "command": "echo old"}
        self.write({"statusLine": old})
        p = self.sh()
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.read()["statusLine"], old)
        self.assertFalse(os.path.exists(os.path.join(self.cdir, "claude-statusline.py")))

    def test_force_then_uninstall_restores_previous(self):
        old = {"type": "command", "command": "echo old"}
        self.write({"statusLine": old, "x": 1})
        self.assertEqual(self.sh("--force").returncode, 0)
        self.assertIn("claude-statusline.py", self.read()["statusLine"]["command"])
        self.assertEqual(self.sh("--uninstall").returncode, 0)
        self.assertEqual(self.read(), {"statusLine": old, "x": 1})

    def test_reinstall_is_idempotent_and_keeps_previous(self):
        old = {"type": "command", "command": "echo old"}
        self.write({"statusLine": old})
        self.sh("--force")
        self.assertEqual(self.sh().returncode, 0)       # second run: already ours
        self.sh("--uninstall")
        self.assertEqual(self.read()["statusLine"], old)

    def test_invalid_settings_untouched(self):
        os.makedirs(self.cdir)
        with open(self.settings, "w") as f:
            f.write("{bad")
        p = self.sh()
        self.assertNotEqual(p.returncode, 0)
        with open(self.settings) as f:
            self.assertEqual(f.read(), "{bad")
        self.assertFalse(os.path.exists(os.path.join(self.cdir, "claude-statusline.py")))

    def test_lang_option_writes_config(self):
        self.assertEqual(self.sh("--lang", "ru", "--preset", "minimal").returncode, 0)
        with open(os.path.join(self.home, ".config/claude-statusline/config.json")) as f:
            cfg = json.load(f)
        self.assertEqual((cfg["lang"], cfg["preset"]), ("ru", "minimal"))

    def test_respects_claude_config_dir(self):
        alt = os.path.join(self.home, "alt")
        self.env["CLAUDE_CONFIG_DIR"] = alt
        self.assertEqual(self.sh().returncode, 0)
        self.assertTrue(os.path.isfile(os.path.join(alt, "settings.json")))


if __name__ == "__main__":
    unittest.main()

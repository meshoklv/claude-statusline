#!/usr/bin/env bash
# claude-statusline installer.
#   ./install.sh [--lang ru] [--preset minimal] [--force]   install / update
#   ./install.sh --uninstall                                  remove, restore previous statusLine
# Honors CLAUDE_CONFIG_DIR (default ~/.claude). Needs bash + python3 (3.8+).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SETTINGS="$CLAUDE_DIR/settings.json"
DEST="$CLAUDE_DIR/claude-statusline.py"
PREV="$CLAUDE_DIR/.claude-statusline-prev.json"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/claude-statusline"
CONF="$CONF_DIR/config.json"
MODE=install; LANG_OPT=""; PRESET_OPT=""; FORCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) MODE=uninstall ;;
    --lang) LANG_OPT="${2:?--lang needs a value}"; shift ;;
    --preset) PRESET_OPT="${2:?--preset needs a value}"; shift ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n 2,5p "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
  || { echo "python 3.8+ is required" >&2; exit 1; }
mkdir -p "$CLAUDE_DIR"

# Edits settings.json via python. action: set | restore
edit_settings() {
  SETTINGS="$SETTINGS" PREV="$PREV" DEST="$DEST" FORCE="$FORCE" ACTION="$1" python3 - <<'PY'
import json, os, shutil, sys, time
settings, prev, dest = os.environ["SETTINGS"], os.environ["PREV"], os.environ["DEST"]
action, force = os.environ["ACTION"], os.environ["FORCE"] == "1"
home = os.path.expanduser("~")
shown = dest.replace(home, "$HOME", 1) if dest.startswith(home) else dest
cmd = 'python3 "%s"' % shown
ours = lambda sl: isinstance(sl, dict) and "claude-statusline.py" in str(sl.get("command", ""))

data = {}
if os.path.exists(settings):
    with open(settings, encoding="utf-8") as f:
        raw = f.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        sys.exit("settings.json is not valid JSON - fix it first (nothing was changed): " + settings)
    if not isinstance(data, dict):
        sys.exit("settings.json is not a JSON object (nothing was changed): " + settings)
cur = data.get("statusLine")

if action == "set":
    if cur and not ours(cur) and not force:
        sys.exit("settings.json already has a different statusLine:\n  %s\n"
                 "Re-run with --force to replace it (the old one is saved and restored on --uninstall)."
                 % json.dumps(cur))
    if os.path.exists(settings):
        bak = "%s.bak-%s" % (settings, time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(settings, bak)
        print("backup:", bak)
    if cur and not ours(cur):
        with open(prev, "w", encoding="utf-8") as f:
            json.dump(cur, f)
    data["statusLine"] = {"type": "command", "command": cmd}
else:
    if not ours(cur):
        print("statusLine is not ours - settings.json left untouched")
        sys.exit(0)
    bak = "%s.bak-%s" % (settings, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(settings, bak)
    print("backup:", bak)
    if os.path.exists(prev):
        with open(prev, encoding="utf-8") as f:
            data["statusLine"] = json.load(f)
        os.remove(prev)
        print("restored your previous statusLine")
    else:
        data.pop("statusLine", None)
tmp = settings + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
os.replace(tmp, settings)
PY
}

if [ "$MODE" = uninstall ]; then
  [ -f "$SETTINGS" ] && edit_settings restore
  rm -f "$DEST"
  echo "Removed $DEST. Your config ($CONF) and cache were kept; delete them by hand if you like."
  exit 0
fi

edit_settings set          # fails before copying anything if settings.json is unusable
install -m 0755 "$HERE/statusline.py" "$DEST"

if [ -n "$LANG_OPT$PRESET_OPT" ]; then
  mkdir -p "$CONF_DIR"
  CONF="$CONF" LANG_OPT="$LANG_OPT" PRESET_OPT="$PRESET_OPT" python3 - <<'PY'
import json, os
p = os.environ["CONF"]
try:
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
except Exception:
    cfg = {}
if os.environ["LANG_OPT"]:
    cfg["lang"] = os.environ["LANG_OPT"]
if os.environ["PRESET_OPT"]:
    cfg["preset"] = os.environ["PRESET_OPT"]
    cfg.pop("segments", None)
with open(p, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
PY
  echo "config: $CONF"
elif [ ! -f "$CONF" ]; then
  mkdir -p "$CONF_DIR"
  cp "$HERE/config.example.json" "$CONF"
  echo "config: $CONF (edit to customise)"
fi

echo "Installed: $DEST"
echo "Restart Claude Code (or open a new session) to see the status line."

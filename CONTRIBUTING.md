# Contributing

1. Fork, branch, make your change.
2. `python3 -m unittest discover -s tests -v` must pass (stdlib only, no extra packages).
3. New segment or option? Add a test in `tests/test_statusline.py` that feeds synthetic JSON and checks the line.
4. If the output of the demo scenarios changes, run `python3 tools/render_demo.py` and commit `docs/demo.html`.
5. Open a PR describing what changed and why.

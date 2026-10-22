import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
from ansi2html import convert  # noqa: E402


class Ansi(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(convert("a \033[31mred\033[0m b"), 'a <span style="color:#cd3131">red</span> b')

    def test_truecolor_and_256(self):
        self.assertIn("#5a5a5a", convert("\033[38;2;90;90;90mx\033[0m"))
        self.assertIn("#ff0000", convert("\033[38;5;196mx\033[0m"))

    def test_escapes_html(self):
        self.assertEqual(convert("<b>&"), "&lt;b&gt;&amp;")


if __name__ == "__main__":
    unittest.main()

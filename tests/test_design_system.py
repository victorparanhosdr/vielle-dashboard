from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.styles = []
        self.scripts = []
        self.page = None
        self.ids = []
        self.feed(source)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "body":
            self.page = attrs.get("data-app-page")
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.styles.append(attrs["href"].split("?")[0])
        if tag == "script" and attrs.get("src"):
            self.scripts.append(attrs["src"].split("?")[0])
        if attrs.get("id"):
            self.ids.append(attrs["id"])


class SharedDesignTests(unittest.TestCase):
    def test_every_page_uses_the_shared_foundation_last(self):
        pages = list((ROOT / "static").rglob("*.html"))
        self.assertEqual(len(pages), 10)
        for path in pages:
            with self.subTest(page=path.name):
                page = Page(path.read_text())
                self.assertTrue(page.page)
                self.assertEqual(page.styles[-1], "/design-system.css")
                self.assertEqual(len(page.ids), len(set(page.ids)))
                if page.page != "login":
                    self.assertIn("/app-shell.js", page.scripts)
                    self.assertIn("/body-icons.js", page.scripts)

    def test_public_pages_do_not_load_protected_session_code(self):
        pricing = Page((ROOT / "static/pricing.html").read_text())
        self.assertNotIn("/session.js", pricing.scripts)
        source = (ROOT / "static/app-shell.js").read_text()
        self.assertNotRegex(source, r"\bfetch\s*\(")
        self.assertNotIn("innerHTML", source)
        self.assertIn('data.permissions?.[clinic]?.includes(`${key}.view`)', source)

    def test_navigation_and_access_controls_keep_their_existing_ids(self):
        for name in ("index.html", "tasks.html", "birthdays.html"):
            page = Page((ROOT / "static" / name).read_text())
            with self.subTest(page=name):
                self.assertTrue({"syncBtn", "settingsLink", "viewTabs", "changeClinicBtn"}.issubset(page.ids))
        shell = (ROOT / "static/app-shell.js").read_text()
        self.assertIn("actions.append(refresh)", shell)
        self.assertIn("chrome.append(nav)", shell)
        self.assertNotIn("cloneNode", shell)

    def test_responsive_print_and_accessibility_fallbacks(self):
        css = (ROOT / "static/design-system.css").read_text()
        for requirement in ("@media print", "prefers-reduced-motion", "prefers-reduced-transparency", ":focus-visible", "overflow-x: auto", "flex-direction: row !important"):
            self.assertIn(requirement, css)
        self.assertNotRegex(css, r"font-size:\s*[^;]*(?:\dvw|clamp\()")
        shell = (ROOT / "static/app-shell.js").read_text()
        self.assertIn('event.key === "Escape"', shell)
        self.assertIn('"aria-label", "Menu da conta"', shell)


if __name__ == "__main__":
    unittest.main()

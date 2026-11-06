# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
"""The interface's languages (F-01): web/i18n/<lang>.json, read by web/i18n.js.

What a browser would only show as a stray key or a stale English sentence: a language
missing a key or a {placeholder}, en.json drifting from the English in index.html, a key
the code asks for that no file has, a language the service worker does not precache
(so it fails offline), or a country with no code for the browser to name it by.
"""

import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
I18N = WEB / "i18n"
EN = json.loads((I18N / "en.json").read_text())
LANG_FILES = sorted(I18N.glob("*.json"))
LOADER = (WEB / "i18n.js").read_text()
APP = (WEB / "app.js").read_text()
STATIC = (WEB / "static.js").read_text()
FILES = (WEB / "files.js").read_text()
INDEX = (WEB / "index.html").read_text()

# Keys app.js builds from a value (`coord.${...}`), and names a language may add.
DYNAMIC = ("coord.", "continent.", "credits.", "country.")


def placeholders(value) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", json.dumps(value)))


def loader_languages() -> list[str]:
    block = re.search(r"const LANGUAGES = \{(.*?)\};", LOADER, re.DOTALL).group(1)
    return re.findall(r"^\s*(\w+):", block, re.MULTILINE)


def test_every_language_the_loader_offers_has_a_file_and_is_precached():
    langs = loader_languages()
    assert "en" in langs
    assert sorted(langs) == sorted(p.stem for p in LANG_FILES)
    listed = re.search(
        r"\.\.\.\[([^\]]*)\]\.map\(\(l\) => `\./i18n/\$\{l\}\.json", (WEB / "sw.js").read_text()
    )
    assert listed, "sw.js must precache i18n/<lang>.json for every language"
    assert sorted(re.findall(r'"(\w+)"', listed.group(1))) == sorted(langs)


@pytest.mark.parametrize("path", LANG_FILES, ids=lambda p: p.stem)
def test_a_language_has_every_key_with_the_same_placeholders(path):
    table = json.loads(path.read_text())
    assert table.get("_language") == dict(
        re.findall(
            r'^\s*(\w+): "([^"]+)"',
            re.search(r"const LANGUAGES = \{(.*?)\};", LOADER, re.DOTALL).group(1),
            re.MULTILINE,
        )
    ).get(path.stem), "_language must match the name i18n.js lists"
    missing = [k for k in EN if k not in table]
    assert not missing, f"{path.name} lacks {missing}"
    extra = [k for k in table if k not in EN and not k.startswith("country.")]
    assert not extra, f"{path.name} has keys en.json does not: {extra}"
    for key, value in table.items():
        if key.startswith(("_", "country.")):
            continue
        assert placeholders(value) == placeholders(EN[key]), f"{path.name} {key}"
        # A plural is {"one": ..., "other": ...}; "other" is the one every language has.
        assert isinstance(value, dict) == isinstance(EN[key], dict), f"{path.name} {key}"
        if isinstance(value, dict):
            assert "other" in value and set(value) <= {"zero", "one", "two", "few", "many", "other"}
            assert all(placeholders(v) <= placeholders(EN[key]) for v in value.values())


@pytest.mark.parametrize("path", [I18N / "en.json", *LANG_FILES], ids=lambda p: p.stem)
def test_a_key_appears_once(path):
    """JSON keeps the last of two: a change to the first would never reach the screen."""

    def no_repeats(pairs):
        keys = [k for k, _ in pairs]
        assert len(keys) == len(set(keys)), (
            f"{path.name} repeats {sorted(k for k in keys if keys.count(k) > 1)}"
        )
        return dict(pairs)

    json.loads(path.read_text(), object_pairs_hook=no_repeats)


@pytest.mark.parametrize("path", LANG_FILES, ids=lambda p: p.stem)
def test_links_in_a_translation_go_where_the_english_ones_do(path):
    table = json.loads(path.read_text())
    hrefs = lambda v: sorted(re.findall(r'href="([^"]+)"', json.dumps(v).replace('\\"', '"')))
    for key in EN:
        if not key.startswith("_"):
            assert hrefs(table[key]) == hrefs(EN[key]), f"{path.name} {key}"


class _Marked(HTMLParser):
    """Each element index.html marks for translation: its attributes and inner HTML."""

    VOID = frozenset(
        {"meta", "link", "input", "br", "img", "hr", "source", "path", "polygon", "circle"}
    )

    def __init__(self, text: str):
        super().__init__(convert_charrefs=False)
        self.text = text
        self.lines = [0]
        for line in text.splitlines(keepends=True):
            self.lines.append(self.lines[-1] + len(line))
        self.stack: list[tuple[str, dict, int]] = []
        self.found: list[tuple[dict, str]] = []
        self.feed(text)

    def _offset(self) -> int:
        line, col = self.getpos()
        return self.lines[line - 1] + col

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            return
        start = self._offset() + len(self.get_starttag_text())
        self.stack.append((tag, dict(attrs), start))

    def handle_endtag(self, tag):
        if tag in self.VOID or not self.stack:
            return
        open_tag, attrs, start = self.stack.pop()
        assert open_tag == tag, f"index.html: <{open_tag}> closed by </{tag}>"
        if any(k.startswith("data-i18n") for k in attrs):
            self.found.append((attrs, self.text[start : self._offset()]))


MARKED = _Marked(INDEX).found
norm = lambda s: re.sub(r"\s+", " ", html.unescape(s)).strip()


def test_index_html_says_what_en_json_says():
    assert MARKED, "index.html marks nothing for translation"
    for attrs, inner in MARKED:
        if "data-i18n" in attrs:
            assert "<" not in inner, (
                f"data-i18n={attrs['data-i18n']} holds markup; use data-i18n-html"
            )
            assert norm(inner) == norm(EN[attrs["data-i18n"]]), attrs["data-i18n"]
        if "data-i18n-html" in attrs:
            assert norm(inner) == norm(EN[attrs["data-i18n-html"]]), attrs["data-i18n-html"]
        for pair in attrs.get("data-i18n-attr", "").split(";"):
            if pair.strip():
                attr, key = (x.strip() for x in pair.split("="))
                assert norm(attrs[attr]) == norm(EN[key]), f"{attr}={key}"


def used_keys() -> set[str]:
    """Every key literal in the code: t("..."), said(..., "...") and data-i18n*."""
    namespaces = {k.split(".")[0] for k in EN if "." in k}
    literals = set(re.findall(r'"([a-z]+\.[A-Za-z]+)"', APP + STATIC + FILES))
    keys = {k for k in literals if k.split(".")[0] in namespaces}
    for attrs, _ in MARKED:
        keys |= {attrs[a] for a in ("data-i18n", "data-i18n-html") if a in attrs}
        keys |= {
            p.split("=")[1].strip() for p in attrs.get("data-i18n-attr", "").split(";") if p.strip()
        }
    return keys


def test_every_key_the_code_asks_for_exists():
    unknown = sorted(k for k in used_keys() if k not in EN)
    assert not unknown, f"no such key in en.json: {unknown}"


def test_every_english_key_is_used():
    unused = sorted(k for k in EN if not k.startswith(("_", *DYNAMIC)) and k not in used_keys())
    assert not unused, f"en.json keys nothing uses: {unused}"


def test_every_place_has_a_code_for_the_browser_to_name_it_by():
    codes = dict(re.findall(r"\b([A-Z]{3}):([A-Z]{2})\b", LOADER))
    places = json.loads((ROOT / "reference" / "continents.json").read_text())["countries"]
    # Neither has an ISO 3166 code, nor a pack.
    no_code = {"ATC", "KAS"}
    assert sorted(set(places) - set(codes) - no_code) == []
    assert len(set(codes.values())) == len(codes) - 1, "one code per place (Kosovo has two ISO3s)"

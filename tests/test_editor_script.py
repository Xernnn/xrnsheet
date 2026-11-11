"""The page's own script, run in Node against a stand-in server.

Only its functions are loaded, not the page, so each test sets up what the code it calls reads.
These are skipped where Node isn't installed. The browser tests in e2e_editor.py cover the rest.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline.engrave import glyph_script

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node isn't installed")

SETUP = """
let queue = Promise.resolve();
let scoreId = "s", score = null, selected = "note1", editing = null, hint = "";
let range = { first: "note1", last: "note2", ids: ["note1", "note2"] };
let clipboard = [{ type: "whole" }];
const AFTER = "after:";
const GROUP_ACTIONS = new Set(["delete"]);
const editor = { on: false, type: "quarter", dots: 0, voice: 1, cursor: null, last: null, trail: [], future: [] };
const sessionStorage = { setItem() {} };
const asked = [], shown = [];
"""

# These come after the page's own functions, so they take the place of those that draw.
STAND_INS = """
function message(text) { if (text) shown.push(text); }
function updateTools() {}
function receive() {}
async function fetch(url) {
  asked.push(url.split("/").pop());
  const failed = COPY_FAILS && url.endsWith("/copy");
  return { ok: !failed, json: async () => (failed ? { detail: "The copy didn't work." } : { events: [{ type: "quarter" }] }) };
}
"""


def page_scripts() -> list[str]:
    """Return the page's own scripts, in the order the page loads them."""
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    return [name for name in re.findall(r'<script src="([\w.]+)"></script>', page) if (STATIC / name).exists()]


def page_functions() -> str:
    """Return every top-level function of the page, in the order the page loads them."""
    source = "\n".join((STATIC / name).read_text(encoding="utf-8") for name in page_scripts())
    return "\n\n".join(re.findall(r"^(?:async )?function \w+\(.*?^\}$", source, re.S | re.M))


def run(action: str, copy_fails: bool) -> dict:
    program = (
        SETUP + page_functions() + STAND_INS + f"const COPY_FAILS = {json.dumps(copy_fails)};\n"
        + f"{action}();\nqueue.then(() => console.log(JSON.stringify({{ asked, shown, clipboard }})));\n"
    )
    done = subprocess.run([NODE, "-"], input=program, capture_output=True, text=True, timeout=30, check=True)
    return json.loads(done.stdout)


def test_a_cut_whose_copy_fails_deletes_nothing():
    """Otherwise the notes are gone, and pasting brings back whatever was copied before."""
    done = run("cutSelection", copy_fails=True)
    assert done == {"asked": ["copy"], "shown": ["The copy didn't work."], "clipboard": [{"type": "whole"}]}


def test_a_cut_copies_and_then_deletes():
    done = run("cutSelection", copy_fails=False)
    assert done == {"asked": ["copy", "group"], "shown": [], "clipboard": [{"type": "quarter"}]}


def test_a_copy_still_copies_on_its_own():
    assert run("copySelection", copy_fails=False) == {"asked": ["copy"], "shown": [], "clipboard": [{"type": "quarter"}]}


def run_script(program: str) -> object:
    done = subprocess.run([NODE, "-"], input=program, capture_output=True, text=True, timeout=30, check=True)
    return json.loads(done.stdout)


def test_the_page_loads_the_scripts_these_tests_read():
    assert page_scripts() == ["page.js", "palettes.js", "shell.js", "editor.js"]


def palettes() -> list[dict]:
    """Build the palettes as the page does, with the engraver's own symbols to draw from."""
    functions = page_functions()
    drawing = "\n".join(re.findall(r"^function (?:setGlyphs|glyphIcon)\(.*?^\}$", functions, re.S | re.M))
    program = (
        glyph_script() + "const UNITS_PER_SPACE = 250;\n" + drawing + (STATIC / "palettes.js").read_text(encoding="utf-8")
        + "console.log(JSON.stringify(buildPalettes().map(palette => ({ ...palette, cells: palette.cells.map(cell => ({ ...cell, runs: typeof cell.run === 'function' })) }))));\n"
    )
    return run_script(program)


def test_the_palettes_musescore_shows_at_first_come_in_its_order():
    usual = [palette["name"] for palette in palettes() if palette.get("usual")]
    assert usual == ["Clefs", "Key signatures", "Time signatures", "Tempo", "Accidentals", "Dynamics", "Articulations", "Text", "Repeats & jumps", "Barlines", "Layout"]


def test_every_palette_cell_is_named_drawn_and_does_something():
    cells = [cell for palette in palettes() for cell in palette["cells"]]
    assert len(cells) > 90
    assert len({cell["key"] for cell in cells}) == len(cells)
    for cell in cells:
        assert cell["label"] and cell["runs"] and ("<svg" in cell["html"] or "<span" in cell["html"]), cell["key"]


def test_key_signature_cells_run_from_one_sharp_round_to_no_sharps_or_flats():
    keys = next(palette for palette in palettes() if palette["id"] == "keys")
    assert [cell["key"] for cell in keys["cells"]] == [f"key:{fifths}" for fifths in [1, 2, 3, 4, 5, 6, 7, -7, -6, -5, -4, -3, -2, -1, 0]]
    assert keys["cells"][0]["label"] == "G major, E minor" and keys["cells"][-1]["label"] == "C major, A minor"
    # A cell shows as many signs as its key has.
    assert [cell["html"].count("<path") for cell in keys["cells"][:3]] == [1, 2, 3]


def test_shortcuts_are_listed_as_keys_with_their_joining_words_left_plain():
    functions = page_functions()
    wanted = re.search(r"^function keysHtml\(.*?^\}$", functions, re.S | re.M).group(0)
    shown = run_script(wanted + 'console.log(JSON.stringify(["Ctrl+Shift+Z", "+  -  =", "A to G", "Ins, Ctrl+B", "<  >"].map(keysHtml)));')
    assert shown == [
        "<kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd>",
        "<kbd>+</kbd> <kbd>-</kbd> <kbd>=</kbd>",
        "<kbd>A</kbd> to <kbd>G</kbd>",
        "<kbd>Ins</kbd>, <kbd>Ctrl</kbd>+<kbd>B</kbd>",
        "<kbd>&lt;</kbd> <kbd>&gt;</kbd>",
    ]

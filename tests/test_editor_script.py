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
    assert page_scripts() == ["page.js", "palettes.js", "shell.js", "playback.js", "dock.js", "editor.js"]


def playback_program(body: str) -> str:
    """The playback script's functions and the constants they read, then a test that prints JSON."""
    source = (STATIC / "playback.js").read_text(encoding="utf-8")
    constants = "\n".join(re.findall(r"^const (?:EARLY|TICK|SLOWEST_SPEED|FASTEST_SPEED) = [^;]*;", source, re.M))
    return constants + "\n" + page_functions() + "\n" + body


def test_the_metronome_counts_a_pickup_as_the_end_of_a_bar_and_six_eight_in_two():
    bars = [
        {"start": 0, "length": 1, "full": 4, "index": 0, "beats": 4, "beatType": 4},
        {"start": 1, "length": 4, "full": 4, "index": 1, "beats": 4, "beatType": 4},
        {"start": 5, "length": 3, "full": 3, "index": 2, "beats": 6, "beatType": 8},
    ]
    found = run_script(playback_program(f"console.log(JSON.stringify(beatsBetween({{ bars: {json.dumps(bars)} }}, 0, 8)));"))
    places = [[beat["place"], beat["strong"]] for beat in found]
    assert places == [[0, False], [1, True], [2, False], [3, False], [4, False], [5, True], [6.5, False]]


def test_the_counters_read_and_write_a_time_and_a_measure_and_beat():
    bars = [{"start": 0, "length": 2, "full": 4, "index": 0, "beats": 4, "beatType": 4}, {"start": 2, "length": 4, "full": 4, "index": 1, "beats": 4, "beatType": 4}]
    body = f"""
        const data = {{ bars: {json.dumps(bars)}, tempo: 120, tempos: [{{ start: 0, bpm: 120 }}, {{ start: 2, bpm: 60 }}] }};
        const clock = clockFor(data, 1);
        console.log(JSON.stringify([
            timeText(0), timeText(3725.43), secondsIn("01:02:05.5"), secondsIn("1:30"), secondsIn("x"),
            beatAt(data, 0), beatAt(data, 1), beatAt(data, 3.5), placeAtBeat(data, 2, 3), placeAtBeat(data, 1, 1), placeAtBeat(data, 9, 1),
            clock(4), clock.place(clock(4)), clock.place(2.5),
        ]));
    """
    found = run_script(playback_program(body))
    assert found[:5] == ["00:00:00.0", "01:02:05.4", 3725.5, 90, None]
    assert found[5:8] == [{"measure": 1, "beat": 3}, {"measure": 1, "beat": 4}, {"measure": 2, "beat": 2}], "a pickup of two beats starts on the third"
    assert found[8:11] == [4, 0, None], "a pickup's first beat is where it starts, not before"
    assert found[11:] == [3, 4, 3.5], "two beats at 120 take a second, and a beat and a half at 60 the rest"


def test_a_loop_goes_round_the_selection_wherever_it_is_first_played():
    bars = [{"start": 0, "length": 4, "index": 0}, {"start": 4, "length": 4, "index": 1}, {"start": 8, "length": 4, "index": 0}, {"start": 12, "length": 4, "index": 1}]
    events = {"a": [0, 1, 1], "b": [1, 2, 2], "c": [0, 3, 1]}
    body = f"""
        const playState = {{ loop: null }};
        const data = {{ bars: {json.dumps(bars)}, events: {json.dumps(events)}, total: 16 }};
        const windows = [];
        for (const loop of [null, {{ first: "a", last: "b" }}, {{ first: "b", last: "a" }}, {{ first: "c", last: "c" }}, {{ first: "a", last: "gone" }}]) {{
            playState.loop = loop;
            windows.push(loopWindow(data));
        }}
        console.log(JSON.stringify([...windows, placeOf(data, "a", 5)]));
    """
    found = run_script(playback_program(body))
    assert found[:5] == [{"start": 0, "end": 16}, {"start": 1, "end": 8}, {"start": 1, "end": 8}, {"start": 3, "end": 4}, {"start": 0, "end": 16}]
    assert found[5] == {"start": 9, "end": 10}, "the second time it's played"


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


def test_time_signature_cells_follow_musescores_order_with_the_two_signs():
    times = next(palette for palette in palettes() if palette["id"] == "times")
    assert [cell["key"].removeprefix("time:") for cell in times["cells"]] == ["2/4", "3/4", "4/4", "5/4", "6/4", "3/8", "4/8", "5/8", "6/8", "7/8", "9/8", "12/8", "common", "cut", "2/2", "3/2"]
    assert [cell["label"] for cell in times["cells"][12:14]] == ["Common time", "Cut time"]


def test_barline_cells_follow_musescores_order():
    barlines = next(palette for palette in palettes() if palette["id"] == "barlines")
    assert [cell["key"].removeprefix("barline:") for cell in barlines["cells"]] == ["single", "double", "forward", "backward", "dashed", "final", "heavy", "heavyDouble", "dotted"]

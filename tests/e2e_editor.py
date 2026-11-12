"""Drive the editor in a real browser, the way a person would.

Start the server, then run this with a Python that has Playwright:

    .venv/bin/python -m app.server --notes own --port 8011
    python tests/e2e_editor.py --port 8011 --shots /tmp/shots

Set XRNSHEET_BROWSER to use a Chromium that's already installed, or pass --browser firefox.
Every score it creates is listed at the end, and removed if you pass --clean, even when a step
crashes. It isn't collected by pytest, because it needs a browser and a running server.
"""

import argparse
import array
import json
import os
import shutil
import urllib.request
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []


def check(name: str, passed: bool, detail: object = "") -> None:
    print(f"{'ok  ' if passed else 'FAIL'} {name} {detail if detail != '' else ''}", flush=True)
    if not passed:
        FAILURES.append(name)


def fetch_text(base: str, path: str) -> str:
    with urllib.request.urlopen(f"{base}{path}") as response:
        return response.read().decode("utf-8")


def read_notes(base: str, score_id: str) -> list[tuple]:
    root = ET.fromstring(fetch_text(base, f"/api/scores/{score_id}/score.musicxml"))
    found = []
    for bar, measure in enumerate(root.find("part").findall("measure")):
        for note in measure.findall("note"):
            found.append((
                bar,
                note.findtext("pitch/step", "rest"),
                note.findtext("pitch/octave", ""),
                note.findtext("type", "bar"),
                note.findtext("accidental", ""),
                note.find("chord") is not None,
            ))
    return found


def pitched(notes: list[tuple]) -> list[str]:
    return [f"{step}{octave}" for _, step, octave, *_ in notes if step != "rest"]


def settle(page) -> None:
    """Wait until every queued edit has come back from the server."""
    page.evaluate("() => queue")


def click_on(page, locator) -> None:
    """Click where a mouse would. Staff lines are drawn over noteheads, which the app allows for."""
    locator.evaluate("element => element.scrollIntoView({ block: 'center' })")
    head = locator.locator(".notehead")
    box = (head.first if head.count() else locator).bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


def new_score(page, instrument: str = "treble", bars: int | None = None, time: str | None = None) -> None:
    """Start a score through the New score dialog: its instrument first, then the rest."""
    page.click("#blankOpen")
    page.fill("#newChooser input", instrument.split("-")[0])
    page.click(f'#newChooser [data-instrument="{instrument}"] button')
    page.click("#blankNext")
    if time:
        page.select_option("#blankTime", time)
    if bars:
        page.fill("#blankBars", str(bars))
    page.click("#blankDone")
    page.wait_for_selector("#pages svg")


def inspector_cell(page, cell: str):
    """The inspector's button for a cell, in the part of it that's showing."""
    return page.locator(f'#inspector section:not([hidden]) [data-cell="{cell}"]')


def choose(page, cell: str) -> None:
    """Click a cell in the inspector, opening its group first if need be, as a person would."""
    if not page.is_visible("#inspector"):
        page.click("#inspectorToggle")
    found = inspector_cell(page, cell)
    if not found.count() or not found.first.is_visible():
        group = page.evaluate("cell => catalogue().find(palette => palette.cells.some(item => item.key === cell)).id", cell)
        page.locator(f'#inspector section:not([hidden]) .group[data-group="{group}"] .groupHead').first.click()
    found.first.click()


def menu(page, *path: str) -> None:
    """Choose from the menu, such as menu(page, "Add", "Text", "Chord symbol")."""
    page.click("#mainMenu")
    for label in path:
        page.locator(".menu").last.locator(f'button:has(span:text-is("{label}"))').click()


def menu_offers(page, *path: str) -> bool:
    """Say whether a menu item can be chosen just now, then close the menu again."""
    page.click("#mainMenu")
    for label in path[:-1]:
        page.locator(".menu").last.locator(f'button:has(span:text-is("{label}"))').click()
    offered = page.locator(".menu").last.locator(f'button:has(span:text-is("{path[-1]}"))').is_enabled()
    page.keyboard.press("Escape")
    return offered


def menu_ticked(page, *path: str) -> str | None:
    """Say whether a menu item is ticked, then close the menu again."""
    page.click("#mainMenu")
    for label in path[:-1]:
        page.locator(".menu").last.locator(f'button:has(span:text-is("{label}"))').click()
    ticked = page.locator(".menu").last.locator(f'button[role="menuitemcheckbox"]:has(span:text-is("{path[-1]}"))').get_attribute("aria-checked")
    page.keyboard.press("Escape")
    return ticked


def command(page, typed: str) -> None:
    """Find a command in the command palette by typing, and run the first one found."""
    page.keyboard.press("Control+Shift+P")
    page.fill("#commandInput", typed)
    page.keyboard.press("Enter")


def score_pane(page) -> None:
    """Show the inspector's score settings: its title, instruments and pages."""
    if not page.is_visible("#inspector"):
        page.click("#inspectorToggle")
    page.click('#inspectorTabs [data-pane="score"]')


def show_instrument(page) -> None:
    """Put the instrument panel out below the score, if it isn't already."""
    if not page.is_visible('#dockTabs [data-dock="instrument"][aria-selected="true"]') or page.is_hidden("#dock"):
        page.keyboard.press("p")


def system_breaks(page, every: int) -> None:
    """Keep this many measures on each system, or fit them to the page if it's 0."""
    menu(page, "Format", "Measures per line…")
    if every:
        page.fill("#breaksCount", str(every))
    else:
        page.check('input[name="breaksMode"][value="fit"]')
    page.click("#breaksDialog button[type=submit]")


def transpose(page, key: int | None = None, interval: str = "", direction: str = "up") -> None:
    menu(page, "Tools", "Transpose…")
    if key is not None:
        page.select_option("#transposeKey", str(key))
    else:
        page.select_option("#transposeInterval", interval)
        page.select_option("#transposeDirection", direction)
    page.click("#transposeDialog button[type=submit]")


def test_writing(page, base: str, shots: Path | None, created: list[str]) -> None:
    page.goto(base)
    new_score(page, bars=8, time="4/4")
    score_id = page.evaluate("() => scoreId")
    created.append(score_id)
    check("blank score opens ready to type", page.get_attribute("#inputToggle", "aria-pressed") == "true")
    check("blank score hides the original panel", page.is_hidden("#original"))
    check("blank score hides mark as checked", page.is_hidden("#checked"))

    page.keyboard.press("5")
    page.keyboard.type("cdefgabc", delay=0)  # As fast as possible, to expose any race.
    settle(page)
    notes = read_notes(base, score_id)
    check("fast typing keeps every note, in order", pitched(notes) == ["C5", "D5", "E5", "F5", "G5", "A5", "B5", "C6"], pitched(notes))
    check("notes crossed into bar 2", [note[0] for note in notes if note[1] != "rest"] == [0, 0, 0, 0, 1, 1, 1, 1])

    page.keyboard.press("6")
    page.keyboard.press("e")
    settle(page)
    notes = read_notes(base, score_id)
    check("a chosen length is used", any(note[3] == "half" and note[1] == "E" for note in notes))
    page.keyboard.press("#")
    settle(page)
    check("sharp applies to the note just written", any(note[4] == "sharp" for note in read_notes(base, score_id)))
    page.keyboard.press("Shift+G")
    settle(page)
    check("shift stacks a chord", any(note[5] for note in read_notes(base, score_id)))

    page.keyboard.press("Control+z")
    page.keyboard.press("Control+z")
    settle(page)
    before = read_notes(base, score_id)
    cursor = lambda: page.evaluate("() => editor.cursor")
    start = cursor()
    page.keyboard.press("d")
    settle(page)
    after = cursor()
    page.keyboard.press("Control+z")
    settle(page)
    check("undo restores the score", read_notes(base, score_id) == before)
    check("undo puts the cursor back", cursor() == start)
    page.keyboard.press("Control+y")
    settle(page)
    check("redo brings it back", read_notes(base, score_id) != before)
    check("redo moves the cursor on", cursor() == after)
    page.keyboard.press("Control+z")
    settle(page)
    check("a second undo still lands in the right place", cursor() == start)

    box = page.locator("#caret").bounding_box()
    check("the entry cursor is visible", box is not None and box["height"] > 10, box)
    if shots:
        page.screenshot(path=str(shots / "entering.png"), full_page=True)

    page.keyboard.press("Escape")
    check("escape leaves entry mode", page.get_attribute("#inputToggle", "aria-pressed") == "false")

    first = page.locator("#pages g.note").first
    click_on(page, first)
    check("a note can be selected", page.locator("#pages g.selected").count() >= 1)
    page.keyboard.press("6")
    settle(page)
    notes = read_notes(base, score_id)
    check("a selected note changes length", notes[0][3] == "half", notes[:3])
    check("the note it covered has gone", notes[1][1] == "E", notes[:3])
    page.keyboard.press("Control+ArrowUp")
    settle(page)
    check("control up moves an octave", read_notes(base, score_id)[0][:3] == (0, "C", "6"))

    click_on(page, page.locator("#pages g.mRest").first)
    check("a whole-bar rest can be selected", page.locator("#pages g.selected").count() == 1)
    page.keyboard.press("Escape")

    bars = lambda: len({note[0] for note in read_notes(base, score_id)})
    menu(page, "Add", "Measures", "Insert one measure at end of score")
    settle(page)
    check("add bar adds one at the end", bars() == 9)
    click_on(page, page.locator("#pages g.note").first)
    menu(page, "Edit", "Delete selected measures")
    settle(page)
    check("delete bar removes it", bars() == 8)
    check("the deleted bar's signature moved on", ET.fromstring(fetch_text(base, f"/api/scores/{score_id}/score.musicxml")).find("part").find("measure").findtext("attributes/divisions") is not None)

    click_on(page, page.locator("#pages g.note").first)
    choose(page, "key:-2")
    settle(page)
    xml = fetch_text(base, f"/api/scores/{score_id}/score.musicxml")
    check("key change is saved", "<fifths>-2</fifths>" in xml)

    page.keyboard.press("Escape")
    choose(page, "time:3/4")
    settle(page)
    chips = page.locator("#barChecks .chip")
    check("bars that no longer fit are listed", chips.count() >= 1, chips.count() and chips.first.inner_text())
    chips.first.click()
    check("a bar check selects that bar", page.locator("#pages g.selected").count() >= 1)
    if shots:
        page.screenshot(path=str(shots / "bar_check.png"), full_page=True)
    page.keyboard.press("Control+z")
    settle(page)
    check("undo clears the bar check", page.locator("#barChecks .chip").count() == 0)
    page.keyboard.press("Escape")

    page.keyboard.press(" ")
    page.wait_for_function("() => document.querySelector('#play').getAttribute('aria-label') === 'Stop'")
    check("space starts playback", int(page.get_attribute("#play", "data-notes")) >= 5)
    page.wait_for_selector("#pages g.playing", timeout=4000)
    check("the playing note lights up", True)
    page.keyboard.press(" ")
    check("space stops playback", page.get_attribute("#play", "aria-label") == "Play" and page.locator("#pages g.playing").count() == 0)

    pdf = urllib.request.urlopen(f"{base}/api/scores/{score_id}/score.pdf").read()
    check("the PDF downloads", pdf[:4] == b"%PDF", len(pdf))

    kept = read_notes(base, score_id)
    page.reload()
    page.wait_for_selector("#pages svg")
    check("a reload keeps the score", read_notes(base, score_id) == kept)
    page.click(f'#historyList a[data-score="{score_id}"]')
    check("the library beside the score lists it as written here, and as the one open", "written here" in page.inner_text(f'#historyList a[data-score="{score_id}"]') and page.get_attribute(f'#historyList a[data-score="{score_id}"]', "aria-current") == "true")


def open_blank(page, base: str, created: list[str], bars: int = 4, staff: str = "treble") -> str:
    page.goto(base)
    new_score(page, staff, bars)
    created.append(page.evaluate("() => scoreId"))
    return created[-1]


def saved_score(base: str, score_id: str) -> ET.Element:
    return ET.fromstring(fetch_text(base, f"/api/scores/{score_id}/score.musicxml"))


def with_slow_server(page, slow: bool) -> None:
    """Hold every request back, so keys arrive faster than the answers."""
    page.evaluate("""slow => {
        window.realFetch ??= window.fetch;
        window.fetch = slow ? async (...request) => { await new Promise(done => setTimeout(done, 150)); return window.realFetch(...request); } : window.realFetch;
    }""", slow)


def click_staff(page, element_id: str, step: int, shift: bool = False) -> None:
    """Click over a note or rest at a chosen place: 0 is the top line, and each step is a line or space."""
    spot = page.evaluate("""([id, step]) => {
        const element = document.getElementById(id);
        element.scrollIntoView({ block: "center" });
        const lines = staffLines(element.closest("g.staff"));
        const box = element.getBoundingClientRect();
        return { x: box.left + box.width / 2, y: lines.top + step * lines.step };
    }""", [element_id, step])
    page.mouse.move(spot["x"], spot["y"])
    if shift:
        page.keyboard.down("Shift")
    page.mouse.click(spot["x"], spot["y"])
    if shift:
        page.keyboard.up("Shift")
    settle(page)


def stepped_over_chord(page) -> bool:
    start = page.evaluate("""() => {
        const chord = document.querySelector("#pages g.chord");
        editor.cursor = chord.querySelector("g.note").id;
        return editor.cursor;
    }""")
    page.keyboard.press("ArrowRight")
    settle(page)
    after = page.evaluate("() => document.getElementById(editor.cursor).closest('g.chord') === null")
    page.keyboard.press("ArrowLeft")
    settle(page)
    return after and page.evaluate("() => editor.cursor") == start


def test_clicking_notes(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = open_blank(page, base, created, staff="piano")
    cursor = page.evaluate("() => editor.cursor")
    click_staff(page, cursor, 4)
    check("clicking the middle line writes B", pitched(read_notes(base, score_id)) == ["B4"], pitched(read_notes(base, score_id)))
    check("the ghost note shows while hovering", page.is_visible("#ghost"))
    written = page.evaluate("() => editor.last")
    click_staff(page, written, 8, shift=True)
    notes = read_notes(base, score_id)
    check("shift-click stacks a chord note", sorted(pitched(notes)) == ["B4", "E4"] and any(note[5] for note in notes), notes[:3])
    lower = page.evaluate("""() => {
        const staves = [...document.querySelectorAll("#pages g.staff")];
        return staves[1].querySelector("g.rest, g.mRest").id;
    }""")
    click_staff(page, lower, 0)
    bass = saved_score(base, score_id).find("part").find("measure")
    placed = [(note.findtext("staff"), note.findtext("pitch/step"), note.findtext("pitch/octave")) for note in bass.findall("note") if note.find("pitch") is not None]
    check("a click on the lower staff uses the bass clef", ("2", "A", "3") in placed, placed)
    check("the arrows step over a chord as one place", stepped_over_chord(page), page.evaluate("() => editor.cursor"))
    page.keyboard.press("Escape")
    settle(page)
    check("the ghost goes when entering stops", page.is_hidden("#ghost"))


def test_end_of_score(page, base: str, created: list[str]) -> None:
    score_id = open_blank(page, base, created, bars=1)
    page.keyboard.press("5")
    page.keyboard.type("cdef", delay=0)
    settle(page)
    bars = lambda: len(saved_score(base, score_id).find("part").findall("measure"))
    check("filling the last bar adds no empty bar", bars() == 1, bars())
    check("the cursor waits past the last note", page.evaluate("() => editor.cursor.startsWith('after:')"))
    check("the caret still shows at the end", page.is_visible("#caret"))
    box, note = page.evaluate("""() => {
        const last = document.getElementById(editor.cursor.slice(6)).getBoundingClientRect();
        return [$("caret").getBoundingClientRect().left, last.right];
    }""")
    check("the caret sits right of the last note", box > note, (box, note))
    page.keyboard.press("g")
    settle(page)
    check("the next note adds the bar", bars() == 2, bars())
    page.keyboard.press("Control+z")
    settle(page)
    check("undo at the end takes the bar away again", bars() == 1 and page.evaluate("() => editor.cursor.startsWith('after:')"), bars())
    page.keyboard.press("ArrowLeft")
    settle(page)
    last_note = page.evaluate("""() => [...document.querySelectorAll("#pages g.note")].at(-1).id""")
    check("left from the end lands on the last note", page.evaluate("() => editor.cursor") == last_note)
    page.keyboard.press("ArrowRight")
    settle(page)
    check("right from the last note goes back past it", page.evaluate("() => editor.cursor.startsWith('after:')"))
    page.keyboard.press("6")
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("g")
    settle(page)
    root = saved_score(base, score_id)
    ties = len(root.findall(".//tie[@type='start']"))
    check("a note over the barline is split and tied", ties == 1 and bars() == 2, (ties, bars()))


def test_quick_keys(page, base: str, created: list[str]) -> None:
    """Keys that change what the next note will be must only reach the notes typed after them."""
    score_id = open_blank(page, base, created)
    with_slow_server(page, True)
    page.keyboard.press("5")
    page.keyboard.type("cd", delay=0)
    page.keyboard.press("6")
    page.keyboard.type("e", delay=0)
    page.keyboard.press("5")
    page.keyboard.press(".")
    page.keyboard.type("f", delay=0)
    page.keyboard.press(".")
    page.keyboard.type("g", delay=0)
    settle(page)
    with_slow_server(page, False)
    root = saved_score(base, score_id)
    written = [(note.findtext("pitch/step"), note.findtext("type"), len(note.findall("dot"))) for note in root.iter("note") if note.find("pitch") is not None]
    check("each length reaches only the notes after it", written == [("C", "quarter", 0), ("D", "quarter", 0), ("E", "half", 0), ("F", "quarter", 1), ("G", "quarter", 0)], written)
    with_slow_server(page, True)
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("a")
    settle(page)
    with_slow_server(page, False)
    steps = pitched(read_notes(base, score_id))
    check("arrows and the next note stay in order", steps == ["C5", "D5", "E5", "A5", "G5"], steps)
    with_slow_server(page, True)
    page.keyboard.type("bc", delay=0)
    page.keyboard.press("Escape")
    settle(page)
    with_slow_server(page, False)
    check("notes typed just before stopping are kept", pitched(read_notes(base, score_id)) == ["C5", "D5", "E5", "A5", "B5", "C6"], pitched(read_notes(base, score_id)))


def test_triplets(page, base: str, created: list[str]) -> None:
    score_id = open_blank(page, base, created)
    page.keyboard.press("5")
    page.keyboard.press("Control+3")
    settle(page)
    check("a triplet asks for eighths", page.evaluate("() => editor.type") == "eighth", page.evaluate("() => editor.type"))
    page.keyboard.type("cde", delay=0)
    settle(page)
    root = saved_score(base, score_id)
    members = [note for note in root.find("part").find("measure").findall("note") if note.find("time-modification") is not None]
    check("typing fills the triplet", [note.findtext("pitch/step") for note in members] == ["C", "D", "E"], len(members))
    check("the triplet is drawn with its number", page.locator("#pages g.tupletNum").count() == 1)
    check("the triplet is beamed", page.locator("#pages g.beam").count() >= 1)
    check("the bar still adds up", page.is_hidden("#barChecks"))
    page.keyboard.press("5")
    page.keyboard.press("g")
    settle(page)
    notes = read_notes(base, score_id)
    check("the note after a triplet is ordinary", notes[3][1] == "G" and notes[3][3] == "quarter", notes[3])
    page.keyboard.press("Escape")
    first = page.locator("#pages g.note").first
    click_on(page, first)
    page.keyboard.press("Control+3")
    settle(page)
    check("a triplet can be taken away again", not list(saved_score(base, score_id).iter("time-modification")))
    page.keyboard.press("Control+z")
    settle(page)
    check("undo brings the triplet back", len(list(saved_score(base, score_id).iter("time-modification"))) == 3)
    page.click("#tripletButton")
    settle(page)
    check("the triplet button does the same", not list(saved_score(base, score_id).iter("time-modification")))


def test_selection(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = open_blank(page, base, created)
    page.keyboard.press("5")
    page.keyboard.type("cdefg", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    notes = page.locator("#pages g.note")
    click_on(page, notes.nth(0))
    box = notes.nth(2).locator(".notehead").bounding_box()
    page.keyboard.down("Shift")
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.keyboard.up("Shift")
    settle(page)
    highlighted = lambda: page.locator("#pages g.selected").count()
    check("shift-click selects a stretch", highlighted() == 3, highlighted())
    page.keyboard.press("Shift+ArrowRight")
    settle(page)
    check("shift and an arrow grow it", highlighted() == 4, highlighted())
    page.keyboard.press("Shift+ArrowLeft")
    settle(page)
    check("shift and the other arrow shrink it", highlighted() == 3, highlighted())
    if shots:
        page.screenshot(path=str(shots / "selection.png"), full_page=True)
    version = page.evaluate("() => score.canUndo")
    page.keyboard.press("ArrowUp")
    settle(page)
    raised = [(step, sign) for _, step, _, _, sign, _ in read_notes(base, score_id)[:4]]
    check("up moves the whole stretch a semitone", raised == [("C", "sharp"), ("D", "sharp"), ("F", ""), ("F", "")], raised)
    check("the selection is still there after the edit", highlighted() == 3, highlighted())
    page.keyboard.press("Control+z")
    settle(page)
    check("one undo reverses the whole stretch", pitched(read_notes(base, score_id))[:4] == ["C5", "D5", "E5", "F5"] and version)
    page.keyboard.press("Control+c")
    settle(page)
    check("copying enables paste", menu_offers(page, "Edit", "Paste"))
    rest_in_bar_three = page.evaluate("""() => {
        const measures = [...document.querySelectorAll("#pages g.measure")];
        return measures[2].querySelector("g.rest, g.mRest").id;
    }""")
    click_on(page, page.locator(f"[id='{rest_in_bar_three}']"))
    page.keyboard.press("Control+v")
    settle(page)
    placed = [(bar, step) for bar, step, *_ in read_notes(base, score_id) if bar == 2 and step != "rest"]
    check("paste writes into the chosen bar", [step for _, step in placed] == ["C", "D", "E"], placed)
    page.keyboard.press("Control+a")
    settle(page)
    check("select all takes every note", highlighted() >= 8, highlighted())
    click_on(page, page.locator("#pages g.note").nth(0))
    check("clicking again drops the stretch", highlighted() == 1, highlighted())
    click_on(page, page.locator("#pages g.note").nth(1))
    page.keyboard.press("Control+x")
    settle(page)
    check("cut leaves a rest behind", pitched(read_notes(base, score_id))[:2] == ["C5", "E5"], pitched(read_notes(base, score_id))[:3])

    other = open_blank(page, base, created)
    page.keyboard.press("Escape")
    click_on(page, page.locator("#pages g.rest, #pages g.mRest").first)
    check("the clip survives opening another score", menu_offers(page, "Edit", "Paste"))
    page.keyboard.press("Control+v")
    settle(page)
    check("the last thing cut is what gets pasted", pitched(read_notes(base, other)) == ["D5"], pitched(read_notes(base, other)))


def test_lyrics(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = open_blank(page, base, created)
    page.keyboard.press("5")
    page.keyboard.type("cdef", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    click_on(page, page.locator("#pages g.note").first)
    page.keyboard.press("Control+l")
    check("the lyric box opens", page.is_visible("#lyricInput"))
    page.keyboard.type("hal-le-lu now", delay=20)
    page.keyboard.press("Enter")
    settle(page)
    root = saved_score(base, score_id)
    found = [(item.findtext("syllabic"), item.findtext("text")) for item in root.iter("lyric")]
    check("words and syllables land on the right notes", found == [("begin", "hal"), ("middle", "le"), ("end", "lu"), ("single", "now")], found)
    check("the lyric box closes", page.is_hidden("#lyricInput"))
    check("lyrics are drawn", "hal" in page.inner_text("#pages"))
    check("typing a lyric played nothing and wrote no notes", len(pitched(read_notes(base, score_id))) == 4)
    click_on(page, page.locator("#pages g.note .notehead").nth(1))
    page.keyboard.press("Control+l")
    settle(page)
    check("reopening shows the syllable", page.input_value("#lyricInput") == "le", page.input_value("#lyricInput"))
    page.keyboard.press("Escape")
    check("escape leaves the lyric alone", any(item.findtext("text") == "le" for item in saved_score(base, score_id).iter("lyric")))
    if shots:
        page.screenshot(path=str(shots / "lyrics.png"), full_page=True)

    click_on(page, page.locator("#pages g.note").last)
    page.keyboard.press("Control+l")
    settle(page)
    page.keyboard.type("wonderfully-overlong")
    page.keyboard.press("Enter")
    settle(page)
    click_on(page, page.locator("#pages g.note").last)
    page.keyboard.press("n")
    settle(page)
    head = page.locator("#pages g.note").last.locator(".notehead").bounding_box()
    caret = page.locator("#caret").bounding_box()
    middle_gap = abs(caret["x"] + caret["width"] / 2 - (head["x"] + head["width"] / 2))
    check("the caret sits on the note, not on its long lyric", middle_gap < 8, middle_gap)
    page.keyboard.press("Escape")

    # A hyphen on the last note has no next note to go to, and the letters after it must stay in the box.
    notes_before = page.locator("#pages g.note").count()
    for label, delay in (("at typing speed", 150), ("all at once", 0)):
        click_on(page, page.locator("#pages g.note").last)
        page.keyboard.press("Control+l")
        settle(page)
        page.keyboard.type("won-der", delay=delay)
        page.keyboard.press("Enter")
        settle(page)
        last_lyric = list(saved_score(base, score_id).iter("lyric"))[-1].findtext("text")
        check(f"the word stays whole on the last note, {label}", last_lyric == "won-der", last_lyric)
        check(f"its letters write no notes, {label}", page.locator("#pages g.note").count() == notes_before)
        check(f"the box closes after it, {label}", not page.is_visible("#lyricInput"))
    click_on(page, page.locator("#pages g.note").last)
    page.keyboard.press("Control+l")
    settle(page)
    page.keyboard.type("end-", delay=150)
    settle(page)
    page.keyboard.press("Escape")
    finished = list(saved_score(base, score_id).iter("lyric"))[-1]
    check("a hyphen left on the last note joins nothing", (finished.findtext("text"), finished.findtext("syllabic")) in (("end", "single"), ("end", "end")), finished.findtext("syllabic"))
    page.keyboard.press("Escape")
    settle(page)

    score_id = open_blank(page, base, created)
    page.keyboard.press("5")
    page.keyboard.type("cdef", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    click_on(page, page.locator("#pages g.note").first)
    with_slow_server(page, True)
    page.keyboard.press("Control+l")
    settle(page)
    page.keyboard.type("one two three four", delay=0)
    page.keyboard.press("Enter")
    settle(page)
    with_slow_server(page, False)
    found = [item.findtext("text") for item in saved_score(base, score_id).iter("lyric")]
    check("words typed faster than the server stay on their own notes", found == ["one", "two", "three", "four"], found)


def test_titles_and_repeats(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = open_blank(page, base, created, bars=4)
    page.keyboard.press("Escape")
    menu(page, "File", "Title and composer…")
    page.fill("#titleInput", "Test Song")
    page.fill("#composerInput", "A. Writer")
    page.keyboard.press("Enter")
    settle(page)
    check("the dialog closes once its fields are saved", page.is_hidden("#projectDialog"))
    root = saved_score(base, score_id)
    check("the title is saved", root.findtext("work/work-title") == "Test Song", root.findtext("work/work-title"))
    printed = page.evaluate("""() => document.querySelector("#pages .pgHead")?.textContent ?? "" """)
    check("title and composer are printed", "Test Song" in printed and "A. Writer" in printed, printed.split())
    page.reload()
    page.wait_for_selector("#pages svg")
    check("the title names the score's tab after reloading", page.inner_text("#scoreName") == "Test Song")
    menu(page, "File", "Title and composer…")
    check("the title is shown again where it's changed", page.input_value("#titleInput") == "Test Song" and page.input_value("#composerInput") == "A. Writer")
    page.keyboard.press("Escape")
    title = page.evaluate("""() => {
        const box = [...document.querySelectorAll("#pages .pgHead text")].find(text => text.textContent.includes("Test Song")).getBoundingClientRect();
        return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
    }""")
    page.mouse.dblclick(title["x"], title["y"])
    check("double-clicking the title opens it to change", page.is_visible("#projectDialog") and page.input_value("#titleInput") == "Test Song")
    page.keyboard.press("Escape")

    rests = page.locator("#pages g.mRest, #pages g.rest")
    click_on(page, rests.nth(1))
    choose(page, "repeat:backward")
    settle(page)
    click_on(page, page.locator("#pages g.mRest, #pages g.rest").nth(1))
    choose(page, "ending:1")
    settle(page)
    click_on(page, page.locator("#pages g.mRest, #pages g.rest").nth(2))
    choose(page, "ending:2")
    settle(page)
    played = json.loads(fetch_text(base, f"/api/scores/{score_id}/playback"))["total"]
    root = saved_score(base, score_id)
    check("repeats are saved", len(list(root.iter("repeat"))) == 1 and len(list(root.iter("ending"))) >= 2)
    check("the repeat plays bars 1, 2, 1, 3, 4", played == 20, played)
    page.keyboard.press("Control+z")
    page.keyboard.press("Control+z")
    settle(page)
    click_on(page, page.locator("#pages g.mRest, #pages g.rest").nth(1))
    bar_three = page.locator("#pages g.mRest, #pages g.rest").nth(2).bounding_box()
    page.keyboard.down("Shift")
    page.mouse.click(bar_three["x"] + bar_three["width"] / 2, bar_three["y"] + bar_three["height"] / 2)
    page.keyboard.up("Shift")
    settle(page)
    choose(page, "ending:1")
    settle(page)
    measures = saved_score(base, score_id).find("part").findall("measure")
    starts = [len(measure.findall("barline[@location='left']/ending")) for measure in measures]
    stops = [len(measure.findall("barline[@location='right']/ending")) for measure in measures]
    check("an ending covers a selected stretch of bars", starts == [0, 1, 0, 0] and stops == [0, 0, 1, 0], (starts, stops))
    if shots:
        page.screenshot(path=str(shots / "repeats.png"), full_page=True)
    listed = json.loads(fetch_text(base, "/api/scores"))
    check("the score list uses the title", any(item.get("title") == "Test Song" for item in listed))


# Render what playback would send to the speakers, then measure which pitch sounds in each stretch.
HEAR = """async () => {
    const data = await (await fetch(`/api/scores/${scoreId}/playback`)).json();
    const beat = 60 / data.tempo;
    const rate = 44100;
    const audio = new OfflineAudioContext(1, Math.ceil((data.total * beat + 0.3) * rate), rate);
    schedule(audio, data, 0, 0);
    const samples = (await audio.startRendering()).getChannelData(0);
    const strength = (from, to, midi) => {
        const frequency = 440 * 2 ** ((midi - 69) / 12);
        const step = 2 * Math.PI * frequency / rate;
        let real = 0, imaginary = 0;
        for (let index = from; index < to; index++) {
            real += samples[index] * Math.cos(index * step);
            imaginary += samples[index] * Math.sin(index * step);
        }
        return Math.hypot(real, imaginary) / (to - from);
    };
    const heard = [];
    for (let at = 0; at < data.total; at += 0.5) {
        const from = Math.floor((at + 0.1) * beat * rate);
        const to = Math.floor((at + 0.4) * beat * rate);
        let best = null, loudest = 0.01;
        for (let midi = 36; midi <= 96; midi++) {
            const level = strength(from, to, midi);
            if (level > loudest) { best = midi; loudest = level; }
        }
        heard.push(best);
    }
    // A tied note keeps sounding, so compare the sound across each join with the sound after it.
    const steady = position => {
        const at = Math.floor(position * beat * rate);
        const width = Math.floor(0.03 * rate);
        return strength(at - width, at + width, 79) / strength(at + width * 3, at + width * 5, 79);
    };
    return { heard, tempo: data.tempo, seconds: samples.length / rate, plainJoin: steady(5), tiedJoin: steady(6) };
}"""


def notes_with(root: ET.Element, path: str) -> list[ET.Element]:
    return [note for note in root.iter("note") if note.find(path) is not None]


def written_melody(page, base: str, created: list[str], bars: int = 4, staff: str = "treble", tune: str = "cdefgabc") -> str:
    """Open a blank score, write a melody in quarter notes and stop entering."""
    score_id = open_blank(page, base, created, bars=bars, staff=staff)
    page.keyboard.press("5")
    page.keyboard.type(tune, delay=0)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    return score_id


def select_notes(page, first: int, last: int | None = None) -> None:
    """Click a note, and shift-click another to select the stretch between them."""
    click_on(page, page.locator("#pages g.note").nth(first))
    settle(page)
    if last is not None:
        page.keyboard.down("Shift")
        click_on(page, page.locator("#pages g.note").nth(last))
        page.keyboard.up("Shift")
        settle(page)


def press(page, key: str) -> None:
    page.keyboard.press(key)
    settle(page)


def test_musescore_keys(page, base: str, created: list[str]) -> None:
    """The keys someone coming from MuseScore reaches for: semitones, letters, Q and W, intervals, measures."""
    score_id = written_melody(page, base, created)
    first = lambda: read_notes(base, score_id)[0][1:5]
    tune = ["C5", "D5", "E5", "F5", "G5", "A5", "B5", "C6"]
    whole_tune = lambda: pitched(read_notes(base, score_id)) == tune and all(note[3] == "quarter" and not note[4] for note in read_notes(base, score_id)[:8])

    select_notes(page, 0)
    press(page, "ArrowUp")
    check("up raises a note a semitone, written with a sharp", first() == ("C", "5", "quarter", "sharp"), first())
    press(page, "Control+z")
    check("undo takes the semitone back", first() == ("C", "5", "quarter", ""), first())
    press(page, "Control+y")
    check("redo raises it again", first() == ("C", "5", "quarter", "sharp"), first())
    select_notes(page, 0)
    press(page, "ArrowDown")
    press(page, "ArrowDown")
    check("down lowers it a semitone each time, to a plain note where there is one", first() == ("B", "4", "quarter", ""), first())
    press(page, "ArrowDown")
    check("and to a flat where there isn't", first() == ("B", "4", "quarter", "flat"), first())
    press(page, "Control+z")
    press(page, "Alt+Shift+ArrowUp")
    check("alt, shift and up move a note one step of the scale", first() == ("C", "5", "quarter", ""), first())
    press(page, "Alt+Shift+ArrowDown")
    check("and down again", first() == ("B", "4", "quarter", ""), first())
    press(page, "Alt+Shift+ArrowUp")
    check("the tune is whole again", whole_tune(), pitched(read_notes(base, score_id)))

    select_notes(page, 0)
    press(page, "e")
    check("a letter renames the selected note", first() == ("E", "5", "quarter", ""), first())
    check("and leaves the notes after it alone", pitched(read_notes(base, score_id))[1:] == tune[1:])
    press(page, "a")
    check("taking the octave nearest where it was", first() == ("A", "5", "quarter", ""), first())
    press(page, "Control+z")
    press(page, "Control+z")
    check("each renaming is undone on its own", whole_tune(), pitched(read_notes(base, score_id)))
    press(page, "Control+y")
    check("and redone", first() == ("E", "5", "quarter", ""), first())
    press(page, "Control+z")
    click_on(page, page.locator("#pages g.mRest").first)
    settle(page)
    press(page, "g")
    notes = read_notes(base, score_id)
    check("a letter on a selected whole-measure rest writes a note that long", notes[8][1:4] == ("G", "5", "whole") or notes[8][1:4] == ("G", "6", "whole"), notes[8])
    press(page, "Control+z")

    select_notes(page, 0)
    press(page, "w")
    notes = read_notes(base, score_id)
    check("W makes a note twice as long, over the next", notes[0][3] == "half" and notes[1][1] == "E", notes[:3])
    press(page, "Control+z")
    check("undo gives the covered note back", whole_tune())
    select_notes(page, 0)
    press(page, "q")
    notes = read_notes(base, score_id)
    check("Q makes it half as long and leaves a rest", notes[0][3] == "eighth" and notes[1][1] == "rest" and notes[1][3] == "eighth", notes[:3])
    press(page, "Control+z")
    select_notes(page, 0)
    press(page, "Shift+W")
    head = saved_score(base, score_id).find(".//note")
    check("shift and W go to the dotted length", head.findtext("type") == "quarter" and len(head.findall("dot")) == 1)
    press(page, "Shift+W")
    head = saved_score(base, score_id).find(".//note")
    check("and on to the next plain one", head.findtext("type") == "half" and not head.findall("dot"))
    press(page, "Shift+Q")
    head = saved_score(base, score_id).find(".//note")
    check("shift and Q come back down through it", head.findtext("type") == "quarter" and len(head.findall("dot")) == 1)
    for _ in range(3):
        press(page, "Control+z")
    check("three undos put the tune back", whole_tune(), read_notes(base, score_id)[:5])

    select_notes(page, 0)
    press(page, "Alt+3")
    notes = read_notes(base, score_id)
    check("alt and 3 add a third above", notes[0][1:3] == ("C", "5") and notes[1][1:3] == ("E", "5") and notes[1][5], notes[:3])
    press(page, "Control+z")
    select_notes(page, 0)
    press(page, "Alt+8")
    notes = read_notes(base, score_id)
    check("alt and 8 add an octave above", notes[1][1:3] == ("C", "6") and notes[1][5], notes[:3])
    press(page, "Control+z")
    select_notes(page, 0)
    press(page, "Shift+G")
    notes = read_notes(base, score_id)
    check("shift and a letter add that note to the selected note's chord", notes[1][1:3] == ("G", "5") and notes[1][5], notes[:3])
    press(page, "Control+z")
    check("undo takes the added note off", whole_tune())

    ids = page.evaluate("() => [...document.querySelectorAll('#pages g.note')].map(note => note.id)")
    chosen = lambda: page.evaluate("() => selected")
    select_notes(page, 1)
    press(page, "Control+ArrowRight")
    check("control and right go to the next measure's first note", chosen() == ids[4], ids.index(chosen()) if chosen() in ids else chosen())
    press(page, "ArrowRight")
    press(page, "Control+ArrowLeft")
    check("control and left go to the start of this measure first", chosen() == ids[4])
    press(page, "Control+ArrowLeft")
    check("and then to the measure before", chosen() == ids[0])
    press(page, "Control+End")
    last = page.evaluate("() => [...document.querySelectorAll('#pages g.measure')].at(-1).contains(document.getElementById(selected))")
    check("control and End go to the end of the staff", last)
    press(page, "Control+Home")
    check("control and Home go back to its start", chosen() == ids[0])
    press(page, "Control+Shift+ArrowRight")
    check("with shift, a measure key stretches the selection", page.evaluate("() => range?.ids.length") == 5, page.evaluate("() => range?.ids.length"))
    press(page, "Control+Shift+End")
    check("and to the end of the staff", page.evaluate("() => range?.ids.length") == 10, page.evaluate("() => range?.ids.length"))
    press(page, "Escape")

    select_notes(page, 1)
    page.click('#noteBar [data-action="doubleSharp"]')
    settle(page)
    check("the toolbar's double sharp goes on the selected note", read_notes(base, score_id)[1][1:5] == ("D", "5", "quarter", "double-sharp"), read_notes(base, score_id)[1])
    check("and is drawn with its own sign", page.locator("#pages g.accid").count() == 1)
    select_notes(page, 2, 3)
    page.click('#accidentalRow [aria-label="Double flat"]')
    settle(page)
    flats = [note[4] for note in read_notes(base, score_id)[:4]]
    check("the inspector's double flat goes on a whole selection", flats == ["", "double-sharp", "flat-flat", "flat-flat"], flats)
    press(page, "Control+z")
    press(page, "Control+z")
    check("each is undone in one step", whole_tune())
    press(page, "Control+y")
    check("and redone", read_notes(base, score_id)[1][4] == "double-sharp")
    press(page, "Control+z")
    press(page, "Escape")

    press(page, "n")
    press(page, "5")
    pressed = lambda: page.get_attribute('#lengths button[aria-pressed="true"]', "data-type")
    press(page, "w")
    check("while entering notes, W picks the next longer duration", pressed() == "half", pressed())
    press(page, "q")
    press(page, "q")
    check("and Q the next shorter", pressed() == "eighth", pressed())
    press(page, "Shift+W")
    check("with shift they step through the dot", pressed() == "eighth" and page.get_attribute("#dotButton", "aria-pressed") == "true")
    press(page, "Shift+W")
    check("and off it again", pressed() == "quarter" and page.get_attribute("#dotButton", "aria-pressed") == "false")
    check("choosing a duration writes nothing", whole_tune())
    press(page, "Escape")


def drawn_signs(page, kind: str) -> list[str]:
    """List the symbols drawn for one kind of thing on the pages, by their codes."""
    return page.evaluate("""kind => [...document.querySelectorAll(`#pages g.${kind} use`)]
        .map(use => (use.getAttribute("xlink:href") ?? use.getAttribute("href")).slice(1, 5))""", kind)


def test_time_signs(page, base: str, shots: Path | None, created: list[str]) -> None:
    page.goto(base)
    new_score(page, "treble", 4, "2/2/cut")
    score_id = page.evaluate("() => scoreId")
    created.append(score_id)
    press(page, "Escape")
    times = lambda: [(index, found.findtext("beats"), found.get("symbol")) for index, measure in enumerate(saved_score(base, score_id).find("part").findall("measure")) for found in measure.findall("attributes/time")]
    check("a new score can start in cut time", times() == [(0, "2", "cut")], times())
    check("which is drawn as its sign and not as numbers", drawn_signs(page, "meterSig") == ["E08B"], drawn_signs(page, "meterSig"))
    click_on(page, page.locator("#pages g.mRest").nth(2))
    settle(page)
    choose(page, "time:common")
    settle(page)
    check("the palette's common time changes the time from the selected measure", times() == [(0, "2", "cut"), (2, "4", "common")], times())
    check("and both signs are drawn", drawn_signs(page, "meterSig") == ["E08B", "E08A"], drawn_signs(page, "meterSig"))
    if shots:
        page.screenshot(path=str(shots / "time-signs.png"), full_page=True)
    press(page, "Control+z")
    check("undo takes the change back", times() == [(0, "2", "cut")], times())
    press(page, "Control+y")
    check("redo makes it again", times() == [(0, "2", "cut"), (2, "4", "common")], times())
    click_on(page, page.locator("#pages g.mRest").nth(2))
    settle(page)
    choose(page, "time:4/4")
    settle(page)
    check("numbers replace a sign of the same time", times() == [(0, "2", "cut"), (2, "4", None)] and drawn_signs(page, "meterSig") == ["E08B", "E084", "E084"], (times(), drawn_signs(page, "meterSig")))
    click_on(page, page.locator("#pages g.mRest").first)
    settle(page)
    choose(page, "time:cut")
    settle(page)
    check("choosing the sign already there changes nothing that's written", times() == [(0, "2", "cut"), (2, "4", None)], times())


def test_barlines(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = written_melody(page, base, created, bars=8, tune="cdefgabcdefgabcdefgabcdefgabc")
    styles = lambda: [measure.findtext("barline/bar-style") for measure in saved_score(base, score_id).find("part").findall("measure")]
    check("a new score ends with a final barline", styles() == [None] * 7 + ["light-heavy"], styles())
    wanted = {"double": "light-light", "dashed": "dashed", "final": "light-heavy", "heavy": "heavy", "heavyDouble": "heavy-heavy", "dotted": "dotted"}
    for measure, style in enumerate(wanted):
        select_notes(page, measure * 4)
        choose(page, f"barline:{style}")
        settle(page)
    check("each palette barline ends the selected note's measure", styles() == [*wanted.values(), None, "light-heavy"], styles())
    if shots:
        page.screenshot(path=str(shots / "barlines.png"), full_page=True)
    press(page, "Control+z")
    check("undo takes the last one off", styles()[5] is None, styles())
    press(page, "Control+y")
    check("redo puts it back", styles()[5] == "dotted", styles())
    select_notes(page, 0, 6)
    choose(page, "barline:dashed")
    settle(page)
    check("with a stretch selected the barline goes at its end", styles()[:2] == ["light-light", "dashed"], styles())
    select_notes(page, 1)
    choose(page, "barline:single")
    settle(page)
    check("a single barline makes it plain again", styles()[0] is None, styles())
    select_notes(page, 4)
    choose(page, "barline:backward")
    settle(page)
    root = saved_score(base, score_id)
    check("a repeat sign takes a styled barline's place", root.find("part").findall("measure")[1].find("barline/repeat") is not None)
    select_notes(page, 4)
    choose(page, "barline:double")
    settle(page)
    second = saved_score(base, score_id).find("part").findall("measure")[1]
    check("and a barline takes a repeat sign's", second.find("barline/repeat") is None and second.findtext("barline/bar-style") == "light-light")
    press(page, "Escape")
    menu(page, "Add", "Measures", "Insert one measure at end of score")
    settle(page)
    check("a measure added at the end takes the final barline with it", styles()[-2:] == [None, "light-heavy"] and len(styles()) == 9, styles())
    check("nothing selected, the inspector shows the score and no barlines", page.is_visible("#propScore") and inspector_cell(page, "barline:double").count() == 0)
    page.keyboard.press("Control+Shift+P")
    page.fill("#commandInput", "double barline")
    check("and the command palette offers a barline only with a measure to put it on", page.get_attribute('#commandList li[data-cell="barline:double"]', "aria-disabled") == "true")
    page.keyboard.press("Escape")


def right_click(page, x: float, y: float) -> list[str]:
    """Right-click a point and return what the menu that opens offers."""
    page.mouse.click(x, y, button="right")
    settle(page)
    return page.evaluate("""() => [...document.querySelectorAll(".menu")].at(-1)
        ? [...[...document.querySelectorAll(".menu")].at(-1).querySelectorAll("button")].map(button => button.querySelector("span").textContent) : []""")


def pick(page, label: str) -> None:
    page.locator(".menu").last.locator(f'button:has(span:text-is("{label}"))').click()
    settle(page)


def centre_of(page, selector: str, index: int = 0) -> tuple[float, float]:
    box = page.evaluate("""([selector, index]) => {
        const found = document.querySelectorAll(selector)[index];
        const box = found.matches("g.note") ? headBox(found) : found.getBoundingClientRect();
        return [(box.left + box.right) / 2, (box.top + box.bottom) / 2];
    }""", [selector, index])
    return box[0], box[1]


def test_context_menu(page, base: str, created: list[str]) -> None:
    score_id = written_melody(page, base, created)
    tune = pitched(read_notes(base, score_id))
    ids = page.evaluate("() => [...document.querySelectorAll('#pages g.note')].map(note => note.id)")
    measures = lambda: len(saved_score(base, score_id).find("part").findall("measure"))

    offered = right_click(page, *centre_of(page, "#pages g.note", 1))
    check("right-clicking a note selects it", page.evaluate("() => selected") == ids[1])
    check("and opens a menu with the Edit menu's actions", offered[:4] == ["Cut", "Copy", "Paste", "Delete"] and "Delete selected measures" in offered and "Remove selected range" in offered, offered)
    check("and the Add menu's", all(label in offered for label in ["Notes", "Tuplets", "Measures", "Text", "Lines"]), offered)
    press(page, "Escape")
    check("Escape closes it", page.locator(".menu").count() == 0)
    right_click(page, *centre_of(page, "#pages g.note", 1))
    pick(page, "Delete")
    check("Delete from the menu turns the note into a rest", pitched(read_notes(base, score_id)) == [tune[0], *tune[2:]] and page.locator(".menu").count() == 0)
    press(page, "Control+z")
    check("which undo reverses", pitched(read_notes(base, score_id)) == tune)

    select_notes(page, 0, 1)
    right_click(page, *centre_of(page, "#pages g.note", 1))
    check("right-clicking inside a selected stretch keeps the stretch", page.evaluate("() => range?.ids.length") == 2)
    pick(page, "Copy")
    right_click(page, *centre_of(page, "#pages g.note", 6))
    check("right-clicking outside it selects the note clicked", page.evaluate("() => selected") == ids[6] and page.evaluate("() => range") is None)
    pick(page, "Paste")
    check("Copy and Paste from the menu work as the keys do", pitched(read_notes(base, score_id)) == [*tune[:6], tune[0], tune[1]], pitched(read_notes(base, score_id)))
    press(page, "Control+z")

    right_click(page, *centre_of(page, "#pages g.note", 2))
    page.locator(".menu").last.locator('button:has(span:text-is("Text"))').click()
    pick(page, "Staff text")
    check("a submenu's action runs from there too", page.is_visible("#editor"))
    page.fill("#chordInput", "dolce")
    press(page, "Enter")
    check("and the text is written", "dolce" in page.inner_text("#pages"))
    offered = right_click(page, *centre_of(page, "#pages g.dir"))
    check("right-clicking text selects it and offers to edit or delete it", offered == ["Edit text", "Delete"] and mark_chosen(page).startswith("Text: dolce"), offered)
    pick(page, "Edit text")
    check("Edit text opens it", page.is_visible("#editor") and page.input_value("#chordInput") == "dolce")
    press(page, "Escape")
    right_click(page, *centre_of(page, "#pages g.dir"))
    pick(page, "Delete")
    check("Delete removes it", "dolce" not in page.inner_text("#pages"))
    press(page, "Control+z")
    check("and undo brings it back", "dolce" in page.inner_text("#pages"))

    spot = empty_spot(page, 3)
    offered = right_click(page, spot["x"], spot["y"])
    check("right-clicking an empty part of a measure selects the measure", page.evaluate("() => range?.ids.length") == 1 and "Delete selected measures" in offered, offered)
    pick(page, "Delete selected measures")
    check("and its menu deletes it", measures() == 3)
    press(page, "Control+z")
    check("which undo reverses too", measures() == 4)

    paper = page.locator("#pages .page").first.bounding_box()
    offered = right_click(page, paper["x"] + 30, paper["y"] + paper["height"] / 2)
    check("right-clicking bare paper offers the score's own things", offered == ["Title and composer…", "Measures per line…", "Zoom in", "Zoom out"] and page.evaluate("() => selected") is None, offered)
    pick(page, "Title and composer…")
    check("such as its properties", page.is_visible("#projectDialog"))
    press(page, "Escape")

    press(page, "n")
    check("keys reach the score again once a dialog opened from a menu has closed", page.evaluate("() => editor.on"))
    offered = right_click(page, *centre_of(page, "#pages g.note", 3))
    check("while entering notes a right-click opens no menu and writes nothing", offered == [] and pitched(read_notes(base, score_id)) == tune, offered)
    press(page, "Escape")


def empty_spot(page, measure: int) -> dict:
    """Find a point on a measure's staff that's clear of its notes, with the staff's edges."""
    return page.evaluate("""index => {
        const staff = document.querySelectorAll("#pages g.measure")[index].querySelector("g.staff");
        const lines = staffLines(staff);
        const boxes = [...staff.querySelectorAll(EVENTS)].map(element => headBox(element));
        const clear = (x, y) => boxes.every(box => Math.hypot(Math.max(box.left - x, 0, x - box.right), Math.max(box.top - y, 0, y - box.bottom)) > 16)
            && !document.elementFromPoint(x, y)?.closest(EVENTS);
        for (const y of [lines.bottom, (lines.top + lines.bottom) / 2, lines.top]) {
            for (let x = lines.right - 3; x > lines.left; x -= 2) {
                if (clear(x, y)) return { x, y, left: lines.left, right: lines.right };
            }
        }
        return null;
    }""", measure)


def range_boxes(page) -> list[dict]:
    return page.evaluate("""() => [...document.querySelectorAll(".rangeBox")].map(box => {
        const drawn = box.getBoundingClientRect();
        return { left: drawn.left, right: drawn.right };
    })""")


def test_measure_selection(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = written_melody(page, base, created)
    measures = lambda: saved_score(base, score_id).find("part").findall("measure")
    tune = pitched(read_notes(base, score_id))
    count = lambda: page.evaluate("() => range?.ids.length ?? 0")

    first = empty_spot(page, 0)
    page.mouse.click(first["x"], first["y"])
    settle(page)
    check("clicking an empty part of a measure selects all of it", count() == 4, count())
    boxes = range_boxes(page)
    check("a box is drawn round the whole measure", len(boxes) == 1 and abs(boxes[0]["left"] - first["left"]) < 2 and abs(boxes[0]["right"] - first["right"]) < 2, (boxes, first))
    second = empty_spot(page, 1)
    page.keyboard.down("Shift")
    page.mouse.click(second["x"], second["y"])
    page.keyboard.up("Shift")
    settle(page)
    boxes = range_boxes(page)
    check("shift-clicking another measure selects up to its end", count() == 8, count())
    check("and the box takes both in", len(boxes) == 1 and abs(boxes[0]["left"] - first["left"]) < 2 and abs(boxes[0]["right"] - second["right"]) < 2, boxes)
    if shots:
        page.screenshot(path=str(shots / "measures.png"), full_page=True)
    press(page, "Control+Delete")
    check("control and Delete take the selected measures out of the score", len(measures()) == 2 and not pitched(read_notes(base, score_id)), len(measures()))
    check("and leave nothing selected", count() == 0 and not range_boxes(page))
    press(page, "Control+z")
    check("one undo brings them all back", len(measures()) == 4 and pitched(read_notes(base, score_id)) == tune)
    press(page, "Control+y")
    check("and one redo removes them again", len(measures()) == 2)
    press(page, "Control+z")

    select_notes(page, 1, 2)
    boxes = range_boxes(page)
    edges = empty_spot(page, 0)
    check("part of a measure gets a box round just those notes", len(boxes) == 1 and boxes[0]["left"] > edges["left"] + 5 and boxes[0]["right"] < edges["right"] - 5, boxes)
    press(page, "Control+Delete")
    left = pitched(read_notes(base, score_id))
    check("control and Delete on part of a measure pull the later notes in", len(measures()) == 4 and left[:2] == ["C5", "F5"], left)
    press(page, "Control+z")
    check("which undoes in one step", pitched(read_notes(base, score_id)) == tune)

    spot = empty_spot(page, 0)
    page.mouse.click(spot["x"], spot["y"])
    settle(page)
    press(page, "Delete")
    notes = read_notes(base, score_id)
    check("plain Delete empties a selected measure and keeps it", len(measures()) == 4 and pitched(notes) == tune[4:], notes[:3])
    press(page, "Control+z")
    spot = empty_spot(page, 3)
    page.mouse.click(spot["x"], spot["y"])
    settle(page)
    inside = page.evaluate("() => document.querySelectorAll('#pages g.measure')[3].contains(document.getElementById(selected))")
    check("an empty measure is selected by its rest", inside and count() == 1, count())
    press(page, "Escape")
    check("Escape clears the selection and its box", count() == 0 and not range_boxes(page) and page.evaluate("() => selected") is None)
    click_on(page, page.locator("#pages g.mRest").last)
    settle(page)
    press(page, "Control+Delete")
    check("control and Delete on a whole-measure rest remove its measure", len(measures()) == 3 and pitched(read_notes(base, score_id)) == tune)
    press(page, "Control+z")
    check("and undo brings the measure back", len(measures()) == 4)
    select_notes(page, 2, 5)
    menu(page, "Edit", "Delete selected measures")
    settle(page)
    check("deleting measures takes every measure the selection touches", len(measures()) == 2 and not pitched(read_notes(base, score_id)))
    press(page, "Control+z")
    check("in one step that undo reverses", len(measures()) == 4 and pitched(read_notes(base, score_id)) == tune)
    # Somewhere that's no measure at all lets go of everything.
    select_notes(page, 0)
    paper = page.locator("#pages .page").first.bounding_box()
    page.mouse.click(paper["x"] + 30, paper["y"] + paper["height"] / 2)
    settle(page)
    check("a click on bare paper selects nothing", page.evaluate("() => selected") is None)


def test_desk(page, base: str, created: list[str]) -> None:
    """Zooming with the wheel and moving the pages by dragging, as on MuseScore's canvas."""
    written_melody(page, base, created)
    factor = lambda: float(page.evaluate("() => getComputedStyle(document.documentElement).getPropertyValue('--zoom')"))
    page.evaluate("() => setZoom(100)")
    desk = page.locator("#desk").bounding_box()
    page.mouse.move(desk["x"] + desk["width"] / 2, desk["y"] + desk["height"] / 2)
    page.mouse.wheel(0, 200)
    check("the wheel alone doesn't zoom", factor() == 1.0, factor())
    page.keyboard.down("Control")
    page.mouse.wheel(0, -120)
    page.wait_for_function("() => getComputedStyle(document.documentElement).getPropertyValue('--zoom') > 1")
    check("control and the wheel zoom in", factor() > 1.0 and page.input_value("#zoomChoice") == str(round(factor() * 100)), (factor(), page.input_value("#zoomChoice")))
    page.mouse.wheel(0, 120)
    page.wait_for_function("() => Number(getComputedStyle(document.documentElement).getPropertyValue('--zoom')) === 1")
    page.keyboard.up("Control")
    check("and out again", factor() == 1.0, factor())

    page.evaluate("() => setZoom(200)")
    select_notes(page, 0)
    chosen = page.evaluate("() => selected")
    page.evaluate("() => { $('desk').scrollTop = 1000; $('desk').scrollLeft = 300; }")
    place = lambda: page.evaluate("() => [$('desk').scrollLeft, $('desk').scrollTop]")
    before = place()
    x, y = desk["x"] + desk["width"] / 2, desk["y"] + desk["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x - 120, y - 80, steps=6)
    page.mouse.up()
    settle(page)
    after = place()
    check("dragging the paper moves the pages with the pointer", after == [before[0] + 120, before[1] + 80], (before, after))
    check("and doesn't change what's selected", page.evaluate("() => selected") == chosen)
    page.mouse.click(x, y)
    settle(page)
    check("a click that doesn't move still selects nothing", page.evaluate("() => selected") is None and place() == after)
    page.evaluate("() => setZoom(100)")


def click_mark(page, selector: str, twice: bool = False) -> None:
    """Click a mark where a mouse would, in the middle of what's drawn. Once selects it, twice opens it."""
    box = page.evaluate("""selector => {
        const element = document.querySelector(selector);
        element.scrollIntoView({ block: "center" });
        const box = element.getBoundingClientRect();
        return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
    }""", selector)
    if twice:
        page.mouse.dblclick(box["x"], box["y"])
    else:
        page.mouse.click(box["x"], box["y"])
    settle(page)


def mark_chosen(page) -> str:
    """Say what the status bar calls the selected mark, or nothing if none is selected."""
    return page.inner_text("#toolLabel") if page.evaluate("() => selectedMark !== null") else ""


def slur_spot(page, piece: int = 0) -> dict:
    """Find a point on a drawn slur, a quarter of the way along it, in page coordinates."""
    return page.evaluate("""piece => {
        const path = document.querySelectorAll("#pages g.slur")[piece].querySelector("path");
        path.scrollIntoView({ block: "center" });
        const { x, y } = path.getPointAtLength(path.getTotalLength() / 4);
        const spot = new DOMPoint(x, y).matrixTransform(path.getScreenCTM());
        return { x: spot.x, y: spot.y };
    }""", piece)


def test_marks_over_a_line_break(page, base: str, created: list[str]) -> None:
    score_id = written_melody(page, base, created, tune="cdefgabcdefgabcd", bars=8)
    system_breaks(page, 2)
    settle(page)
    select_notes(page, 5, 10)
    page.keyboard.press("s")
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    check("a slur over a line break is drawn in two pieces", page.locator("#pages g.slur").count() == 2)
    spot = slur_spot(page, 1)
    page.mouse.click(spot["x"], spot["y"])
    settle(page)
    check("its second piece can be clicked too", mark_chosen(page).startswith("Slur"), page.inner_text("#toolLabel"))
    check("and both pieces show as selected", page.locator("#pages g.slur.selected").count() == 2)
    page.keyboard.press("Delete")
    settle(page)
    check("and Delete removes the whole slur", not notes_with(saved_score(base, score_id), "notations/slur"))
    page.keyboard.press("Control+z")
    settle(page)
    check("undo brings the slur back", len(notes_with(saved_score(base, score_id), "notations/slur")) == 2)
    page.keyboard.press("Control+y")
    settle(page)
    check("redo removes it again", not notes_with(saved_score(base, score_id), "notations/slur"))


def test_octave_and_pedal_lines(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = written_melody(page, base, created, tune="cdefgabcdefgabcd", bars=8)
    plain = head_heights(page)
    step = page.evaluate("() => staffLines(document.querySelector('#pages g.staff')).step")
    select_notes(page, 0, 2)
    choose(page, "line:8va")
    settle(page)
    check("an 8va line is drawn", page.locator("#pages g.octave").count() == 1)
    lowered = head_heights(page)
    check("the notes under it are drawn an octave lower", all(abs(low - high - 7 * step) < 1.5 for high, low in zip(plain[:3], lowered[:3])) and abs(lowered[3] - plain[3]) < 1, (plain[:4], lowered[:4]))
    octaves = [note.findtext("pitch/octave") for note in saved_score(base, score_id).iter("note") if note.find("pitch") is not None][:3]
    check("and keep the pitch they sound at", octaves == ["5", "5", "5"], octaves)
    sounding = page.evaluate("async () => (await (await fetch(`/api/scores/${scoreId}/playback`)).json()).notes.slice(0, 3).map(note => note.midi)")
    check("so they play as written", sounding == [72, 74, 76], sounding)
    page.keyboard.press("n")
    settle(page)
    click_staff(page, page.evaluate("() => document.querySelectorAll('#pages g.note')[1].id"), 0)
    entered = [(note.findtext("pitch/step"), note.findtext("pitch/octave")) for note in saved_score(base, score_id).iter("note") if note.find("pitch") is not None][1]
    check("a note clicked onto the top line under the 8va is the F an octave up", entered == ("F", "6"), entered)
    page.keyboard.press("Escape")
    settle(page)
    click_mark(page, "#pages g.octave")
    check("the line can be selected", mark_chosen(page).startswith("Ottava"), page.inner_text("#toolLabel"))
    if shots:
        page.screenshot(path=str(shots / "lines.png"))
    page.keyboard.press("Delete")
    settle(page)
    check("Delete removes the whole line", page.locator("#pages g.octave").count() == 0 and not list(saved_score(base, score_id).iter("octave-shift")))
    select_notes(page, 4, 6)
    menu(page, "Add", "Lines", "Ottava 8va bassa")
    settle(page)
    check("Add ▸ Lines puts an 8va bassa below", [shift.get("type") for shift in saved_score(base, score_id).iter("octave-shift")] == ["up", "stop"])
    select_notes(page, 8, 10)
    choose(page, "line:pedal-pedstar")
    settle(page)
    check("a pedal is drawn as Ped. and a star", page.locator("#pages g.pedal").count() == 2)
    held = page.evaluate("async () => (await (await fetch(`/api/scores/${scoreId}/playback`)).json()).notes.slice(8, 11).map(note => note.start + note.length)")
    check("and holds its notes until it comes up", held == [11, 11, 11], held)
    select_notes(page, 12, 14)
    choose(page, "line:pedal-line")
    settle(page)
    click_mark(page, "#pages g.pedal:has(rect)")
    check("a pedal line can be selected", mark_chosen(page).startswith("Pedal"), page.inner_text("#toolLabel"))
    if shots:
        page.screenshot(path=str(shots / "pedals.png"))
    page.keyboard.press("Escape")
    settle(page)


def test_note_marks(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = written_melody(page, base, created)
    select_notes(page, 0)
    page.keyboard.press("Shift+S")
    settle(page)
    check("shift-S puts a staccato on the note", len(notes_with(saved_score(base, score_id), "notations/articulations/staccato")) == 1)
    check("the staccato is drawn", page.locator("#pages g.artic").count() >= 1)
    page.keyboard.press("Shift+S")
    settle(page)
    check("again takes it off", not notes_with(saved_score(base, score_id), "notations/articulations/staccato"))
    page.keyboard.press("Control+z")
    settle(page)
    check("undo brings a mark back", len(notes_with(saved_score(base, score_id), "notations/articulations/staccato")) == 1)

    select_notes(page, 1, 3)
    choose(page, "mark:accent")
    settle(page)
    check("a mark goes on a whole selection", len(notes_with(saved_score(base, score_id), "notations/articulations/accent")) == 3)
    select_notes(page, 4)
    choose(page, "mark:fermata")
    settle(page)
    check("a fermata is drawn", page.locator("#pages g.fermata").count() == 1)
    select_notes(page, 5)
    choose(page, "mark:trill")
    settle(page)
    check("a trill is drawn", page.locator("#pages g.trill").count() == 1)

    select_notes(page, 1, 3)
    page.keyboard.press("s")
    settle(page)
    root = saved_score(base, score_id)
    check("S slurs a stretch", len(notes_with(root, "notations/slur")) == 2)
    check("the slur is drawn", page.locator("#pages g.slur").count() == 1)
    page.keyboard.press("Escape")
    settle(page)
    spot = slur_spot(page)
    page.mouse.click(spot["x"], spot["y"] - 4)
    settle(page)
    check("a click just off a slur still finds it", mark_chosen(page).startswith("Slur") and page.is_hidden("#editor"), page.inner_text("#toolLabel"))
    page.keyboard.press("Escape")
    settle(page)
    check("Escape lets go of it", mark_chosen(page) == "" and page.locator("#pages g.slur.selected").count() == 0)
    # At its ends a slur touches the note, which is where a click could go to the wrong one.
    click_on(page, page.locator("#pages g.note").nth(1))
    settle(page)
    check("a note under the end of a slur can still be selected", page.is_hidden("#editor") and page.evaluate("() => selected") is not None)
    # Where a stem really touches the curve, a click lands on both, and the note has to win.
    check("a click on a note beats a slur right beside it", page.evaluate("""() => {
        const path = document.querySelector("#pages g.slur path");
        const { x, y } = path.getPointAtLength(0);
        const spot = new DOMPoint(x, y).matrixTransform(path.getScreenCTM());
        return markAt({ target: document.querySelector("#pages g.note"), clientX: spot.x, clientY: spot.y }) === null;
    }"""))
    page.keyboard.press("Escape")
    settle(page)
    spot = slur_spot(page)
    page.mouse.click(spot["x"], spot["y"])
    settle(page)
    check("clicking a slur selects it", mark_chosen(page).startswith("Slur"), page.inner_text("#toolLabel"))
    check("the inspector names it and offers to delete it", page.inner_text("#propMarkKind") == "Slur" and page.is_visible("#markDelete") and page.is_hidden("#markEdit"))
    page.click("#markDelete")
    settle(page)
    check("the slur can be removed from there", not notes_with(saved_score(base, score_id), "notations/slur"))
    check("and the inspector goes back to the score", page.is_hidden("#propMark") and page.is_visible("#propScore"))

    select_notes(page, 2, 5)
    page.keyboard.press("<")
    settle(page)
    check("< draws a crescendo", page.locator("#pages g.hairpin").count() == 1)
    select_notes(page, 2, 5)
    page.keyboard.press("<")
    settle(page)
    check("the same again takes it away", page.locator("#pages g.hairpin").count() == 0)
    select_notes(page, 2, 5)
    choose(page, "hairpin:diminuendo")
    settle(page)
    root = saved_score(base, score_id)
    check("the button draws a diminuendo", [wedge.get("type") for wedge in root.iter("wedge")] == ["diminuendo", "stop"])
    click_mark(page, "#pages g.hairpin")
    check("clicking a hairpin selects it", mark_chosen(page).startswith("Hairpin"), page.inner_text("#toolLabel"))
    page.keyboard.press("Delete")
    settle(page)
    check("a selected hairpin is removed with Delete", not list(saved_score(base, score_id).iter("wedge")))

    select_notes(page, 2)
    page.keyboard.press("/")
    settle(page)
    root = saved_score(base, score_id)
    grace = notes_with(root, "grace")
    check("/ adds a grace note", len(grace) == 1)
    check("the grace note is selected, ready to move", page.evaluate("() => selected") == grace[0].get("id"))
    page.keyboard.press("ArrowUp")
    settle(page)
    moved = notes_with(saved_score(base, score_id), "grace")[0]
    check("the grace note moves with the arrows", moved.findtext("pitch/step") == "F", moved.findtext("pitch/step"))
    page.keyboard.press("Delete")
    settle(page)
    check("the grace note can be deleted", not notes_with(saved_score(base, score_id), "grace"))
    if shots:
        page.screenshot(path=str(shots / "marks_notes.png"))


def test_placed_marks(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = written_melody(page, base, created)
    select_notes(page, 0)
    choose(page, "dynamic:p")
    settle(page)
    check("a dynamic is drawn", page.locator("#pages g.dynam").count() == 1)
    check("a drawn dynamic knows what it is", page.get_attribute("#pages g.dynam", "data-mark") is not None)
    click_mark(page, "#pages g.dynam")
    check("clicking a dynamic selects it and says what it is", mark_chosen(page).startswith("Dynamic: p") and page.is_hidden("#editor"), page.inner_text("#toolLabel"))
    check("the selected dynamic is drawn in the selection colour", page.locator("#pages g.dynam.selected").count() == 1)
    click_mark(page, "#pages g.dynam", twice=True)
    check("double-clicking a dynamic opens its name", page.is_visible("#editor") and page.input_value("#chordInput") == "p", page.input_value("#chordInput"))
    page.fill("#chordInput", "ff")
    page.keyboard.press("Enter")
    settle(page)
    root = saved_score(base, score_id)
    check("a dynamic can be changed where it's drawn", root.find(".//dynamics/ff") is not None)
    click_mark(page, "#pages g.dynam")
    page.keyboard.press("F2")
    check("F2 opens a selected dynamic too", page.is_visible("#editor") and page.input_value("#chordInput") == "ff")
    page.fill("#chordInput", "loud")
    page.keyboard.press("Enter")
    settle(page)
    check("a nonsense dynamic is refused, and says why", page.is_visible("#editorError") and "dynamic" in page.inner_text("#editorError"))
    page.keyboard.press("Escape")
    click_mark(page, "#pages g.dynam", twice=True)
    page.click("#chordDelete")
    settle(page)
    check("an open dynamic can be removed", saved_score(base, score_id).find(".//dynamics") is None)
    page.keyboard.press("Control+z")
    settle(page)
    click_mark(page, "#pages g.dynam")
    page.keyboard.press("Delete")
    settle(page)
    check("a selected dynamic is removed with Delete", saved_score(base, score_id).find(".//dynamics") is None)
    page.keyboard.press("Control+z")
    settle(page)
    check("undo brings the dynamic back", saved_score(base, score_id).find(".//dynamics/ff") is not None)
    page.keyboard.press("Control+y")
    settle(page)
    check("redo removes it again", saved_score(base, score_id).find(".//dynamics") is None)

    select_notes(page, 0)
    choose(page, "tempo:Allegro 144")
    settle(page)
    root = saved_score(base, score_id)
    check("a tempo from the palette is saved", root.find(".//metronome/per-minute").text == "144")
    check("the tempo is drawn", page.locator("#pages g.tempo").count() == 1)
    select_notes(page, 4)
    menu(page, "Add", "Text", "Tempo marking")
    page.fill("#chordInput", "Adagio 66")
    page.keyboard.press("Enter")
    settle(page)
    check("a typed tempo is saved", [found.text for found in saved_score(base, score_id).iter("per-minute")] == ["144", "66"])

    select_notes(page, 1)
    choose(page, "text:staff")
    page.fill("#chordInput", "solo")
    page.keyboard.press("Enter")
    settle(page)
    check("staff text is drawn", "solo" in page.inner_text("#pages"))
    click_mark(page, "#pages g.dir", twice=True)
    check("double-clicking text opens it", page.input_value("#chordInput") == "solo")
    page.fill("#chordInput", "tutti")
    page.keyboard.press("Enter")
    settle(page)
    check("text can be changed where it's drawn", "tutti" in page.inner_text("#pages") and "solo" not in page.inner_text("#pages"))
    click_mark(page, "#pages g.dir")
    check("clicking text selects it and says what it says", mark_chosen(page).startswith("Text: tutti") and page.is_hidden("#editor"), page.inner_text("#toolLabel"))
    check("the inspector offers to edit or delete it", page.inner_text("#propMarkKind") == "Text" and page.is_visible("#markEdit") and page.is_visible("#markDelete"))
    page.click("#markEdit")
    check("Edit text opens it", page.is_visible("#editor") and page.input_value("#chordInput") == "tutti")
    page.keyboard.press("Escape")
    click_mark(page, "#pages g.dir")
    page.keyboard.press("Delete")
    settle(page)
    check("Delete removes selected text", "tutti" not in page.inner_text("#pages"))
    check("and nothing is left selected", mark_chosen(page) == "" and page.is_hidden("#propMark"))
    page.keyboard.press("Control+z")
    settle(page)
    check("undo brings the text back", "tutti" in page.inner_text("#pages"))
    page.keyboard.press("Control+y")
    settle(page)
    check("redo removes it again", "tutti" not in page.inner_text("#pages"))
    page.keyboard.press("Control+z")
    settle(page)
    select_notes(page, 2)
    choose(page, "text:rehearsal")
    settle(page)
    check("a rehearsal letter is drawn", page.locator("#pages g.reh").count() == 1)

    select_notes(page, 0)
    choose(page, "jump:segno")
    settle(page)
    check("a segno is drawn", page.locator("#pages g.repeatMark").count() == 1)
    click_mark(page, "#pages g.repeatMark")
    check("clicking a sign selects it", mark_chosen(page).startswith("Sign"), page.inner_text("#toolLabel"))
    page.keyboard.press("Backspace")
    settle(page)
    check("a selected sign is removed with Backspace", page.locator("#pages g.repeatMark").count() == 0)
    select_notes(page, 7)
    choose(page, "jump:dc")
    settle(page)
    check("a jump is drawn", "D.C." in page.inner_text("#pages"))

    select_notes(page, 3)
    choose(page, "clef:bass")
    settle(page)
    root = saved_score(base, score_id)
    check("a clef change is saved in that bar", root.find("part").findall("measure")[0].find("attributes/clef/sign").text == "F")
    if shots:
        page.screenshot(path=str(shots / "marks_placed.png"))


def test_voice_key_waits_its_turn(page, base: str, created: list[str]) -> None:
    score_id = open_blank(page, base, created, bars=2)
    page.keyboard.press("5")
    with_slow_server(page, True)
    page.keyboard.type("cd", delay=0)
    page.keyboard.press("Control+Alt+2")
    page.keyboard.type("a", delay=0)
    settle(page)
    with_slow_server(page, False)
    notes = saved_score(base, score_id).find("part").find("measure").findall("note")
    sounding = [(note.findtext("voice"), note.findtext("pitch/step")) for note in notes if note.find("pitch") is not None]
    check("notes still waiting keep the voice they were typed in", sounding == [("1", "C"), ("1", "D"), ("2", "A")], sounding)
    page.keyboard.press("Escape")
    settle(page)


def test_voices(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = written_melody(page, base, created, tune="cdef")
    select_notes(page, 0)
    page.keyboard.press("n")
    settle(page)
    with_slow_server(page, True)
    page.keyboard.press("Control+Alt+2")
    page.keyboard.press("6")
    page.keyboard.type("ab", delay=0)
    settle(page)
    with_slow_server(page, False)
    root = saved_score(base, score_id)
    upper = [note for note in root.find("part").findall("measure")[0].findall("note") if note.findtext("voice") == "2"]
    check("a voice chosen by key is used at once", [note.findtext("pitch/step") for note in upper] == ["A", "B"], [note.findtext("pitch/step", "rest") for note in upper])
    check("the first voice is left alone", [note.findtext("pitch/step") for note in root.find("part").findall("measure")[0].findall("note") if note.findtext("voice") == "1"] == list("CDEF"))
    check("the voice button shows it", page.get_attribute("#voices button[data-voice='2']", "aria-pressed") == "true")
    check("two voices are drawn", page.locator("#pages g.measure").first.locator("g.layer").count() == 2)
    check("nothing is wrong with the bar", page.is_hidden("#barChecks"))
    page.keyboard.press("6")
    page.keyboard.type("c", delay=0)
    settle(page)
    bars = saved_score(base, score_id).find("part").findall("measure")
    check("a voice carries on over the barline", [note.findtext("pitch/step", "rest") for note in bars[1].findall("note") if note.findtext("voice") == "2"][0] == "C")
    page.keyboard.press("Control+Alt+1")
    page.keyboard.press("Escape")
    settle(page)
    click_on(page, page.locator("#pages g.measure").first.locator("g.layer").nth(1).locator("g.note").first)
    settle(page)
    start = page.evaluate("() => selected")
    page.keyboard.press("ArrowRight")
    settle(page)
    check("the arrows stay in a voice", page.evaluate("() => selected") != start and page.evaluate("() => layerNumber(document.getElementById(selected))") == 1)
    last = page.evaluate("""() => [...document.querySelector("#pages g.measure g.layer").querySelectorAll("g.note")].pop().id""")
    click_on(page, page.locator(f"[id='{last}']"))
    settle(page)
    page.keyboard.press("ArrowRight")
    settle(page)
    check("the arrow leaves the end of a voice for the next bar, not back to the bar's start", page.evaluate("() => document.getElementById(selected).closest('g.measure') !== document.querySelector('#pages g.measure')"))
    select_notes(page, 1)
    page.keyboard.press("Control+Alt+3")
    settle(page)
    voices = {note.findtext("voice") for note in saved_score(base, score_id).find("part").findall("measure")[0].findall("note")}
    check("a selection moves to another voice", "3" in voices, voices)
    if shots:
        page.screenshot(path=str(shots / "marks_voices.png"))


def test_transposing(page, base: str, created: list[str]) -> None:
    score_id = written_melody(page, base, created, tune="cdef")
    select_notes(page, 0)
    menu(page, "Add", "Text", "Chord symbol")
    page.fill("#chordInput", "Cmaj7")
    page.keyboard.press("Enter")
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    transpose(page, key=2)
    settle(page)
    root = saved_score(base, score_id)
    check("the whole score moves to D", root.find(".//fifths").text == "2")
    check("its notes move with it", [note.findtext("pitch/step") for note in root.iter("note") if note.find("pitch") is not None][:4] == list("DEFG"))
    check("its chords move with it", page.evaluate("() => score.chords[0].text") == "Dmaj7", page.evaluate("() => score.chords[0].text"))
    page.keyboard.press("Control+z")
    settle(page)
    check("one undo takes it all back", saved_score(base, score_id).find(".//fifths").text == "0")
    select_notes(page, 1, 2)
    transpose(page, interval="M2", direction="up")
    settle(page)
    root = saved_score(base, score_id)
    check("a selection moves alone", [note.findtext("pitch/step") for note in root.iter("note") if note.find("pitch") is not None][:4] == list("CEFF"))
    check("the key stays", root.find(".//fifths").text == "0")


def test_instruments_and_layout(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = written_melody(page, base, created, bars=12, tune="cdef")
    score_pane(page)
    page.click("#partAdd")
    page.click('#partChooser li:text-is("Brass")')
    page.dblclick('#partChooser [data-instrument="trumpet"]')
    page.click("#partOk")
    settle(page)
    check("the inspector lists both instruments", page.locator("#partList li").all_inner_texts() == ["Music", "Trumpet in B♭"], page.locator("#partList li").all_inner_texts())
    root = saved_score(base, score_id)
    check("an instrument adds a part", len(root.findall("part")) == 2)
    check("its bars match", len(root.findall("part")[1].findall("measure")) == 12)
    check("a B flat trumpet reads two sharps more", root.findall("part")[1].find(".//fifths").text == "2")
    check("both staves are drawn", page.locator("#pages g.measure").first.locator("g.staff").count() == 2)
    page.click("#partsButton")
    check("each part is offered for printing", page.locator("#partsList li").count() == 2)
    page.evaluate("() => { window.asked = []; window.open = address => { window.asked.push(address); }; }")
    page.dblclick("#partsList li >> nth=1")
    asked = page.evaluate("() => window.asked")
    check("a part opens as its own PDF", len(asked) == 1 and asked[0].endswith("score.pdf?part=1"), asked)
    check("and the list of parts closes", page.is_hidden("#partsDialog"))
    lower = page.evaluate("""() => document.querySelector("#pages g.measure").querySelectorAll("g.staff")[1].querySelector("g.mRest, g.rest").id""")
    click_on(page, page.locator(f"[id='{lower}']"))
    settle(page)
    page.keyboard.press("n")
    settle(page)
    page.keyboard.press("5")
    page.keyboard.type("gg", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    root = saved_score(base, score_id)
    check("notes can be written in the new part", len([note for note in root.findall("part")[1].iter("note") if note.find("pitch") is not None]) == 2)
    with urllib.request.urlopen(f"{base}/api/scores/{score_id}/score.pdf?part=1") as response:
        check("one part prints as a PDF", response.read(5) == b"%PDF-")
    with urllib.request.urlopen(f"{base}/api/scores/{score_id}/score.mid") as response:
        check("a MIDI file downloads", response.read(4) == b"MThd")
    click_on(page, page.locator("#pages g.measure").first.locator("g.staff").nth(1).locator("g.note").first)
    settle(page)
    check("selecting a note selects its instrument in the inspector", page.get_attribute("#partList li >> nth=1", "aria-selected") == "true")
    check("and in the instrument switcher", page.inner_text("#partName") == "Trumpet in B♭", page.inner_text("#partName"))
    score_pane(page)
    page.click("#partRemove")
    settle(page)
    check("an instrument can be removed", len(saved_score(base, score_id).findall("part")) == 1)

    bars_in_lines = """() => [...document.querySelectorAll("#pages g.system")].map(system => system.querySelectorAll("g.measure").length)"""
    system_breaks(page, 4)
    settle(page)
    check("bars can be set to 4 a line", page.evaluate(bars_in_lines) == [4, 4, 4], page.evaluate(bars_in_lines))
    system_breaks(page, 0)
    settle(page)
    check("and given back to the page", page.evaluate(bars_in_lines) != [4, 4, 4], page.evaluate(bars_in_lines))
    click_on(page, page.locator("#pages g.measure").nth(1).locator("g.rest, g.mRest").first)
    settle(page)
    page.keyboard.press("Enter")
    settle(page)
    check("Enter starts a new line at a bar", page.evaluate(bars_in_lines)[0] == 1, page.evaluate(bars_in_lines))
    page.keyboard.press("Enter")
    settle(page)
    check("again takes the break away", page.evaluate(bars_in_lines)[0] > 1, page.evaluate(bars_in_lines))


def head_heights(page) -> list[float]:
    """The height of each notehead on the page, top to bottom being smaller to larger."""
    return page.evaluate("""() => [...document.querySelectorAll("#pages g.note")].map(note => note.querySelector(".notehead").getBoundingClientRect().top)""")


def test_concert_pitch(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = written_melody(page, base, created, staff="trumpet", tune="cdef")
    check("concert pitch starts off", page.get_attribute("#concertPitch", "aria-pressed") == "false")
    check("a trumpet in concert C reads D major", page.locator("#pages g.keySig g.keyAccid").count() == 2, page.locator("#pages g.keySig g.keyAccid").count())
    written = head_heights(page)
    step = page.evaluate("() => staffLines(document.querySelector('#pages g.staff')).step")
    score_pane(page)
    page.click("#concertPitch")
    settle(page)
    check("the inspector's button turns concert pitch on", page.get_attribute("#concertPitch", "aria-pressed") == "true")
    ticked = menu_ticked(page, "View", "Concert pitch")
    check("and View ▸ Concert pitch is ticked", ticked == "true", ticked)
    sounding = head_heights(page)
    check("every note is drawn a step lower", all(abs(low - high - step) < 1 for high, low in zip(written, sounding)), (written, sounding, step))
    check("the key is drawn as concert C", page.locator("#pages g.keySig g.keyAccid").count() == 0)
    if shots:
        page.screenshot(path=str(shots / "concert.png"))
    select_notes(page, 0)
    press(page, "g")
    steps = [note.findtext("pitch/step") for note in saved_score(base, score_id).iter("note") if note.find("pitch") is not None]
    check("a letter at concert pitch renames to the note that sounds", steps[:4] == list("ADEF"), steps)
    root = saved_score(base, score_id)
    check("the file keeps the trumpet's written notes and key", root.find(".//fifths").text == "2" and root.find(".//transpose") is not None)
    menu(page, "View", "Concert pitch")
    settle(page)
    check("View ▸ Concert pitch turns it off again", page.get_attribute("#concertPitch", "aria-pressed") == "false")
    press(page, "Control+z")
    check("turning it off is undone like an edit", page.get_attribute("#concertPitch", "aria-pressed") == "true")
    press(page, "Escape")


def test_more_clefs_and_instruments(page, base: str, shots: Path | None, created: list[str]) -> None:
    page.goto(base)
    page.click("#blankOpen")
    families = page.locator('#newChooser [data-list="families"] li').all_inner_texts()
    check("the families are MuseScore's, in its order", families == ["Woodwinds", "Free Reed", "Brass", "Percussion - Pitched", "Vocals", "Keyboards", "Strings - Plucked", "Strings - Bowed", "Plain staves"], families)
    page.click('#newChooser li:text-is("Percussion - Pitched")')
    offered = page.locator('#newChooser [data-list="instruments"] li').all_inner_texts()
    check("pitched percussion has its instruments", offered == ["Timpani", "Glockenspiel", "Xylophone", "Vibraphone", "Marimba"], offered)
    page.fill("#newChooser input", "clarinet")
    offered = page.locator('#newChooser [data-list="instruments"] li').all_inner_texts()
    check("the clarinets can be searched for", offered == ["Clarinet in E♭", "Clarinet in B♭", "Clarinet in A", "Bass Clarinet"], offered)
    if shots:
        page.screenshot(path=str(shots / "instruments.png"))
    page.click("#blankCancel")
    score_id = written_melody(page, base, created, staff="bass-clarinet", tune="cdefgabcdefg")
    root = saved_score(base, score_id)
    check("a bass clarinet sounds a ninth below how it's written", (root.findtext(".//transpose/diatonic"), root.findtext(".//transpose/chromatic"), root.findtext(".//transpose/octave-change")) == ("-1", "-2", "-1"))
    check("and says what it is for other programs", root.findtext(".//score-instrument/instrument-sound") == "wind.reed.clarinet.bass")
    select_notes(page, 4)
    choose(page, "clef:bass8vb")
    settle(page)
    clefs = page.evaluate("() => [...document.querySelectorAll('#pages g.clef use')].map(use => (use.getAttribute('xlink:href') ?? use.getAttribute('href')).slice(1, 5))")
    check("a bass clef 8va bassa can be chosen", "E064" in clefs, clefs)
    select_notes(page, 8)
    choose(page, "clef:baritoneC")
    settle(page)
    shapes = [(clef.findtext("sign"), clef.findtext("line"), clef.findtext("clef-octave-change")) for clef in saved_score(base, score_id).iter("clef")]
    check("so can a baritone C clef", shapes == [("G", "2", None), ("F", "4", "-1"), ("C", "5", None)], shapes)
    select_notes(page, 8)
    choose(page, "clef:percussion")
    settle(page)
    shapes = [(clef.findtext("sign"), clef.findtext("line")) for clef in saved_score(base, score_id).iter("clef")]
    check("and a percussion clef in its place", shapes[2] == ("percussion", None), shapes)
    clefs = page.evaluate("() => [...document.querySelectorAll('#pages g.clef use')].map(use => (use.getAttribute('xlink:href') ?? use.getAttribute('href')).slice(1, 5))")
    check("each clef is drawn", "E064" in clefs and "E069" in clefs, clefs)
    if shots:
        page.screenshot(path=str(shots / "clefs.png"))


def test_continuous_view(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = written_melody(page, base, created, bars=16, tune="cdefgabcdefgabcdefgabc")
    menu(page, "File", "Title and composer…")
    page.fill("#titleInput", "Long Tune")
    page.click("#projectDialog button[type=submit]")
    settle(page)
    systems = lambda: page.locator("#pages g.system").count()
    check("page view lays the music out in systems", systems() > 1, systems())
    page.select_option("#viewMode", "horizontal")
    settle(page)
    check("the horizontal continuous view is one long system", systems() == 1 and page.locator("#pages .page").count() == 1)
    check("without the title frame, as in MuseScore", page.locator("#pages g.pgHead").count() == 0)
    width = page.evaluate("() => score.pageSizes[0][0]")
    check("and as wide as its music", width > 1.5 * 210, width)
    ticked = menu_ticked(page, "View", "One line")
    check("View shows which view it is", ticked == "true", ticked)
    click_on(page, page.locator("#pages g.note").nth(12))
    settle(page)
    check("a note far along can be clicked", page.evaluate("() => selected") == page.locator("#pages g.note").nth(12).get_attribute("id"))
    page.keyboard.press("ArrowRight")
    settle(page)
    check("and the arrows go on from it", page.evaluate("() => selected") == page.locator("#pages g.note").nth(13).get_attribute("id"))
    page.keyboard.press("n")
    settle(page)
    rest = page.evaluate("() => document.querySelectorAll('#pages g.measure')[10].querySelector('g.rest, g.mRest').id")
    click_staff(page, rest, 0)
    written = [note for note in saved_score(base, score_id).findall("part/measure")[10].iter("note") if note.find("pitch") is not None]
    check("a click on the staff writes a note there", [(note.findtext("pitch/step"), note.findtext("pitch/octave")) for note in written] == [("F", "5")])
    page.keyboard.press("Escape")
    settle(page)
    if shots:
        page.screenshot(path=str(shots / "continuous.png"))
    menu(page, "View", "One long page")
    settle(page)
    check("the vertical continuous view is one long page", page.locator("#pages .page").count() == 1 and systems() > 1)
    check("which keeps its title", page.locator("#pages g.pgHead").count() == 1)
    page.reload()
    page.wait_for_selector("#pages svg")
    check("the score keeps its view", page.input_value("#viewMode") == "vertical")
    check("which is no edit to undo", page.evaluate("() => score.canUndo") is True and json.loads(fetch_text(base, f"/api/scores/{score_id}"))["view"] == "vertical")
    check("it still prints", urllib.request.urlopen(f"{base}/api/scores/{score_id}/score.pdf").read(5) == b"%PDF-")
    page.select_option("#viewMode", "page")
    settle(page)
    check("and goes back to page view", systems() > 1 and page.locator("#pages g.pgHead").count() == 1)


def post_json(base: str, path: str, body: dict) -> dict:
    request = urllib.request.Request(f"{base}{path}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def test_one_line_keeps_its_start_in_sight(page, base: str, shots: Path | None, created: list[str]) -> None:
    """The one-line view keeps the names, braces, clefs and keys at the left as it scrolls, as they are there."""
    score_id = open_blank(page, base, created, bars=40, staff="piano")
    page.keyboard.press("Escape")
    settle(page)
    post_json(base, f"/api/scores/{score_id}/parts", {"action": "add", "names": ["violin"]})
    measures = saved_score(base, score_id).find("part").findall("measure")
    bass_rest = next(note.get("id") for note in measures[2].iter("note") if note.findtext("staff") == "2")
    post_json(base, f"/api/scores/{score_id}/bars", {"action": "clef", "note": bass_rest, "clef": "treble"})
    post_json(base, f"/api/scores/{score_id}/bars", {"action": "key", "note": next(measures[3].iter("note")).get("id"), "fifths": -1})
    page.reload()
    page.wait_for_selector("#pages svg")
    page.click('#modeSwitch [data-mode="write"]')
    page.select_option("#viewMode", "horizontal")
    settle(page)
    page.evaluate("() => { $('desk').scrollLeft = 0; }")
    page.wait_for_timeout(100)
    check("at the start of the line its own names and clefs are there", page.evaluate("() => $('stickyStart').hidden"))
    page.evaluate("() => { $('desk').scrollLeft = $('desk').scrollWidth; }")
    page.wait_for_timeout(200)
    shown = page.evaluate("""() => {
        const host = $('stickyStart');
        const glyphs = selector => [...host.querySelectorAll(selector)].map(use => use.getAttribute('xlink:href').slice(1, 5));
        return { hidden: host.hidden, left: host.getBoundingClientRect().left - $('desk').getBoundingClientRect().left,
                 names: host.textContent.trim(), clefs: glyphs('g.clef use'), keys: glyphs('g.keySig use') };
    }""")
    check("scrolled along, the start of the line stays at the left", not shown["hidden"] and abs(shown["left"]) < 2 and "Violin" in shown["names"], shown)
    check("with the clefs in force there, the left hand's changed to treble", shown["clefs"] == ["E050", "E050", "E050"], shown["clefs"])
    check("and the key, one flat", shown["keys"].count("E260") == 3, shown["keys"])
    if shots:
        page.screenshot(path=str(shots / "one-line-start.png"))
    page.evaluate("() => { $('desk').scrollLeft = 0; }")
    page.select_option("#viewMode", "page")
    settle(page)
    check("and in pages it's not there", page.evaluate("() => $('stickyStart').hidden"))


def test_page_settings(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = written_melody(page, base, created, bars=24, tune="cdefgabc")
    width = lambda: page.evaluate("() => document.querySelector('#pages .page').getBoundingClientRect().width")
    a4 = width()
    step = lambda: page.evaluate("() => staffLines(document.querySelector('#pages g.staff')).step")
    first_step = step()
    menu(page, "Format", "Page settings…")
    check("page settings start from A4, portrait", (page.input_value("#pageSize"), page.is_checked('input[name="orientation"][value="portrait"]'), page.input_value("#pageWidth")) == ("A4", True, "210"))
    page.select_option("#pageSize", "Letter")
    check("a page size fills in its width and height", (page.input_value("#pageWidth"), page.input_value("#pageHeight")) == ("215.9", "279.4"))
    page.check('input[name="orientation"][value="landscape"]')
    check("landscape turns the page", (page.input_value("#pageWidth"), page.input_value("#pageHeight")) == ("279.4", "215.9"))
    page.fill("#staffSpace", "2")
    page.fill("#marginTop", "25")
    page.click("#pageDialog button[type=submit]")
    settle(page)
    check("the page is drawn at its new size", abs(width() / a4 - 279.4 / 210) < 0.01, (a4, width()))
    check("and its staves with the new staff space", abs(step() / first_step - 2 / 1.75) < 0.02, (first_step, step()))
    root = saved_score(base, score_id)
    check("the score keeps it as MusicXML does", (root.findtext("defaults/scaling/millimeters"), root.findtext("defaults/page-layout/page-width")) == ("8", "1397"))
    if shots:
        page.screenshot(path=str(shots / "page_settings.png"))
    tops = "() => [...document.querySelectorAll('#pages g.system')].slice(0, 2).map(system => staffLines(system.querySelector('g.staff')).top)"
    before = page.evaluate(tops)
    menu(page, "Format", "Spacing…")
    check("Style starts from the usual spacing", (page.input_value("#staffDistance"), page.input_value("#systemDistance")) == ("6", "6"))
    page.fill("#systemDistance", "14")
    page.click("#styleDialog button[type=submit]")
    settle(page)
    after = page.evaluate(tops)
    check("more room between systems spreads them", after[1] - after[0] > before[1] - before[0], (before, after))
    page.keyboard.press("Control+z")
    page.keyboard.press("Control+z")
    settle(page)
    check("both can be undone", abs(width() - a4) < 1 and page.evaluate("() => score.layout.width") == 210, width())
    menu(page, "Format", "Page settings…")
    page.fill("#marginLeft", "50")
    page.fill("#marginRight", "50")
    page.fill("#pageWidth", "120")
    page.click("#pageDialog button[type=submit]")
    settle(page)
    check("a page with no room for music is refused", "no room" in page.inner_text("#message"), page.inner_text("#message"))


def test_quick_entry(page, base: str, created: list[str]) -> None:
    score_id = written_melody(page, base, created, tune="cd")
    select_notes(page, 0)
    box = page.evaluate("""() => {
        const note = document.getElementById(selected);
        const head = headBox(note);
        return { x: head.left + head.width / 2, y: head.top + head.height / 2, step: staffLines(note.closest("g.staff")).step };
    }""")
    page.mouse.move(box["x"], box["y"])
    page.mouse.down()
    page.mouse.move(box["x"], box["y"] - box["step"] * 2, steps=4)
    page.mouse.up()
    settle(page)
    first = [note for note in saved_score(base, score_id).iter("note") if note.find("pitch") is not None][0]
    check("dragging a note moves it", first.findtext("pitch/step") == "E", first.findtext("pitch/step"))
    dragged_note = page.evaluate("() => selected")
    box = page.evaluate("""() => {
        const note = document.getElementById(selected);
        const head = headBox(note);
        return { x: head.left + head.width / 2, y: head.top + head.height / 2, step: staffLines(note.closest("g.staff")).step };
    }""")
    page.mouse.move(box["x"], box["y"])
    page.mouse.down()
    page.mouse.move(box["x"], box["y"] - box["step"] * 8, steps=6)
    page.mouse.up()
    settle(page)
    check("a long drag doesn't also count as a click on empty paper", page.evaluate("() => selected") == dragged_note, page.evaluate("() => selected"))
    page.keyboard.press("Control+z")
    settle(page)

    select_notes(page, 1)
    page.keyboard.press("r")
    settle(page)
    steps = [note.findtext("pitch/step") for note in saved_score(base, score_id).iter("note") if note.find("pitch") is not None]
    check("R repeats a note", steps == ["E", "D", "D"], steps)
    select_notes(page, 0)
    page.keyboard.press("#")
    settle(page)
    page.keyboard.press("j")
    settle(page)
    first = [note for note in saved_score(base, score_id).iter("note") if note.find("pitch") is not None][0]
    check("J swaps a note for its other name", (first.findtext("pitch/step"), first.findtext("pitch/alter")) == ("F", None), (first.findtext("pitch/step"), first.findtext("pitch/alter")))
    page.keyboard.press("x")
    settle(page)
    check("X flips a stem", [note for note in saved_score(base, score_id).iter("note") if note.find("stem") is not None] != [])

    select_notes(page, 1)
    page.keyboard.press("Shift+V")
    settle(page)
    page.keyboard.press("Control+c")
    settle(page)
    select_notes(page, 2)
    page.keyboard.press("Control+v")
    settle(page)
    check("paste keeps articulations", len(notes_with(saved_score(base, score_id), "notations/articulations/accent")) == 2)


def test_tuplets_and_verses(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = open_blank(page, base, created, bars=2)
    page.keyboard.press("5")
    page.keyboard.press("Control+5")
    settle(page)
    check("Ctrl+5 makes a quintuplet", page.locator("#pages g.tuplet").count() == 1)
    check("it asks for the length it uses", page.evaluate("() => editor.type") == "16th", page.evaluate("() => editor.type"))
    page.keyboard.type("cdefg", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    root = saved_score(base, score_id)
    in_tuplet = [note for note in root.iter("note") if note.find("time-modification") is not None and note.find("pitch") is not None]
    check("five notes fill the quintuplet", [note.findtext("pitch/step") for note in in_tuplet] == list("CDEFG"))
    check("each is one of five", {note.findtext("time-modification/actual-notes") for note in in_tuplet} == {"5"})
    check("the bar still adds up", page.is_hidden("#barChecks"))

    score_id = written_melody(page, base, created, tune="cd")
    select_notes(page, 0)
    page.keyboard.press("Control+l")
    settle(page)
    page.keyboard.type("one", delay=0)
    page.keyboard.press("ArrowDown")
    settle(page)
    page.keyboard.type("two", delay=0)
    page.keyboard.press("Enter")
    settle(page)
    first = next(note for note in saved_score(base, score_id).iter("note") if note.find("pitch") is not None)
    check("verses stack under one note", [(lyric.get("number"), lyric.findtext("text")) for lyric in first.findall("lyric")] == [("1", "one"), ("2", "two")], [lyric.findtext("text") for lyric in first.findall("lyric")])
    check("both verses are drawn", page.locator("#pages g.verse").count() == 2)
    page.keyboard.press("Escape")
    if shots:
        page.screenshot(path=str(shots / "tuplets_verses.png"))


HARMONICS = """async () => {
    const data = await (await fetch(`/api/scores/${scoreId}/playback`)).json();
    const rate = 44100;
    const audio = new OfflineAudioContext(1, rate, rate);
    schedule(audio, { ...data, notes: data.notes.slice(0, 1), chords: [] }, 0, 0);
    const samples = (await audio.startRendering()).getChannelData(0);
    const strength = frequency => {
        const step = 2 * Math.PI * frequency / rate;
        let real = 0, imaginary = 0;
        for (let index = 2000; index < 12000; index++) {
            real += samples[index] * Math.cos(index * step);
            imaginary += samples[index] * Math.sin(index * step);
        }
        return Math.hypot(real, imaginary);
    };
    const pitch = 440 * 2 ** ((data.notes[0].midi - 69) / 12);
    return strength(pitch * 2) / strength(pitch);
}"""


def test_instrument_sounds(page, base: str, created: list[str]) -> None:
    written_melody(page, base, created, staff="flute", tune="c")
    flute = page.evaluate(HARMONICS)
    written_melody(page, base, created, staff="trumpet", tune="c")
    trumpet = page.evaluate(HARMONICS)
    check("the blank form starts a score for an instrument", page.locator("#pages g.note").count() == 1)
    check("a flute sounds pure", flute < 0.02, flute)
    check("a trumpet sounds bright, so two parts can be told apart", trumpet > 0.3, trumpet)


def test_pickup(page, base: str, created: list[str]) -> None:
    score_id = open_blank(page, base, created, bars=2)
    page.select_option("#pickupChoice", "4")
    settle(page)
    check("the menu makes a pickup with nothing selected", saved_score(base, score_id).find("part").find("measure").get("implicit") == "yes")
    page.keyboard.press("5")
    page.keyboard.type("gcd", delay=0)
    settle(page)
    measures = saved_score(base, score_id).find("part").findall("measure")
    check("writing carries on from the pickup into bar one", [note.findtext("pitch/step") for measure in measures[:2] for note in measure.findall("note") if note.find("pitch") is not None] == list("GCD"))
    check("the first bar has only the pickup in it", len(measures[0].findall("note")) == 1, len(measures[0].findall("note")))
    check("the bar check accepts it", page.is_hidden("#barChecks"))
    page.keyboard.press("Escape")
    settle(page)


def test_audio_file(page, base: str, created: list[str]) -> None:
    written_melody(page, base, created, bars=2, tune="cdef")
    with page.expect_download() as download:
        menu(page, "File", "Export", "Audio (WAV)")
    check("the audio file has a sensible name", download.value.suggested_filename == "score.wav")
    with wave.open(str(download.value.path())) as sound:
        rate, channels, width, length = sound.getframerate(), sound.getnchannels(), sound.getsampwidth(), sound.getnframes()
        frames = sound.readframes(length)
    samples = array.array("h", frames)
    left, right = samples[0::2], samples[1::2]
    tempo = page.evaluate("async () => (await (await fetch(`/api/scores/${scoreId}/playback`)).json()).tempo")
    beat = 60 / tempo
    check("it's a plain stereo WAV another program can read", (rate, channels, width) == (44100, 2, 2), (rate, channels, width))
    check("it lasts as long as the score", abs(length / rate - (8 * beat + 0.5)) < 0.01, length / rate)
    check("the notes can be heard", max(abs(sample) for sample in left[: int(rate * beat * 3)]) > 3000)
    check("in the middle, as nothing is panned", left[: int(rate * beat * 3)] == right[: int(rate * beat * 3)])
    check("the empty bar is silent", max(abs(sample) for sample in samples[int(rate * beat * 4.5) * 2 :]) == 0)
    turned_down = page.evaluate("""async () => {
        const file = new DataView(await wavFile(Float32Array.of(0.5, 2, -1), 44100).arrayBuffer());
        return [file.getInt16(44, true), file.getInt16(46, true), file.getInt16(48, true)];
    }""")
    check("a sound too loud for the file is turned down, not wrapped round", turned_down == [8191, 32767, -16383], turned_down)
    paired = page.evaluate("""async () => {
        const file = new DataView(await wavFile([Float32Array.of(1, 0), Float32Array.of(0, -1)], 44100).arrayBuffer());
        return [file.getUint16(22, true), file.getUint32(28, true), file.getUint16(32, true), file.getInt16(44, true), file.getInt16(46, true), file.getInt16(48, true), file.getInt16(50, true)];
    }""")
    check("a stereo file keeps left and right side by side", paired == [2, 44100 * 4, 4, 32767, 0, 0, -32767], paired)


def mixer_strip(page, name: str):
    return page.locator(f'#mixerStrips .strip:has(.name:text-is("{name}"))')


def test_mixer(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = written_melody(page, base, created, bars=2, tune="cdef")
    score_pane(page)
    page.click("#partAdd")
    page.click('#partChooser li:text-is("Woodwinds")')
    page.dblclick('#partChooser [data-instrument="flute"]')
    page.click("#partOk")
    settle(page)
    page.keyboard.press("F10")
    check("F10 shows the mixer below the score, in the instrument's place", page.is_visible("#mixerPanel") and page.inner_text('#dockTabs [aria-selected="true"]') == "Mixer" and page.is_hidden("#pianoPanel"))
    names = page.locator("#mixerStrips .strip .name").all_inner_texts()
    check("it has a strip for each instrument, the chord symbols, the metronome and the master", names == ["Music", "Flute", "Chord symbols", "Metronome", "Master"], names)
    mixer_strip(page, "Music").locator(".fader input").fill("-12")
    mixer_strip(page, "Music").locator(".pan input").fill("-100")
    mixer_strip(page, "Music").locator(".solo").click()
    check("a fader shows its level", mixer_strip(page, "Music").locator(".fader output").inner_text() == "-12.0 dB")
    check("and a pan its side", mixer_strip(page, "Music").locator(".pan output").inner_text() == "L 100")
    page.wait_for_timeout(600)
    saved = json.loads(fetch_text(base, f"/api/scores/{score_id}"))["mixer"]
    parts = list(saved["parts"].values())
    check("the score keeps the mixer", parts[0]["volume"] == -12 and parts[0]["pan"] == -100 and parts[0]["solo"], saved)
    check("which isn't an edit to undo", json.loads(fetch_text(base, f"/api/scores/{score_id}"))["canUndo"] is True and page.evaluate("() => score.canUndo"))
    heard = page.evaluate("""async () => {
        const data = await fetchPlayback();
        const rate = 44100, audio = new OfflineAudioContext(2, rate * 3, rate);
        schedule(audio, data, 0, 0, 1, Infinity, { mixer: mixerForPlayback() });
        const sound = await audio.startRendering();
        const top = samples => samples.reduce((most, sample) => Math.max(most, Math.abs(sample)), 0);
        return [top(sound.getChannelData(0)), top(sound.getChannelData(1))];
    }""")
    check("panned hard left, only the left speaker sounds", heard[0] > 0.01 and heard[1] < 1e-6, heard)
    mixer_strip(page, "Flute").locator(".mute").click()
    quiet = page.evaluate("""async () => {
        const data = await fetchPlayback();
        data.notes = data.notes.map(note => ({ ...note, part: 1 }));
        const audio = new OfflineAudioContext(2, 44100 * 3, 44100);
        schedule(audio, data, 0, 0, 1, Infinity, { mixer: mixerForPlayback() });
        return (await audio.startRendering()).getChannelData(0).reduce((most, sample) => Math.max(most, Math.abs(sample)), 0);
    }""")
    check("a muted instrument is silent", quiet == 0, quiet)
    mixer_strip(page, "Music").locator(".solo").click()
    mixer_strip(page, "Music").locator(".pan input").fill("0")
    mixer_strip(page, "Flute").locator(".mute").click()
    mixer_strip(page, "Flute").locator(".pan input").fill("-100")
    page.wait_for_timeout(600)
    with urllib.request.urlopen(f"{base}/api/scores/{score_id}/score.musicxml") as response:
        exported = ET.fromstring(response.read())
    levels = [(entry.findtext("midi-instrument/volume"), entry.findtext("midi-instrument/pan")) for entry in exported.findall("part-list/score-part")]
    check("MusicXML gets each instrument's volume and pan", levels == [("39.3701", "0"), ("78.7402", "-90")], levels)
    if shots:
        page.screenshot(path=str(shots / "mixer.png"))
    page.keyboard.press("F10")
    check("F10 puts it away again", page.is_hidden("#dock"))
    menu(page, "View", "Mixer")
    check("as View ▸ Mixer shows it", page.is_visible("#mixerPanel"))
    page.click("#dockClose")
    check("and its close button closes it", page.is_hidden("#dock"))


def lit_keys(page, kind: str) -> list[int]:
    return page.evaluate("kind => [...document.querySelectorAll(`#pianoKeys .key.${kind}`)].map(key => Number(key.dataset.midi))", kind)


def test_piano_keyboard(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = written_melody(page, base, created, bars=2, tune="cdef")
    page.wait_for_function("() => playState.data !== null")
    page.keyboard.press("p")
    shown = page.is_visible("#pianoPanel")
    page.keyboard.press("p")
    check("P puts the piano keyboard below the score, or away", shown != page.is_visible("#pianoPanel"))
    show_instrument(page)
    check("a piano's part has the keyboard", page.is_visible("#pianoPanel") and page.inner_text('#dockTabs [aria-selected="true"]') == "Keyboard")
    check("with a piano's 88 keys", page.locator("#pianoKeys .key").count() == 88 and page.locator("#pianoKeys .white").count() == 52)
    select_notes(page, 0)
    check("the selected note's key is lit", lit_keys(page, "chosen") == [72], lit_keys(page, "chosen"))
    select_notes(page, 0, 2)
    check("and every key of a selected stretch", lit_keys(page, "chosen") == [72, 74, 76], lit_keys(page, "chosen"))
    page.keyboard.press("Escape")
    page.keyboard.press("n")
    settle(page)
    page.locator('#pianoKeys [data-midi="64"]').click()
    settle(page)
    page.keyboard.down("Shift")
    page.locator('#pianoKeys [data-midi="67"]').click()
    page.keyboard.up("Shift")
    settle(page)
    notes = [(note.findtext("pitch/step"), note.findtext("pitch/octave"), note.find("chord") is not None) for note in saved_score(base, score_id).iter("note") if note.find("pitch") is not None]
    check("in note input a key writes its note, and Shift adds it to the chord", notes[:2] == [("E", "4", False), ("G", "4", True)], notes[:3])
    page.keyboard.press("Escape")
    settle(page)
    page.keyboard.press("Escape")
    page.click("#rewind")
    page.click("#play")
    page.wait_for_selector("#pianoKeys .key.sounding", timeout=4000)
    check("keys light up as their notes play", len(lit_keys(page, "sounding")) >= 1, lit_keys(page, "sounding"))
    if shots:
        page.screenshot(path=str(shots / "piano.png"))
    page.click("#play")
    check("and go out when playback stops", lit_keys(page, "sounding") == [])
    before = page.evaluate("() => document.querySelector('#pianoKeys .white').getBoundingClientRect().width")
    box = page.locator("#pianoPanel").bounding_box()
    page.mouse.move(box["x"] + 200, box["y"] + 40)
    page.keyboard.down("Control")
    page.mouse.wheel(0, -100)
    page.keyboard.up("Control")
    after = page.evaluate("() => document.querySelector('#pianoKeys .white').getBoundingClientRect().width")
    check("Ctrl and the wheel make the keys wider", after > before, (before, after))
    page.mouse.move(box["x"] + 200, box["y"] + 40)
    page.keyboard.down("Control")
    page.mouse.wheel(0, 100)
    page.keyboard.up("Control")

    written_melody(page, base, created, staff="trumpet", tune="c")
    page.wait_for_function("() => playState.data && Object.keys(playState.data.events).length > 0")
    show_instrument(page)
    select_notes(page, 0)
    check("a trumpet's note lights its written key", lit_keys(page, "chosen") == [73], lit_keys(page, "chosen"))
    check("an instrument that isn't a piano, guitar or violin has the keyboard too", page.is_visible("#pianoPanel"))
    score_pane(page)
    page.click("#concertPitch")
    settle(page)
    page.wait_for_function("() => document.querySelector('#pianoKeys .key.chosen')?.dataset.midi === '71'")
    check("and at concert pitch the key that sounds", lit_keys(page, "chosen") == [71], lit_keys(page, "chosen"))
    page.keyboard.press("p")
    check("P puts the keyboard away", page.is_hidden("#dock"))


def timeline_cell(page, row: str, measure: int):
    return page.locator(f'#timelineGrid .row:has(.label:text-is("{row}")) .cell[data-measure="{measure}"]')


def test_timeline(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = written_melody(page, base, created, bars=8, tune="cdefgabcdefg")
    score_pane(page)
    page.click("#partAdd")
    page.click('#partChooser li:text-is("Woodwinds")')
    page.dblclick('#partChooser [data-instrument="flute"]')
    page.click("#partOk")
    settle(page)
    select_notes(page, 4)
    choose(page, "time:3/4")
    settle(page)
    select_notes(page, 0)
    page.keyboard.press("Control+m")
    settle(page)
    page.keyboard.press("F12")
    check("F12 opens the timeline below the score", page.is_visible("#timelinePanel"))
    labels = page.locator("#timelineGrid .label").all_inner_texts()
    check("it has MuseScore's rows, then one for each instrument", labels == ["", "Tempo", "Time signature", "Rehearsal mark", "Key signature", "Barlines", "Jumps and markers", "Music", "Flute"], labels)
    check("a column for each measure", page.locator('#timelineGrid .row:has(.label:text-is("Music")) .cell').count() == 8)
    check("the time signature row shows where it changes", (timeline_cell(page, "Time signature", 0).inner_text(), timeline_cell(page, "Time signature", 1).inner_text()) == ("4/4", "3/4"))
    check("and the rehearsal mark row its letter", timeline_cell(page, "Rehearsal mark", 0).inner_text() == "A")
    check("and the barlines row the end", timeline_cell(page, "Barlines", 7).inner_text() == "final")
    filled = page.evaluate("""() => [...document.querySelectorAll('#timelineGrid .row.part')].map(row => [...row.querySelectorAll('.cell')].map(cell => cell.classList.contains('filled')))""")
    check("an instrument's row shows the measures it plays in", filled[0] == [True] * 3 + [False] * 5 and not any(filled[1]), filled)
    timeline_cell(page, "Flute", 2).click()
    settle(page)
    chosen = page.evaluate("() => [...document.querySelectorAll('#timelineGrid .cell.chosen')].map(cell => [Number(cell.dataset.part), Number(cell.dataset.measure)])")
    check("a click selects that measure of that instrument", chosen == [[1, 2]], chosen)
    lower = page.evaluate("() => document.querySelectorAll('#pages g.measure')[2].querySelectorAll(':scope > g.staff')[1].querySelector('g.mRest, g.rest').id")
    check("on the page too", page.evaluate("() => range?.ids ?? [selected]") == [lower], page.evaluate("() => range?.ids ?? [selected]"))
    page.keyboard.down("Shift")
    timeline_cell(page, "Flute", 4).click()
    page.keyboard.up("Shift")
    settle(page)
    chosen = page.evaluate("() => [...document.querySelectorAll('#timelineGrid .cell.chosen')].map(cell => Number(cell.dataset.measure))")
    check("Shift+click stretches the selection to the measure", chosen == [2, 3, 4], chosen)
    if shots:
        page.screenshot(path=str(shots / "timeline.png"))
    page.keyboard.press("Escape")
    settle(page)
    menu(page, "View", "Timeline")
    check("View ▸ Timeline puts it away", page.is_hidden("#dock"))


def test_midi_keyboard(context, base: str, created: list[str]) -> None:
    page = context.new_page()
    page.add_init_script("""
        window.fakeMidi = { inputs: new Map([["key", { type: "input" }]]), onstatechange: null };
        window.midiAsked = 0;
        navigator.requestMIDIAccess = async () => { window.midiAsked++; return window.fakeMidi; };
        window.press = number => window.fakeMidi.inputs.get("key").onmidimessage({ data: [0x90, number, 90] });
    """)
    page.goto(base)
    check("just opening the app doesn't ask for a MIDI keyboard", page.evaluate("() => window.midiAsked") == 0)
    score_id = open_blank(page, base, created, bars=2)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    page.keyboard.press("n")
    settle(page)
    check("writing asks once, however often it's started", page.evaluate("() => window.midiAsked") == 1, page.evaluate("() => window.midiAsked"))
    page.keyboard.press("5")
    page.evaluate("() => { press(61); }")
    settle(page)
    page.wait_for_timeout(120)
    page.evaluate("() => { press(67); }")
    settle(page)
    page.wait_for_timeout(120)
    page.evaluate("() => { press(60); press(64); press(67); }")
    settle(page)
    notes = [note for note in saved_score(base, score_id).iter("note") if note.find("pitch") is not None]
    steps = [(note.findtext("pitch/step"), note.findtext("pitch/alter"), note.findtext("pitch/octave")) for note in notes]
    check("MIDI keys write exact notes", steps[:2] == [("C", "1", "4"), ("G", None, "4")], steps)
    check("keys pressed together make a chord", len(notes) == 5 and sum(note.find("chord") is not None for note in notes) == 2, steps)
    page.close()


def test_opening_a_file(page, base: str, shots: Path | None, created: list[str]) -> None:
    source = written_melody(page, base, created, tune="cdef")
    select_notes(page, 0)
    choose(page, "dynamic:mf")
    settle(page)
    text = fetch_text(base, f"/api/scores/{source}/score.musicxml")
    page.goto(base)
    page.set_input_files("#file", files=[{"name": "tune.musicxml", "mimeType": "application/xml", "buffer": text.encode()}])
    page.wait_for_selector("#pages svg")
    created.append(page.evaluate("() => scoreId"))
    check("a MusicXML file opens in the editor", page.locator("#pages g.note").count() == 4)
    check("it has no photo panel", page.is_hidden("#original"))
    check("its marks come with it", page.locator("#pages g.dynam").count() == 1)
    page.goto(base)
    page.set_input_files("#file", files=[{"name": "broken.musicxml", "mimeType": "application/xml", "buffer": b"<nonsense"}])
    page.wait_for_selector("#message:not([hidden])")
    check("a broken file says so", "MusicXML" in page.inner_text("#message"), page.inner_text("#message"))


def test_sound_of_marks(page, base: str, created: list[str]) -> None:
    written_melody(page, base, created, tune="cdefcdef")
    select_notes(page, 0)
    menu(page, "Add", "Text", "Tempo marking")
    page.fill("#chordInput", "60")
    page.keyboard.press("Enter")
    settle(page)
    select_notes(page, 0)
    choose(page, "dynamic:pp")
    settle(page)
    select_notes(page, 4)
    choose(page, "dynamic:ff")
    settle(page)
    select_notes(page, 2)
    menu(page, "Add", "Text", "Tempo marking")
    page.fill("#chordInput", "120")
    page.keyboard.press("Enter")
    settle(page)
    select_notes(page, 6)
    page.keyboard.press("Shift+S")
    settle(page)
    heard = page.evaluate("""async () => {
        const data = await (await fetch(`/api/scores/${scoreId}/playback`)).json();
        const rate = 44100;
        const audio = new OfflineAudioContext(1, rate * 8, rate);
        schedule(audio, data, 0, 0);
        const samples = (await audio.startRendering()).getChannelData(0);
        const loudness = (from, to) => {
            let top = 0;
            for (let index = Math.floor(from * rate); index < to * rate; index++) top = Math.max(top, Math.abs(samples[index]));
            return top;
        };
        const fast = new OfflineAudioContext(1, rate * 8, rate);
        const { at } = schedule(fast, data, 0, 0, 2);
        return {
            starts: data.notes.slice(0, 5).map(note => note.start),
            soft: loudness(0.1, 0.4),
            loud: loudness(3.1, 3.3),
            sixth: loudness(4.02, 4.2),
            gap: loudness(4.3, 4.45),
            total: at(data.total),
        };
    }""")
    check("a soft passage is quieter than a loud one", heard["soft"] * 5 < heard["loud"], heard)
    check("a tempo change makes the later notes faster", heard["loud"] > 0.05, heard)
    check("a staccato note stops early", heard["sixth"] > 0.05 and heard["gap"] < heard["sixth"] / 10, heard)
    # Two beats at 60 and fourteen at 120 take nine seconds, or half that at double speed.
    check("the speed control halves the time", abs(heard["total"] - 4.5) < 0.01, heard["total"])


def test_sound(page, base: str, created: list[str]) -> None:
    score_id = open_blank(page, base, created, bars=3)
    page.keyboard.press("5")
    page.keyboard.type("cd", delay=0)
    page.keyboard.press("6")
    page.keyboard.press("e")
    settle(page)
    page.keyboard.press("5")
    page.keyboard.type("ggg", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    click_on(page, page.locator("#pages g.note").nth(3))
    page.keyboard.press("ArrowRight")
    page.keyboard.press("t")
    settle(page)
    bar_two = page.evaluate("""() => document.querySelectorAll("#pages g.measure")[1].querySelector("g.note").id""")
    click_on(page, page.locator(f"[id='{bar_two}']"))
    choose(page, "repeat:backward")
    settle(page)
    first = page.evaluate(HEAR)
    heard = first["heard"]
    check("each note sounds at its written pitch, on its own beat", heard[:6] == [72, 72, 74, 74, 76, 76], heard[:8])
    check("a half note keeps sounding", heard[6:8] == [76, 76], heard[:8])
    check("the next bar starts on time", heard[8:14] == [79] * 6, heard[8:16])
    check("a rest is silent", heard[14:16] == [None, None], heard[14:16])
    check("the repeat plays the start again", heard[16:24] == heard[:8], heard[16:24])
    check("the last bar is silent, as written", heard[32:] == [None] * 8, heard[32:])
    check("the audio is as long as the score", abs(first["seconds"] - (20 * 60 / first["tempo"] + 0.3)) < 0.1, first["seconds"])
    check("a repeated note starts again", first["plainJoin"] < 0.8, first["plainJoin"])
    check("a tied note keeps sounding", first["tiedJoin"] > 0.9, first["tiedJoin"])
    click_on(page, page.locator("#pages g.note").nth(2))
    page.click("#play")
    page.wait_for_function("() => document.querySelector('#pages .playing')")
    check("the playing note lights up", page.locator("#pages .playing").count() >= 1)
    check("playback starts from the selected note", page.get_attribute("#play", "data-notes") == "8", page.get_attribute("#play", "data-notes"))
    page.click("#play")
    check("playback stops", page.locator("#pages .playing").count() == 0)


def wait_playing(page, playing: bool) -> None:
    page.wait_for_function("wanted => document.querySelector('#play').getAttribute('aria-pressed') === wanted", arg=str(playing).lower())


def test_playback_toolbar(page, base: str, shots: Path | None, created: list[str]) -> None:
    written_melody(page, base, created, bars=4, tune="cdefgabcdefg")
    page.wait_for_function("() => playState.data !== null")
    check("the toolbar has rewind, play, loop, metronome and settings", all(page.is_visible(f"#{name}") for name in ("rewind", "play", "loop", "metronome", "playSettings")))
    check("it shows the tempo", page.inner_text("#tempoValue") == "100", page.inner_text("#tempoValue"))
    check("and the position at the start", (page.input_value("#timeCounter"), page.input_value("#beatCounter")) == ("00:00:00.0", "1.1"))
    page.fill("#beatCounter", "3.1")
    page.keyboard.press("Enter")
    check("a measure typed in moves the position there", page.input_value("#timeCounter") == "00:00:04.8", page.input_value("#timeCounter"))
    page.keyboard.press(" ")
    wait_playing(page, True)
    check("with nothing selected, Play starts from that position", page.get_attribute("#play", "data-notes") == "4", page.get_attribute("#play", "data-notes"))
    page.wait_for_timeout(400)
    page.keyboard.press(" ")
    wait_playing(page, False)
    measure = page.input_value("#beatCounter")
    check("stopping keeps the place it got to", measure.startswith("3."), measure)
    page.keyboard.press(" ")
    wait_playing(page, True)
    check("and Play carries on from there", page.get_attribute("#play", "data-notes") in ("3", "4"), page.get_attribute("#play", "data-notes"))
    page.keyboard.press(" ")
    wait_playing(page, False)
    page.click("#rewind")
    check("rewind goes back to the start", (page.input_value("#timeCounter"), page.input_value("#beatCounter")) == ("00:00:00.0", "1.1"))
    page.fill("#timeCounter", "00:00:01.2")
    page.keyboard.press("Enter")
    check("a time typed in moves the position there", page.input_value("#beatCounter") == "1.3", page.input_value("#beatCounter"))

    select_notes(page, 1, 2)
    page.click("#loop")
    check("the loop button turns on", page.get_attribute("#loop", "aria-pressed") == "true")
    check("flags mark where the loop starts and ends", page.locator(".loopMarker.in").count() == 1 and page.locator(".loopMarker.out").count() == 1)
    page.click("#play")
    wait_playing(page, True)
    check("the loop plays only the selection", page.get_attribute("#play", "data-notes") == "2", page.get_attribute("#play", "data-notes"))
    page.wait_for_timeout(1700)  # Two quarter notes at 100 a minute take 1.2 seconds.
    place = page.evaluate("() => player && playingPlace()")
    check("and goes round again", place is not None and 1 <= place <= 3, place)
    if shots:
        page.screenshot(path=str(shots / "playback.png"))
    page.click("#play")
    wait_playing(page, False)
    page.click("#loop")
    check("the loop can be turned off", page.get_attribute("#loop", "aria-pressed") == "false" and page.locator(".loopMarker").count() == 0)

    page.click("#metronome")
    check("the metronome turns on", page.get_attribute("#metronome", "aria-pressed") == "true")
    beats = page.evaluate("""async () => {
        const data = await fetchPlayback();
        const { beats } = schedule(new OfflineAudioContext(1, 44100, 44100), data, 0, 0, 1, Infinity, { metronome: playState.metronome });
        return beats.map(beat => [beat.place, beat.strong]);
    }""")
    check("it ticks every beat, higher on each bar's first", beats == [[place, place % 4 == 0] for place in range(16)], beats[:6])
    with page.expect_download() as download:
        menu(page, "File", "Export", "Audio (WAV)")
    with wave.open(str(download.value.path())) as sound:
        rate = sound.getframerate()
        samples = array.array("h", sound.readframes(sound.getnframes()))
    check("exported audio has the ticks in it while it's on", max(abs(sample) for sample in samples[int(rate * 0.6 * 12.5) :]) > 1000)
    page.click("#metronome")

    page.fill("#beatCounter", "1.3")
    page.keyboard.press("Enter")
    page.click("#tempoButton")
    page.fill("#speedValue", "200")
    page.keyboard.press("Enter")
    check("the speed doubles the tempo shown", page.inner_text("#tempoValue") == "200", page.inner_text("#tempoValue"))
    check("and halves the time to a place", page.input_value("#timeCounter") == "00:00:00.6", page.input_value("#timeCounter"))
    page.click("#speedReset")
    check("reset goes back to the written tempo", page.inner_text("#tempoValue") == "100")
    page.keyboard.press("Escape")
    page.mouse.click(5, 5)
    settle(page)


def shown_pages(page) -> list[int]:
    """The pages a reader has in view, counted from 0."""
    return page.evaluate("() => pagesInView().shown")


def test_modes(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = open_blank(page, base, created, bars=40)
    page.keyboard.press("5")
    page.keyboard.type("cdefgabc", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    system_breaks(page, 1)
    settle(page)
    pages = page.locator("#pages .page").count()
    check("a long score has several pages", pages >= 3, pages)
    page.evaluate("() => { window.drawnBefore = [...document.querySelectorAll('#pages .page')]; }")
    select_notes(page, 0)
    press(page, "ArrowUp")
    kept = page.evaluate("() => [...document.querySelectorAll('#pages .page')].filter(element => window.drawnBefore.includes(element)).length")
    check("an edit draws again only the page it changed", kept == pages - 1, (kept, pages))
    press(page, "Control+z")
    press(page, "Escape")
    check("a score opens in Write, with note input and the inspector", page.get_attribute('#modeSwitch [data-mode="write"]', "aria-checked") == "true" and page.is_visible("#noteBar") and page.is_visible("#inspector"))
    show_instrument(page)
    page.click('#modeSwitch [data-mode="read"]')
    check("Read puts away the note input, the inspector, the library and the instrument", all(page.is_hidden(name) for name in ("#noteBar", "#inspector", "#library", "#dock", "#practiceBar")))
    check("and fits a whole page in the window", page.input_value("#zoomChoice") == "page", page.input_value("#zoomChoice"))
    check("it says which page is in view", page.inner_text("#pageCount").startswith("Page") and f"of {pages}" in page.inner_text("#pageCount"), page.inner_text("#pageCount"))
    start = shown_pages(page)
    page.keyboard.press(" ")
    after_space = shown_pages(page)
    check("Space turns the page", after_space[0] > start[0], (start, after_space))
    page.keyboard.press("Shift+ ")
    check("and Shift+Space turns it back", shown_pages(page) == start, shown_pages(page))
    page.keyboard.press("PageDown")
    check("Page Down turns it too, as a page-turning pedal does", shown_pages(page) == after_space, shown_pages(page))
    page.keyboard.press("ArrowLeft")
    check("and the arrows", shown_pages(page) == start, shown_pages(page))
    desk = page.locator("#desk").bounding_box()
    page.mouse.click(desk["x"] + desk["width"] * 0.8, desk["y"] + desk["height"] / 2)
    check("a click on the right of the score turns forward", shown_pages(page) == after_space, shown_pages(page))
    page.mouse.click(desk["x"] + desk["width"] * 0.2, desk["y"] + desk["height"] / 2)
    check("and on the left back", shown_pages(page) == start, shown_pages(page))
    page.keyboard.press("End")
    check("End goes to the last page", shown_pages(page)[-1] == pages - 1, shown_pages(page))
    page.keyboard.press("Home")
    check("and Home to the first", shown_pages(page)[0] == 0, shown_pages(page))
    before = read_notes(base, score_id)
    for key in ("c", "Delete", "5", "Control+k"):
        page.keyboard.press(key)
    settle(page)
    check("Read changes nothing, whatever is pressed", read_notes(base, score_id) == before and page.is_hidden("#editor"))
    if shots:
        page.screenshot(path=str(shots / "read.png"))
    page.evaluate("() => followNote(document.querySelectorAll('#pages .page')[2].querySelector('g.note, g.rest, g.mRest'))")
    check("playback following a note turns the page to it", 2 in shown_pages(page), shown_pages(page))
    page.reload()
    page.wait_for_selector("#pages svg")
    check("the mode is kept for next time", page.get_attribute('#modeSwitch [data-mode="read"]', "aria-checked") == "true")
    page.keyboard.press("Alt+p")
    check("Alt+P goes to Practice", page.get_attribute("body", "data-mode") == "practice" and page.is_visible("#practiceBar") and page.is_hidden("#noteBar") and page.is_hidden("#inspector"))
    page.keyboard.press("Alt+r")
    check("Alt+R to Read", page.get_attribute("body", "data-mode") == "read")
    page.keyboard.press("n")
    settle(page)
    check("N in Read starts writing", page.get_attribute("body", "data-mode") == "write" and page.get_attribute("#inputToggle", "aria-pressed") == "true")
    page.keyboard.press("Escape")
    page.keyboard.press("Alt+w")
    check("and Alt+W keeps to Write", page.get_attribute("body", "data-mode") == "write" and page.input_value("#zoomChoice") != "page")


def test_library(page, base: str, created: list[str]) -> None:
    first = written_melody(page, base, created, tune="cdef")
    menu(page, "File", "Title and composer…")
    page.fill("#titleInput", "Library Song One")
    page.click("#projectDialog button[type=submit]")
    settle(page)
    second = written_melody(page, base, created, tune="gfed")
    page.wait_for_selector(f'#historyList a[data-score="{first}"]')
    check("the library is beside the open score", page.is_visible("#library") and page.is_visible("#pages"))
    check("and lists the scores, the open one marked", page.get_attribute(f'#historyList a[data-score="{second}"]', "aria-current") == "true" and page.get_attribute(f'#historyList a[data-score="{first}"]', "aria-current") is None)
    page.fill("#homeSearch", "library song")
    shown = page.locator("#historyList a").count()
    check("its search finds a score by its title", shown >= 1 and page.locator(f'#historyList a[data-score="{first}"]').count() == 1 and page.locator(f'#historyList a[data-score="{second}"]').count() == 0, shown)
    page.click(f'#historyList a[data-score="{first}"]')
    page.wait_for_function("id => scoreId === id && score !== null", arg=first)
    check("a click opens it in place of the other", page.inner_text("#scoreName") == "Library Song One")
    page.fill("#homeSearch", "")
    page.click("#libraryClose")
    check("the library folds away", page.is_hidden("#library") and page.get_attribute("#libraryToggle", "aria-pressed") == "false")
    page.keyboard.press("F6")
    check("and F6 brings it back", page.is_visible("#library"))
    menu(page, "File", "Close")
    check("with nothing open the library is the whole window", page.is_visible("#library") and page.is_hidden("#workspace") and page.is_hidden("#inspector"))
    check("and shows each score as a page", page.locator(f'#historyList a[data-score="{first}"] img').count() == 1)


def test_inspector(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = open_blank(page, base, created, bars=4, staff="piano")
    page.keyboard.press("5")
    page.keyboard.type("cdef", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    check("leaving note input selects the last note written, which the inspector shows", page.is_visible("#propNote") and page.inner_text("#propKind") == "F5", page.inner_text("#propKind"))
    page.keyboard.press("Escape")
    settle(page)
    check("with nothing selected the inspector shows the score", page.is_visible("#propScore") and page.is_hidden("#propNote") and page.get_attribute('#inspectorTabs [data-pane="score"]', "aria-selected") == "true")
    page.fill("#scoreTitle", "Inspected")
    page.keyboard.press("Enter")
    settle(page)
    check("its title can be typed there", json.loads(fetch_text(base, f"/api/scores/{score_id}"))["title"] == "Inspected" and page.inner_text("#scoreName") == "Inspected")
    check("it lists the instruments by kind", page.locator("#partList li").all_inner_texts() == ["Piano"] and page.locator("#partList li .kind svg").count() == 1)
    select_notes(page, 0)
    check("a selected note is named as it's drawn, with its length, voice and hand", (page.inner_text("#propKind"), page.inner_text("#propWhat")) == ("C5", "Quarter · voice 1 · Piano, right hand"), (page.inner_text("#propKind"), page.inner_text("#propWhat")))
    check("its length shows in the inspector", page.get_attribute('#lengthRow [data-length="quarter"]', "aria-pressed") == "true")
    page.click('#accidentalRow [aria-label="Sharp"]')
    settle(page)
    check("an accidental from the inspector changes its name", page.inner_text("#propKind") == "C♯5", page.inner_text("#propKind"))
    page.click('#lengthRow [data-length="half"]')
    settle(page)
    check("and a length its length", read_notes(base, score_id)[0][3] == "half" and page.inner_text("#propWhat").startswith("Half"), page.inner_text("#propWhat"))
    check("the measure's facts are beside it", page.inner_text("#propMeasureName") == "Measure 1" and "4/4" in page.inner_text('#measureGroups .group[data-group="times"] .current'))
    choose(page, "key:2")
    settle(page)
    check("and a key chosen there is shown there", "D major" in page.inner_text('#measureGroups .group[data-group="keys"] .current'), page.inner_text('#measureGroups .group[data-group="keys"] .current'))
    head = page.locator('#noteGroups .group[data-group="dynamics"] .groupHead')
    was = head.get_attribute("aria-expanded")
    head.click()
    check("a group of marks opens and closes", head.get_attribute("aria-expanded") != was)
    if head.get_attribute("aria-expanded") == "false":
        head.click()
    check("a pianist's note has the pedal beside it", page.is_visible('#propInstrument [data-instrument-cell="line:pedal-pedstar"]') and "pianist" in page.inner_text("#propInstrumentName"))
    page.click('#propInstrument [data-instrument-cell="line:pedal-pedstar"]')
    settle(page)
    check("which puts a pedal down", any(mark["kind"] == "pedal" for mark in json.loads(fetch_text(base, f"/api/scores/{score_id}"))["marks"]))
    lower = page.evaluate("""() => document.querySelector("#pages g.measure").querySelectorAll("g.staff")[1].querySelector("g.mRest, g.rest").id""")
    click_on(page, page.locator(f"[id='{lower}']"))
    settle(page)
    check("a rest on the lower staff is the left hand's", page.inner_text("#propKind") == "Rest" and page.inner_text("#propWhat").endswith("Piano, left hand"), page.inner_text("#propWhat"))
    page.click('#inspectorTabs [data-pane="score"]')
    check("the score is a tab away while a note is selected", page.is_visible("#propScore") and page.evaluate("() => selected") == lower)
    page.click('#inspectorTabs [data-pane="selection"]')
    if shots:
        page.screenshot(path=str(shots / "inspector.png"))
    page.keyboard.press("F7")
    check("F7 shows the score", page.is_visible("#propScore"))
    page.keyboard.press("F8")
    check("F8 goes back to the selection", page.is_visible("#propNote"))
    page.keyboard.press("F8")
    check("and again puts the inspector away", page.is_hidden("#inspector"))
    page.keyboard.press("F8")
    check("and back", page.is_visible("#propNote"))
    page.keyboard.press("Escape")


def test_command_palette(page, base: str, created: list[str]) -> None:
    score_id = written_melody(page, base, created, tune="cdef")
    page.keyboard.press("Escape")
    settle(page)
    page.keyboard.press("Control+Shift+P")
    check("Ctrl+Shift+P opens the command palette", page.is_visible("#commandDialog") and page.evaluate("() => document.activeElement.id") == "commandInput")
    count = page.evaluate("() => commands.list.length")
    leaves = page.evaluate("() => flatten(menuList(), [], []).length")
    check("it has every menu item, and every cell, note action and mode", count > leaves + 150, (count, leaves))
    page.fill("#commandInput", "staccato")
    first = page.locator("#commandList li").first
    check("typing finds a command, with its key", first.locator(".what").inner_text() == "Staccato" and first.locator("kbd").count() >= 2, first.inner_text())
    check("and it can't run with nothing to put it on", first.get_attribute("aria-disabled") == "true")
    page.keyboard.press("Enter")
    check("so Enter leaves it open", page.is_visible("#commandDialog"))
    page.keyboard.press("Escape")
    check("Esc closes it", page.is_hidden("#commandDialog"))
    select_notes(page, 1)
    command(page, "staccato")
    settle(page)
    check("a found command runs on the selection", len(notes_with(saved_score(base, score_id), "notations/articulations/staccato")) == 1)
    page.keyboard.press("/")
    settle(page)
    check("/ on a selected note still adds a grace note", any(note.find("grace") is not None for note in saved_score(base, score_id).iter("note")) and page.is_hidden("#commandDialog"))
    page.keyboard.press("Escape")
    settle(page)
    page.keyboard.press("Escape")
    page.keyboard.press("/")
    check("with nothing chosen / opens the palette", page.is_visible("#commandDialog"))
    page.fill("#commandInput", "prac mode")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowUp")
    page.keyboard.press("Enter")
    check("words typed in part find a mode, which runs", page.get_attribute("body", "data-mode") == "practice")
    command(page, "write mode")
    command(page, "key d major")
    settle(page)
    check("and a key from the start of the score", saved_score(base, score_id).find(".//fifths").text == "2")
    page.click("#commandOpen")
    page.fill("#commandInput", "zzqx")
    check("the button opens it too, and nothing matching says so", page.inner_text("#commandList") == "Nothing goes by that name.")
    page.keyboard.press("Escape")


def neck_spots(page, holder: str, kind: str) -> list[list[int]]:
    return page.evaluate("([holder, kind]) => [...document.querySelectorAll(`#${holder} .spot.${kind}`)].map(spot => [Number(spot.dataset.string), Number(spot.dataset.stop)])", [holder, kind])


def test_fretboard(page, base: str, shots: Path | None, created: list[str]) -> None:
    page.goto(base)
    new_score(page, "guitar", bars=2)
    score_id = page.evaluate("() => scoreId")
    created.append(score_id)
    page.wait_for_function("() => playState.data !== null")
    show_instrument(page)
    check("a guitar's part has a fretboard", page.is_visible("#fretboardPanel") and page.inner_text('#dockTabs [aria-selected="true"]') == "Fretboard" and page.inner_text("#partName") == "Guitar")
    check("six strings in standard tuning, open to the 19th fret", page.locator("#fretboard .stringName").all_inner_texts() == list("EBGDAE") and page.locator("#fretboard .spot").count() == 6 * 20)
    page.keyboard.press("5")
    page.locator('#fretboard .spot[data-string="6"][data-stop="3"]').click()
    settle(page)
    page.keyboard.down("Shift")
    page.locator('#fretboard .spot[data-string="4"][data-stop="0"]').click()
    page.keyboard.up("Shift")
    settle(page)
    page.locator('#fretboard .spot[data-string="1"][data-stop="12"]').click()
    settle(page)
    notes = [(note.findtext("pitch/step"), note.findtext("pitch/octave"), note.find("chord") is not None) for note in saved_score(base, score_id).iter("note") if note.find("pitch") is not None]
    check("a fret writes the note it sounds, and Shift adds a string to the chord", notes == [("G", "2", False), ("D", "3", True), ("E", "5", False)], notes)
    page.keyboard.press("Escape")
    settle(page)
    page.wait_for_function("() => Object.values(playState.data?.events ?? {}).some(event => event[3] === 43)")
    select_notes(page, 0)
    check("a selected note lights where it's played", neck_spots(page, "fretboard", "chosen") in ([[6, 3]], [[6, 3], [4, 0]]), neck_spots(page, "fretboard", "chosen"))
    select_notes(page, 2)
    check("in the lowest place it fits", neck_spots(page, "fretboard", "chosen") == [[1, 12]], neck_spots(page, "fretboard", "chosen"))
    page.keyboard.press("Escape")
    page.click("#rewind")
    page.click("#play")
    page.wait_for_selector("#fretboard .spot.sounding", timeout=4000)
    check("frets light as their notes sound", len(neck_spots(page, "fretboard", "sounding")) >= 1)
    if shots:
        page.screenshot(path=str(shots / "fretboard.png"))
    page.click("#play")
    check("and go out when it stops", neck_spots(page, "fretboard", "sounding") == [])


def test_fingerboard_and_switcher(page, base: str, shots: Path | None, created: list[str]) -> None:
    page.goto(base)
    page.click("#blankOpen")
    page.click('#quickStart [data-quick="piano"] .add')
    page.click('#quickStart [data-quick="violin"] .add')
    page.click("#blankNext")
    page.fill("#blankBars", "2")
    page.click("#blankDone")
    page.wait_for_selector("#pages svg")
    score_id = page.evaluate("() => scoreId")
    created.append(score_id)
    page.keyboard.press("Escape")
    settle(page)
    show_instrument(page)
    check("the switcher shows the first instrument", page.inner_text("#partName") == "Piano" and page.is_visible("#pianoPanel"))
    page.click("#partSwitch")
    offered = page.locator(".menu button span:first-child").all_inner_texts()
    check("and offers each one by its kind", offered[:2] == ["Piano · keyboard", "Violin · fingerboard"], offered)
    page.locator('.menu button:has(span:text-is("Violin · fingerboard"))').click()
    check("choosing the violin brings its fingerboard", page.is_visible("#fingerboardPanel") and page.is_hidden("#pianoPanel") and page.inner_text("#partName") == "Violin")
    check("four strings tuned in fifths, with the positions marked", page.locator("#fingerboard .stringName").all_inner_texts() == list("EADG") and page.locator("#fingerboard .number").all_inner_texts() == ["1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th", "11th", "12th"])
    violin = page.evaluate("() => document.querySelector('#pages g.measure').querySelectorAll(':scope > g.staff')[2].querySelector('g.mRest, g.rest').id")
    click_on(page, page.locator(f"[id='{violin}']"))
    settle(page)
    page.keyboard.press("n")
    settle(page)
    page.keyboard.press("6")
    page.locator('#fingerboard .spot[data-string="2"][data-stop="2"]').click()
    settle(page)
    violin_notes = [(note.findtext("pitch/step"), note.findtext("pitch/octave")) for note in saved_score(base, score_id).findall("part")[1].iter("note") if note.find("pitch") is not None]
    check("a place on a string writes that note in the violin's part", violin_notes == [("B", "4")], violin_notes)
    page.keyboard.press("Escape")
    settle(page)
    page.wait_for_function("() => Object.values(playState.data?.events ?? {}).some(event => event[3] === 71)")
    select_notes(page, 0)
    spots = page.evaluate("() => [...document.querySelectorAll('#fingerboard .spot.chosen')].map(spot => [spot.dataset.string, spot.dataset.stop, spot.dataset.show])")
    check("a selected note lights its place, with its first-position finger", spots == [["2", "2", "1"]], spots)
    check("and has the violinist's bowing beside it in the inspector", "violinist" in page.inner_text("#propInstrumentName") and page.is_visible('#propInstrument [data-instrument-cell="mark:downbow"]') and page.is_visible('#propInstrument [data-instrument-cell="mark:upbow"]'))
    page.click('#propInstrument [data-instrument-cell="mark:upbow"]')
    settle(page)
    check("which marks the note", len(notes_with(saved_score(base, score_id), "notations/technical/up-bow")) == 1)
    if shots:
        page.screenshot(path=str(shots / "fingerboard.png"))
    piano = page.evaluate("() => document.querySelector('#pages g.measure').querySelector('g.staff g.mRest, g.staff g.rest').id")
    click_on(page, page.locator(f"[id='{piano}']"))
    settle(page)
    check("selecting the piano's music switches back to its keyboard", page.inner_text("#partName") == "Piano" and page.is_visible("#pianoPanel"))
    page.keyboard.press("Escape")


def test_cello_and_double_bass_fingerboards(page, base: str, shots: Path | None, created: list[str]) -> None:
    """A cello and a double bass get fingerboards of their own, with their strings, positions and fingers."""
    for instrument, strings, stop, finger, player, positions in [
        ("cello", list("ADGC"), 4, "3", "cellist", {"3": "2nd", "5": "3rd", "7": "4th", "8": "5th"}),
        ("double-bass", list("GDAE"), 4, "4", "double bassist", {"3": "2nd", "5": "3rd", "7": "4th", "8": "5th"}),
    ]:
        score_id = open_blank(page, base, created, bars=1, staff=instrument)
        show_instrument(page)
        check(f"a {instrument.replace('-', ' ')} has a fingerboard of its strings", page.is_visible("#fingerboardPanel") and page.locator("#fingerboard .stringName").all_inner_texts() == strings, page.locator("#fingerboard .stringName").all_inner_texts())
        marked = page.evaluate("() => Object.fromEntries([...document.querySelectorAll('#fingerboard .number')].map(number => [getComputedStyle(number).gridColumnStart - 2, number.textContent]))")
        check("with its own positions marked", all(marked.get(at) == name for at, name in positions.items()), marked)
        page.locator(f'#fingerboard .spot[data-string="1"][data-stop="{stop}"]').click()
        settle(page)
        page.keyboard.press("Escape")
        settle(page)
        note = own_notes(saved_score(base, score_id))[0].get("id")
        page.wait_for_function("id => playState.data?.events?.[id]", arg=note)
        click_on(page, page.locator(f"[id='{note}']"))
        settle(page)
        lit = page.evaluate("() => [...document.querySelectorAll('#fingerboard .spot.chosen')].map(spot => [spot.dataset.string, spot.dataset.stop, spot.dataset.show])")
        check(f"its note is lit where it was played, with the {player}'s finger", lit == [["1", str(stop), finger]], lit)
        check(f"and the inspector is for the {player}", page.inner_text("#propInstrumentName").strip() == f"For the {player}", page.inner_text("#propInstrumentName"))
        if shots and instrument == "cello":
            page.screenshot(path=str(shots / "cello-fingerboard.png"))
        page.keyboard.press("Escape")


def score_data(base: str, score_id: str) -> dict:
    return json.loads(fetch_text(base, f"/api/scores/{score_id}"))


def test_new_scores_for_players(page, base: str, shots: Path | None, created: list[str]) -> None:
    """Guitarists and bassists get tablature from New score, one click away, and from the instrument choosers."""
    page.goto(base)
    page.click("#blankOpen")
    offered = page.locator("#quickStart .quick .name").all_inner_texts()
    check("New score offers the players' instruments first", offered == ["Piano", "Guitar + tablature", "Violin", "Bass + tablature"], offered)
    if shots:
        page.screenshot(path=str(shots / "new-score.png"))
    page.click('#quickStart [data-quick="guitar-tab"] .start')
    page.wait_for_selector("#pages svg")
    score_id = page.evaluate("() => scoreId")
    created.append(score_id)
    data = score_data(base, score_id)
    check("one click on Guitar + tablature starts a guitar score with its TAB staff", data["parts"] == ["Guitar"] and data["partStaves"] == [2] and data["fretted"][0]["tab"] is True, (data["parts"], data["partStaves"]))
    check("ready to write in", page.evaluate("() => editor.on") and page.is_hidden("#newDialog"))
    check("and its staff and tablature are drawn", page.locator("#pages g.measure").first.locator(":scope > g.staff").count() == 2)
    page.keyboard.press("Escape")
    page.click("#blankOpen")
    page.fill("#newChooser input", "tablature")
    found = page.locator('#newChooser [data-list="instruments"] li span').all_inner_texts()
    check("searching for tablature finds a guitar and bass with it", {"Guitar + tablature", "Acoustic Guitar + tablature", "Electric Guitar + tablature", "Bass Guitar + tablature"} <= set(found), found)
    page.click('#newChooser [data-instrument="bass-guitar-tab"] button')
    page.click("#blankNext")
    page.fill("#blankBars", "2")
    page.click("#blankDone")
    page.wait_for_function("id => scoreId !== id", arg=score_id)
    page.wait_for_selector("#pages svg")
    bass_id = page.evaluate("() => scoreId")
    created.append(bass_id)
    bass = score_data(base, bass_id)
    check("a bass with tablature can be chosen from the list too", bass["fretted"][0] is not None and bass["fretted"][0]["strings"] == ["E1", "A1", "D2", "G2"] and bass["fretted"][0]["tab"], bass["fretted"])
    page.keyboard.press("Escape")
    page.keyboard.press("i")
    page.fill("#partChooser input", "tablature")
    page.click('#partChooser [data-instrument="guitar-tab"] button')
    page.click("#partOk")
    settle(page)
    both = score_data(base, bass_id)
    check("and a guitar with tablature can be added to a score", both["parts"] == ["Bass Guitar", "Guitar"] and both["fretted"][1]["tab"] is True and both["partStaves"] == [2, 2], (both["parts"], both["partStaves"]))


def own_notes(root: ET.Element, part: int = 0, staff: str = "1") -> list[ET.Element]:
    """A part's notes with a pitch on one of its staves, in order: the standard staff of a guitar, not its tablature."""
    return [note for note in root.findall("part")[part].iter("note") if note.find("pitch") is not None and (note.findtext("staff") or "1") == staff]


def string_and_fret(root: ET.Element, note_id: str) -> tuple:
    copy = next((note for note in root.iter("note") if note.get("id") == f"{note_id}-tab"), None)
    return (copy.findtext("notations/technical/string"), copy.findtext("notations/technical/fret")) if copy is not None else None


def spots(page, holder: str, kind: str) -> list[list[int]]:
    return page.evaluate("([holder, kind]) => [...document.querySelectorAll(`#${holder} .spot.${kind}`)].map(spot => [Number(spot.dataset.string), Number(spot.dataset.stop)])", [holder, kind])


def slot_cell(page, cell: str):
    return page.locator(f'#instrumentSlots [data-cell="{cell}"]')


def test_guitar_notation(page, base: str, shots: Path | None, created: list[str]) -> None:
    """A guitarist's score: letters in the guitar's range, strings from the fretboard and the inspector, techniques, tuning and capo."""
    score_id = open_blank(page, base, created, bars=3, staff="guitar-tab")
    page.keyboard.press("5")
    page.keyboard.type("egc", delay=0)
    settle(page)
    played = [f"{note.findtext('pitch/step')}{note.findtext('pitch/octave')}" for note in own_notes(saved_score(base, score_id))]
    check("letters typed for a guitar land where it plays, from the middle of its staff", played == ["E4", "G4", "C5"], played)
    show_instrument(page)
    page.locator('#fretboard .spot[data-string="4"][data-stop="7"]').click()
    settle(page)
    page.keyboard.down("Shift")
    page.locator('#fretboard .spot[data-string="2"][data-stop="1"]').click()
    page.keyboard.up("Shift")
    settle(page)
    root = saved_score(base, score_id)
    notes = own_notes(root)
    check("a fret clicked while writing writes its note on that string", [string_and_fret(root, note.get("id")) for note in notes[3:5]] == [("4", "7"), ("2", "1")], [string_and_fret(root, note.get("id")) for note in notes[3:5]])
    page.keyboard.press("Escape")
    settle(page)
    page.wait_for_function("() => Object.keys(playState.data?.events ?? {}).length >= 5")
    a3 = notes[3].get("id")
    click_on(page, page.locator(f"[id='{a3}']"))
    settle(page)
    check("the fretboard shows a note where the tablature has it", spots(page, "fretboard", "chosen") == [[4, 7]], spots(page, "fretboard", "chosen"))
    check("and the inspector says its string and fret", page.inner_text("#stringInfo") == "String 4, fret 7" and page.get_attribute('#stringButtons [data-string="4"]', "aria-pressed") == "true", page.inner_text("#stringInfo"))
    page.click('#stringButtons [data-string="3"]')
    settle(page)
    check("another string can be chosen for it", string_and_fret(saved_score(base, score_id), a3) == ("3", "2") and spots(page, "fretboard", "chosen") == [[3, 2]])
    page.click('#stringButtons [data-string="1"]')
    settle(page)
    check("a string that can't play it says why", "String 1 can't play A3" in page.inner_text("#message") and page.locator('#stringButtons [data-string="1"].cannot').count() == 1, page.inner_text("#message"))
    press(page, "Control+z")
    check("and undo puts it back on its first string", string_and_fret(saved_score(base, score_id), a3) == ("4", "7"))
    if shots:
        page.screenshot(path=str(shots / "guitar-strings.png"))

    # Every technique the inspector offers beside a guitar's note: it's written, drawn, and undone.
    techniques = [
        ("join:hammerOn", 0, 1, "notations/technical/hammer-on", "#pages g.xrn-joined"),
        ("join:pullOff", 1, 2, "notations/technical/pull-off", "#pages g.xrn-joined"),
        ("join:slide", 0, 1, "notations/slide", "#pages g.slide, #pages g.gliss"),
        ("bend:full", 1, None, "notations/technical/bend", "#pages g.xrn-bend"),
        ("vibrato", 2, None, "notations/ornaments/wavy-line", "#pages g.trill, #pages g.vibrato, #pages g.ornam"),
        ("line:palmMute", 0, 2, None, "#pages g.dir"),
        ("line:letRing", 0, 2, None, "#pages g.dir"),
        ("harmonic:natural", 0, None, "notations/technical/harmonic", "#pages g.harm-circle, #pages g.artic, #pages g.fing"),
        ("harmonic:artificial", 0, None, "notations/technical/harmonic/artificial", "#pages text"),
        ("deadNote", 1, None, "notehead", "#pages g.note"),
        ("pluck:i", 2, None, "notations/technical/pluck", "#pages g.fing[data-mark]"),
        ("finger:2", 2, None, "notations/technical/fingering", "#pages g.fing[data-mark]"),
        ("string:3", 1, None, "notations/technical/string", "#pages g.fing[data-mark]"),
    ]
    for cell, first, last, saved_at, drawn in techniques:
        select_notes(page, first, last)
        before = page.locator(drawn).count()
        words_before = page.inner_text("#pages")
        slot_cell(page, cell).click()
        settle(page)
        root = saved_score(base, score_id)
        if saved_at:
            written = len([note for note in own_notes(root) if note.find(saved_at) is not None and (saved_at != "notehead" or note.findtext("notehead") == "x")])
        else:
            written = sum(1 for words in root.iter("words") if words.text in ("P.M.", "let ring"))
        shown = page.locator(drawn).count() > before or page.inner_text("#pages") != words_before
        press(page, "Control+z")
        root = saved_score(base, score_id)
        gone = (len([note for note in own_notes(root) if note.find(saved_at) is not None and (saved_at != "notehead" or note.findtext("notehead") == "x")]) if saved_at else sum(1 for words in root.iter("words") if words.text in ("P.M.", "let ring"))) == 0
        check(f"{cell} beside a guitar's note is written, shown and undone", written >= 1 and shown and gone, (written, shown, gone))
    select_notes(page, 1)
    slot_cell(page, "bend:half").click()
    settle(page)
    check("a bend's button is pressed while the note has it", slot_cell(page, "bend:half").get_attribute("aria-pressed") == "true" and slot_cell(page, "bend:full").get_attribute("aria-pressed") == "false")
    page.keyboard.press("Escape")
    settle(page)
    bend = page.locator("#pages g.xrn-bend").first
    bend.evaluate("element => element.scrollIntoView({ block: 'center' })")
    box = bend.bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + 2)
    settle(page)
    check("a bend on the page can be selected", page.inner_text("#propMarkKind") == "Bend" and page.is_visible("#markEdit"), page.inner_text("#toolLabel"))
    page.keyboard.press("F2")
    page.fill("#chordInput", "1½")
    page.keyboard.press("Enter")
    settle(page)
    check("and retyped", [bend.findtext("bend-alter") for bend in saved_score(base, score_id).iter("bend")] == ["3"])
    page.locator("#pages g.xrn-bend").first.evaluate("element => element.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: element.getBoundingClientRect().x + 3, clientY: element.getBoundingClientRect().y + 3 }))")
    settle(page)
    page.keyboard.press("Delete")
    settle(page)
    check("and deleted", not list(saved_score(base, score_id).iter("bend")) and page.locator("#pages g.xrn-bend").count() == 0)

    # Ctrl+F types fingers for the selected notes, one each in turn.
    select_notes(page, 0, 2)
    page.keyboard.press("Control+f")
    page.keyboard.type("013")
    page.keyboard.press("Enter")
    settle(page)
    check("Ctrl+F then 013 fingers three notes in turn", [note.findtext("notations/technical/fingering") for note in own_notes(saved_score(base, score_id))[:3]] == ["0", "1", "3"])
    page.keyboard.press("Escape")
    settle(page)
    page.locator("#pages g.fing[data-mark]").first.evaluate("element => element.scrollIntoView({ block: 'center' })")
    finger = page.locator("#pages g.fing[data-mark]").first.bounding_box()
    page.mouse.click(finger["x"] + finger["width"] / 2, finger["y"] + finger["height"] / 2)
    settle(page)
    check("a fingering on the page is selected by a click", page.inner_text("#propMarkKind") == "Fingering")
    page.keyboard.press("Delete")
    settle(page)
    check("and Delete takes it off", [note.findtext("notations/technical/fingering") for note in own_notes(saved_score(base, score_id))[:3]] == [None, "1", "3"])
    press(page, "Control+z")

    # The tuning and capo, from the Score tab, and the fretboard follows them.
    page.keyboard.press("Escape")
    settle(page)
    score_pane(page)
    check("the Score tab has the guitar's tuning and capo", page.is_visible("#partSettings") and page.input_value("#tuningChoice") == "standard" and page.input_value("#capoChoice") == "0")
    page.select_option("#tuningChoice", "dropD")
    settle(page)
    check("a tuning chosen there tunes the guitar", score_data(base, score_id)["fretted"][0]["strings"][0] == "D2" and page.locator("#fretboard .stringName").all_inner_texts()[-1] == "D")
    page.select_option("#capoChoice", "2")
    settle(page)
    check("a capo puts a capo on the fretboard, with the frets behind it out of reach", page.locator("#fretboard .capo").count() == 1 and page.locator('#fretboard .spot.blocked[data-stop="1"]').count() == 6)
    click_on(page, page.locator(f"[id='{a3}']"))
    settle(page)
    page.click('#inspectorTabs [data-pane="selection"]')
    check("and a note's place is its fret from the capo", spots(page, "fretboard", "chosen") == [[4, 7]] and page.inner_text("#stringInfo") == "String 4, fret 5 from the capo on fret 2", (spots(page, "fretboard", "chosen"), page.inner_text("#stringInfo")))
    if shots:
        page.screenshot(path=str(shots / "guitar-capo.png"))
    page.keyboard.press("Escape")
    score_pane(page)
    page.select_option("#tuningChoice", "custom")
    page.fill("#customStrings", "C2 G2 D3 G3 C4 E4")
    page.keyboard.press("Enter")
    settle(page)
    check("a tuning can be typed string by string", score_data(base, score_id)["fretted"][0]["tuning"] == "custom" and page.locator("#fretboard .stringName").all_inner_texts() == ["E", "C", "G", "D", "G", "C"])
    page.click("#tabSwitch")
    settle(page)
    check("the tablature can be taken away", score_data(base, score_id)["partStaves"] == [1] and page.locator("#pages g.measure").first.locator(":scope > g.staff").count() == 1)
    page.click("#tabSwitch")
    settle(page)
    check("and brought back", score_data(base, score_id)["partStaves"] == [2])
    page.click("#diagramsSwitch")
    settle(page)
    check("chord diagrams are switched on there", score_data(base, score_id)["diagrams"] is True and page.get_attribute("#diagramsSwitch", "aria-pressed") == "true")
    press(page, "Control+z")
    check("and undone", score_data(base, score_id)["diagrams"] is False)

    # A note no string can play is listed, and a click goes to it.
    last = own_notes(saved_score(base, score_id))[-1].get("id")
    page.evaluate("id => enqueue(() => send(`notes/${id}`, { action: 'write', type: 'quarter', dots: 0, midi: 30 }))", last)
    settle(page)
    chip = page.locator("#barChecks [data-fret-problem]")
    check("a note no string can play is listed with the bar checks", chip.count() == 1 and "below this instrument's lowest string" in chip.inner_text(), chip.all_inner_texts())
    chip.click()
    settle(page)
    check("and a click selects it", page.evaluate("() => selected") == chip.get_attribute("data-fret-problem"))
    command(page, "hammer-on")
    check("the techniques are in the command palette", True)
    page.keyboard.press("Control+Shift+P")
    page.fill("#commandInput", "type fingers")
    found = page.locator("#commandList li").first.inner_text()
    check("and the fingering box with its key", "Ctrl" in found and "F" in found, found)
    page.keyboard.press("Escape")


def technique_round(page, base: str, score_id: str, cell: str, first: int, last: int | None, written, drawn: str, where: str) -> None:
    """Press a cell beside the selected notes: the score has it, the page shows it, and undo takes it away."""
    select_notes(page, first, last)
    before, words = page.locator(drawn).count(), page.inner_text("#pages")
    page.locator(f'#instrumentSlots [data-cell="{cell}"]').click()
    settle(page)
    has = written(saved_score(base, score_id))
    shown = page.locator(drawn).count() > before or page.inner_text("#pages") != words
    press(page, "Control+z")
    gone = not written(saved_score(base, score_id))
    check(f"{cell} beside a {where}'s note is written, shown and undone", has and shown and gone, (has, shown, gone))


def words_in(root: ET.Element, *texts: str) -> list[str]:
    return [words.text for words in root.iter("words") if words.text in texts]


def test_violin_notation(page, base: str, shots: Path | None, created: list[str]) -> None:
    """A violinist's score: bowings by key, strings, positions and fingers, and the fingerboard following them."""
    score_id = open_blank(page, base, created, bars=3, staff="violin")
    page.keyboard.press("5")
    page.keyboard.type("gbd", delay=0)
    settle(page)
    show_instrument(page)
    page.locator('#fingerboard .spot[data-string="3"][data-stop="9"]').click()
    settle(page)
    page.locator('#fingerboard .spot[data-string="2"][data-stop="2"]').click()
    settle(page)
    notes = own_notes(saved_score(base, score_id))
    check("a place on another string writes the note with that string's number", [note.findtext("notations/technical/string") for note in notes[3:5]] == ["3", None] and [note.findtext("pitch/step") for note in notes[3:5]] == ["B", "B"], [note.findtext("notations/technical/string") for note in notes])
    page.keyboard.press("Escape")
    settle(page)
    page.wait_for_function("() => Object.keys(playState.data?.events ?? {}).length >= 5")
    select_notes(page, 3)
    check("the fingerboard shows it on its string", spots(page, "fingerboard", "chosen") == [[3, 9]], spots(page, "fingerboard", "chosen"))
    select_notes(page, 0)
    page.keyboard.press("[")
    settle(page)
    check("[ marks a down-bow", len(notes_with(saved_score(base, score_id), "notations/technical/down-bow")) == 1 and page.locator("#pages g.artic").count() >= 1)
    select_notes(page, 1)
    page.keyboard.press("]")
    settle(page)
    check("] marks an up-bow", len(notes_with(saved_score(base, score_id), "notations/technical/up-bow")) == 1)
    press(page, "Control+z")
    press(page, "Control+z")
    check("and undo takes them away", not notes_with(saved_score(base, score_id), "notations/technical/down-bow") and not notes_with(saved_score(base, score_id), "notations/technical/up-bow"))
    rows = page.locator("#instrumentSlots .slot").evaluate_all("rows => rows.map(row => row.dataset.row)")
    check("a violinist's note has fingers, strings, sul, positions and bowing beside it", rows == ["Fingers", "String", "Sul", "Position", "Bow"], rows)
    sul_cells = page.locator('#instrumentSlots [data-row="Sul"] button').evaluate_all("buttons => buttons.map(button => button.dataset.cell)")
    check("sul offers the violin's own strings", sul_cells == ["sul:G", "sul:D", "sul:A", "sul:E", "sul:pont.", "sul:tasto"], sul_cells)

    technical = lambda tag: lambda root: bool(notes_with(root, f"notations/technical/{tag}"))
    for cell, first, last, written, drawn in [
        ("finger:2", 1, None, technical("fingering"), "#pages g.fing"),
        ("bowedString:2", 1, None, lambda root: own_notes(root)[1].findtext("notations/technical/string") == "2", "#pages g.fing"),
        ("sul:G", 0, 2, lambda root: bool(words_in(root, "sul G")), "#pages g.dir"),
        ("sul:pont.", 0, 2, lambda root: bool(words_in(root, "sul pont.")), "#pages g.dir"),
        ("position:3", 0, None, lambda root: bool(words_in(root, "III pos.")), "#pages g.dir"),
        ("pizzicato:on", 1, None, lambda root: bool(words_in(root, "pizz.")), "#pages g.dir"),
        ("pizzicato:off", 1, None, lambda root: bool(words_in(root, "arco")), "#pages g.dir"),
        ("mark:open", 0, None, technical("open-string"), "#pages g.artic"),
        ("mark:stopped", 1, None, technical("stopped"), "#pages g.artic"),
        ("harmonic:natural", 2, None, technical("harmonic"), "#pages g.artic, #pages g.fing"),
        ("harmonic:artificial", 2, None, lambda root: len(notes_with(root, "notations/technical/harmonic")) == 2, "#pages g.note"),
    ]:
        technique_round(page, base, score_id, cell, first, last, written, drawn, "violin")

    # A position and a sul over the notes move them on the fingerboard, with the finger they're played with.
    select_notes(page, 0)
    page.locator('#instrumentSlots [data-cell="position:3"]').click()
    settle(page)
    select_notes(page, 1)
    check("a note in third position is shown there, with its finger", page.evaluate("() => [...document.querySelectorAll('#fingerboard .spot.chosen')].map(spot => [spot.dataset.string, spot.dataset.stop, spot.dataset.show])") == [["3", "9", "3"]] and page.get_attribute('#instrumentSlots [data-cell="position:3"]', "aria-pressed") == "true")
    select_notes(page, 0, 2)
    page.locator('#instrumentSlots [data-cell="sul:G"]').click()
    settle(page)
    select_notes(page, 1)
    check("and sul G puts it on the G string", spots(page, "fingerboard", "chosen") == [[4, 16]], spots(page, "fingerboard", "chosen"))
    page.locator('#instrumentSlots [data-cell="finger:4"]').click()
    settle(page)
    check("a finger marked is the one the fingerboard shows", page.evaluate("() => document.querySelector('#fingerboard .spot.chosen').dataset.show") == "4")
    if shots:
        page.screenshot(path=str(shots / "violin-write.png"))
    page.keyboard.press("Escape")
    settle(page)
    position = page.locator("#pages g.dir", has_text="III pos.").first
    position.evaluate("element => element.scrollIntoView({ block: 'center' })")
    box = position.bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    settle(page)
    check("a position on the page can be selected", page.inner_text("#propMarkKind") == "Position", page.inner_text("#toolLabel"))
    page.keyboard.press("Delete")
    settle(page)
    check("and deleted", not words_in(saved_score(base, score_id), "III pos."))
    press(page, "Control+z")
    check("and undo brings it back", words_in(saved_score(base, score_id), "III pos.") == ["III pos."])
    page.keyboard.press("Control+Shift+P")
    page.fill("#commandInput", "up bow")
    found = page.locator("#commandList li").first.inner_text()
    page.keyboard.press("Escape")
    check("the palette has the bowings with their keys", "Up bow" in found and "]" in found, found)


def test_piano_notation(page, base: str, shots: Path | None, created: list[str]) -> None:
    """A pianist's score: fingers typed after selecting notes, hands, and a note drawn on the other staff, heard with that hand."""
    score_id = open_blank(page, base, created, bars=2, staff="piano")
    page.keyboard.press("5")
    page.keyboard.type("cegc", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    lower = page.evaluate("""() => document.querySelector("#pages g.measure").querySelectorAll("g.staff")[1].querySelector("g.mRest, g.rest").id""")
    click_on(page, page.locator(f"[id='{lower}']"))
    settle(page)
    page.keyboard.press("n")
    settle(page)
    page.keyboard.press("6")
    page.keyboard.type("cg", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    page.keyboard.press("Escape")
    right = lambda: own_notes(saved_score(base, score_id))
    left = lambda: own_notes(saved_score(base, score_id), staff="2")
    fingers = lambda notes: [(note.findtext("notations/technical/fingering"), note.find("notations/technical/fingering").get("placement") if note.find("notations/technical/fingering") is not None else None) for note in notes]
    select_notes(page, 0, 2)
    rows = page.locator("#instrumentSlots .slot").evaluate_all("rows => rows.map(row => row.dataset.row)")
    check("a pianist's note has fingers and hands beside it", rows == ["Fingers", "Hand"], rows)
    page.keyboard.press("Control+f")
    check("Ctrl+F asks for the fingers of the notes selected", page.is_visible("#chordInput") and "3 in turn" in page.get_attribute("#chordInput", "placeholder"), page.get_attribute("#chordInput", "placeholder"))
    page.keyboard.type("135")
    page.keyboard.press("Enter")
    settle(page)
    check("then 135 fingers them one each, above the right hand", fingers(right()[:3]) == [("1", "above"), ("3", "above"), ("5", "above")], fingers(right()))
    check("and the digits typed there don't change the notes' lengths", [note.findtext("type") for note in right()[:3]] == ["quarter"] * 3)
    left_note = page.evaluate("""() => document.querySelector("#pages g.measure").querySelectorAll("g.staff")[1].querySelector("g.note").id""")
    click_on(page, page.locator(f"[id='{left_note}']"))
    settle(page)
    page.locator('#instrumentSlots [data-cell="finger:5"]').click()
    settle(page)
    check("a finger for the left hand goes below its staff", fingers(left()[:1]) == [("5", "below")], fingers(left()))
    check("and its button shows it", page.get_attribute('#instrumentSlots [data-cell="finger:5"]', "aria-pressed") == "true")
    page.keyboard.press("Control+f")
    page.keyboard.type("0")
    page.keyboard.press("Enter")
    settle(page)
    check("a finger a pianist hasn't got is refused, with the range", page.is_visible("#editorError") and "1 to 5" in page.inner_text("#editorError"), page.inner_text("#editorError"))
    page.keyboard.press("Escape")
    for cell, words in [("hand:RH", ["R.H."]), ("hand:LH", ["L.H."])]:
        technique_round(page, base, score_id, cell, 3, None, lambda root, words=words: words_in(root, *words) == words, "#pages g.dir", "piano")
    select_notes(page, 3)
    page.locator('#instrumentSlots [data-cell="hand:RH"]').click()
    settle(page)
    page.locator('#instrumentSlots [data-cell="hand:LH"]').click()
    settle(page)
    check("the other hand's words take the place of the first's", words_in(saved_score(base, score_id), "R.H.", "L.H.") == ["L.H."])

    # The last note of the right hand drawn on the left hand's staff, as Ctrl+Shift+↓ does in MuseScore.
    crossed = right()[3].get("id")
    lines = lambda staff: page.evaluate("staff => { const lines = staffLines(document.querySelector('#pages g.measure').querySelectorAll('g.staff')[staff]); return [lines.top, lines.bottom]; }", staff)
    head_y = lambda: page.evaluate("id => { const box = document.getElementById(id).querySelector('.notehead').getBoundingClientRect(); return box.top + box.height / 2; }", crossed)
    select_notes(page, 3)
    page.keyboard.press("Control+Shift+ArrowDown")
    settle(page)
    lower_top, lower_bottom = lines(1)
    # It's C6, which the bass clef puts sixteen steps above its top line, A3.
    wanted = lower_top - 16 * (lower_bottom - lower_top) / 8
    check("Ctrl+Shift+↓ draws the note on the staff below, in its clef", score_data(base, score_id)["notes"][crossed][5].get("cross") == 2 and abs(head_y() - wanted) < 3, (head_y(), wanted))
    check("and the inspector says which hand plays it", "left hand" in page.inner_text("#propWhat") and page.get_attribute('#instrumentSlots [data-cell="cross:down"]', "aria-pressed") == "true", page.inner_text("#propWhat"))
    check("its bar still adds up in its own voice", score_data(base, score_id)["barChecks"] == [])
    page.wait_for_function("id => playState.data?.notes?.some(note => note.id === id && note.staff === 2)", arg=crossed)
    heard = lambda choice: page.evaluate("""async choice => {
        practice.hear = choice;
        const data = await fetchPlayback();
        return schedule(new OfflineAudioContext(1, 44100, 44100), data, 0, 0, 1, Infinity, { only: notesOf(choice) }).notes.map(note => note.id);
    }""", choice)
    check("Practice's left hand alone plays it", crossed in heard("staff:0:2") and crossed not in heard("staff:0:1"), heard("staff:0:2"))
    if shots:
        page.screenshot(path=str(shots / "piano-write.png"))
    page.keyboard.press("Control+Shift+ArrowUp")
    settle(page)
    check("and Ctrl+Shift+↑ brings it back", "cross" not in (score_data(base, score_id)["notes"][crossed][5:] or [{}])[0])
    press(page, "Control+z")
    check("which undo takes back", score_data(base, score_id)["notes"][crossed][5].get("cross") == 2)
    page.keyboard.press("Control+Shift+ArrowUp")
    settle(page)
    page.keyboard.press("Control+Shift+ArrowUp")
    settle(page)
    check("there's no staff above the right hand, and it says so", "no staff there" in page.inner_text("#message"), page.inner_text("#message"))
    page.keyboard.press("Control+Shift+P")
    page.fill("#commandInput", "staff below")
    found = page.locator("#commandList li").first.inner_text()
    page.keyboard.press("Escape")
    check("the palette has it with its key", "staff below" in found and "Ctrl" in found and "↓" in found, found)
    page.keyboard.press("Escape")
    page.click('#modeSwitch [data-mode="practice"]')
    page.select_option("#practiceHear", "staff:0:1")
    if shots:
        page.screenshot(path=str(shots / "piano-practice.png"))
    page.click('#modeSwitch [data-mode="write"]')


def click_drawn(page, locator) -> None:
    """Click the middle of something drawn on the page, such as a fingering or the words of a line."""
    locator.evaluate("element => element.scrollIntoView({ block: 'center' })")
    box = locator.bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    settle(page)


def marks_api(base: str, score_id: str, body: dict) -> dict:
    request = urllib.request.Request(f"{base}/api/scores/{score_id}/marks", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def test_every_technique_mark(page, base: str, shots: Path | None, created: list[str]) -> None:
    """Each mark a player's technique leaves on the page is selected by a click, named, retyped where it has words, deleted and undone."""
    score_id = open_blank(page, base, created, bars=2, staff="guitar-tab")
    page.keyboard.press("5")
    page.keyboard.type("egab", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    ids = [note.get("id") for note in own_notes(saved_score(base, score_id))]
    for body in [{"action": "pluck", "notes": ids[:1], "text": "p"}, {"action": "stringNumber", "notes": ids[1:2], "number": 3},
                 {"action": "join", "notes": ids[2:4], "name": "pullOff"}, {"action": "line", "notes": ids[:2], "name": "palmMute"}]:
        marks_api(base, score_id, body)
    page.reload()
    page.wait_for_selector("#pages svg")
    page.click('#modeSwitch [data-mode="write"]')
    kinds = lambda: sorted(mark["kind"] for mark in score_data(base, score_id)["marks"])
    check("the marks are listed for the page", kinds() == ["pluck", "pull", "string", "textline"], kinds())
    # A pluck and a string number are retyped, as the inspector's Edit text does.
    for drawn, name, retyped, path in [("p", "Right-hand finger", "a", "notations/technical/pluck"), ("3", "String number", "2", "notations/technical/string")]:
        click_drawn(page, page.locator("#pages g.fing[data-mark]", has_text=drawn).first)
        check(f"a drawn {name.lower()} is selected and named", page.inner_text("#propMarkKind") == name and page.is_visible("#markEdit"), page.inner_text("#toolLabel"))
        page.click("#markEdit")
        page.fill("#chordInput", retyped)
        page.keyboard.press("Enter")
        settle(page)
        check(f"and retyped as {retyped}", [note.findtext(path) for note in own_notes(saved_score(base, score_id)) if note.find(path) is not None] == [retyped])
        click_drawn(page, page.locator("#pages g.fing[data-mark]", has_text=retyped).first)
        page.keyboard.press("Delete")
        settle(page)
        check("and deleted", not [note for note in own_notes(saved_score(base, score_id)) if note.find(path) is not None])
        press(page, "Control+z")
        check("and undone", len([note for note in own_notes(saved_score(base, score_id)) if note.find(path) is not None]) == 1)
    check("a guitar's string number retyped moved its tablature to that string", string_and_fret(saved_score(base, score_id), ids[1]) == ("2", "8"), string_and_fret(saved_score(base, score_id), ids[1]))
    page.keyboard.press("Escape")
    settle(page)
    spot = page.evaluate("""() => {
        const path = document.querySelector("#pages g.slur[data-mark] path");
        path.scrollIntoView({ block: "center" });
        const { x, y } = path.getPointAtLength(path.getTotalLength() / 2);
        return new DOMPoint(x, y).matrixTransform(path.getScreenCTM());
    }""")
    page.mouse.click(spot["x"], spot["y"])
    settle(page)
    check("a pull-off's slur is selected, and has no words to change", page.inner_text("#propMarkKind") == "Pull-off" and page.is_hidden("#markEdit"), page.inner_text("#toolLabel"))
    page.keyboard.press("Delete")
    settle(page)
    check("Delete takes both its ends away", not notes_with(saved_score(base, score_id), "notations/technical/pull-off") and page.locator("#pages g.xrn-joined").count() == 0)
    press(page, "Control+z")
    check("and undo brings it back", len(notes_with(saved_score(base, score_id), "notations/technical/pull-off")) == 2)
    click_drawn(page, page.locator("#pages g.dir", has_text="P.M.").first)
    check("palm mute's words select its line", page.inner_text("#propMarkKind") == "Palm mute", page.inner_text("#toolLabel"))
    page.keyboard.press("Delete")
    settle(page)
    check("which Delete takes away with its end", not words_in(saved_score(base, score_id), "P.M.") and not list(saved_score(base, score_id).iter("dashes")))
    press(page, "Control+z")

    # A violin's "sul G" line and pizz., and a pianist's hand, are words that are selected and changed too.
    violin = open_blank(page, base, created, bars=2, staff="violin")
    page.keyboard.press("5")
    page.keyboard.type("gbd", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    notes = [note.get("id") for note in own_notes(saved_score(base, violin))]
    marks_api(base, violin, {"action": "sul", "notes": notes[:2], "text": "G"})
    marks_api(base, violin, {"action": "pizzicato", "notes": notes[2:3], "on": True})
    marks_api(base, violin, {"action": "hand", "notes": notes[:1], "name": "RH"})
    page.reload()
    page.wait_for_selector("#pages svg")
    click_drawn(page, page.locator("#pages g.dir", has_text="sul G").first)
    check("a sul line is selected by its words", page.inner_text("#propMarkKind") == "String to play on", page.inner_text("#toolLabel"))
    page.keyboard.press("F2")
    page.fill("#chordInput", "sul D")
    page.keyboard.press("Enter")
    settle(page)
    check("and retyped", words_in(saved_score(base, violin), "sul D") == ["sul D"])
    for words, name in [("pizz.", "Pizzicato or arco"), ("R.H.", "Hand")]:
        click_drawn(page, page.locator("#pages g.dir", has_text=words).first)
        check(f"{words} is selected and named", page.inner_text("#propMarkKind") == name, page.inner_text("#toolLabel"))
        page.keyboard.press("Delete")
        settle(page)
        check(f"and {words} deleted", not words_in(saved_score(base, violin), words))
        press(page, "Control+z")
        check(f"and {words} undone", words_in(saved_score(base, violin), words) == [words])

    # Every cell for a player's technique is in the command palette, with its key where it has one.
    found = page.evaluate("""() => {
        const listed = new Map(allCommands().filter(command => command.cell).map(command => [command.cell, command.keys]));
        const wanted = catalogue().filter(palette => ["guitar", "fingers", "bowed", "hands"].includes(palette.id)).flatMap(palette => palette.cells.map(cell => cell.key));
        return { missing: wanted.filter(key => !listed.has(key)), keys: ["mark:downbow", "mark:upbow", "finger:type", "cross:up", "cross:down"].map(key => listed.get(key)), count: wanted.length };
    }""")
    check("every technique is in the command palette, with its key", not found["missing"] and found["keys"] == ["[", "]", "Ctrl+F", "Ctrl+Shift+↑", "Ctrl+Shift+↓"] and found["count"] > 50, found)


def test_techniques_at_concert_pitch(page, base: str, shots: Path | None, created: list[str]) -> None:
    """A bass guitar sounds an octave below its music: at concert pitch its marks, frets and strings stay as they are."""
    score_id = open_blank(page, base, created, bars=2, staff="bass-guitar-tab")
    show_instrument(page)
    page.keyboard.press("5")
    page.locator('#fretboard .spot[data-string="3"][data-stop="5"]').click()
    settle(page)
    page.locator('#fretboard .spot[data-string="2"][data-stop="2"]').click()
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    ids = [note.get("id") for note in own_notes(saved_score(base, score_id))]
    written = [(note.findtext("pitch/step"), note.findtext("pitch/octave")) for note in own_notes(saved_score(base, score_id))]
    check("a bass's fret writes the note an octave above its sound, on that string", written == [("D", "3"), ("E", "3")] and [string_and_fret(saved_score(base, score_id), note_id) for note_id in ids] == [("3", "5"), ("2", "2")], (written, [string_and_fret(saved_score(base, score_id), note_id) for note_id in ids]))
    marks_api(base, score_id, {"action": "fingering", "notes": ids, "text": "12"})
    marks_api(base, score_id, {"action": "bend", "notes": ids[1:], "name": "half"})
    page.reload()
    page.wait_for_selector("#pages svg")
    page.click('#modeSwitch [data-mode="write"]')
    show_instrument(page)
    drawn = lambda: page.locator("#pages g[data-mark]").count()
    before = drawn()
    page.click("#concertPitch") if page.is_visible("#concertPitch") else menu(page, "View", "Concert pitch")
    settle(page)
    check("at concert pitch the bass's fingerings and bend are still drawn and named", score_data(base, score_id)["concertPitch"] and drawn() == before and before >= 3, (before, drawn()))
    page.wait_for_function("() => playState.data !== null")
    select_notes(page, 0)
    check("its note is still at its string and fret", spots(page, "fretboard", "chosen") == [[3, 5]], spots(page, "fretboard", "chosen"))
    click_drawn(page, page.locator("#pages g.fing[data-mark]").first)
    check("and a fingering is still selected by a click", page.inner_text("#propMarkKind") == "Fingering")
    page.keyboard.press("Escape")
    settle(page)
    last = page.evaluate("() => allEvents().filter(element => staffNumber(element) === 0).at(-1).id")
    click_on(page, page.locator(f"[id='{last}']"))
    settle(page)
    page.keyboard.press("n")
    settle(page)
    page.locator('#fretboard .spot[data-string="1"][data-stop="7"]').click()
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    root = saved_score(base, score_id)
    newest = own_notes(root)[-1]
    check("a fret clicked at concert pitch writes the same note for the player, on that string", (newest.findtext("pitch/step"), newest.findtext("pitch/octave")) == ("D", "4") and string_and_fret(root, newest.get("id")) == ("1", "7"), (newest.findtext("pitch/step"), newest.findtext("pitch/octave"), string_and_fret(root, newest.get("id"))))
    if shots:
        page.screenshot(path=str(shots / "bass-concert.png"))
    page.click("#concertPitch") if page.is_visible("#concertPitch") else menu(page, "View", "Concert pitch")
    settle(page)


def test_techniques_copied_and_the_capo(page, base: str, shots: Path | None, created: list[str]) -> None:
    """Copied guitar notes keep their techniques and strings, and a capo is named over the music."""
    score_id = open_blank(page, base, created, bars=3, staff="guitar-tab")
    page.keyboard.press("5")
    page.keyboard.type("egab", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    ids = [note.get("id") for note in own_notes(saved_score(base, score_id))]
    for body in [{"action": "fingering", "notes": ids[:2], "text": "12"}, {"action": "bend", "notes": ids[3:], "name": "full"},
                 {"action": "join", "notes": ids[:2], "name": "hammerOn"}, {"action": "line", "notes": ids[2:4], "name": "palmMute"}]:
        marks_api(base, score_id, body)
    page.evaluate("id => enqueue(() => send(`notes/${id}`, { action: 'string', string: 3 }))", ids[2])
    settle(page)
    page.reload()
    page.wait_for_selector("#pages svg")
    page.click('#modeSwitch [data-mode="write"]')
    select_notes(page, 0, 3)
    page.keyboard.press("Control+c")
    settle(page)
    rest = page.evaluate("() => document.querySelectorAll('#pages g.measure')[1].querySelector('g.staff g.mRest, g.staff g.rest').id")
    click_on(page, page.locator(f"[id='{rest}']"))
    settle(page)
    page.keyboard.press("Control+v")
    settle(page)
    root = saved_score(base, score_id)
    pasted = own_notes(root)[4:8]
    check("pasted notes keep their fingers, bend and hammer-on", [note.findtext("notations/technical/fingering") for note in pasted] == ["1", "2", None, None] and pasted[3].find("notations/technical/bend") is not None and pasted[0].find("notations/technical/hammer-on") is not None)
    check("and the string a note was played on", string_and_fret(root, pasted[2].get("id")) == ("3", "14"), string_and_fret(root, pasted[2].get("id")))
    check("and the page draws them", page.locator("#pages g.xrn-bend").count() == 2 and page.locator("#pages g.xrn-joined").count() == 2)
    check("and the palm mute over them", sum(1 for words in root.iter("words") if words.text == "P.M.") == 2 and page.locator("#pages g.dir", has_text="P.M.").count() == 2)
    press(page, "Control+z")
    check("and one undo takes the paste away", len(own_notes(saved_score(base, score_id))) == 4)
    score_pane(page)
    page.select_option("#capoChoice", "2")
    settle(page)
    check("a capo is named over the music", page.locator("#pages g.xrn-capo").count() == 1 and "Capo 2" in page.locator("#pages g.xrn-capo").text_content())
    page.click('#inspectorTabs [data-pane="selection"]')
    click_drawn(page, page.locator("#pages g.dir", has_text="P.M.").first)
    check("and the marks in its first bar can still be selected", page.inner_text("#propMarkKind") == "Palm mute", page.inner_text("#toolLabel"))
    if shots:
        page.screenshot(path=str(shots / "guitar-capo-words.png"))
    page.keyboard.press("Escape")


def chord_diagram(base: str, score_id: str, index: int = 0) -> dict:
    return score_data(base, score_id)["chords"][index]["diagram"]


def test_chord_diagram_editing(page, base: str, shots: Path | None, created: list[str]) -> None:
    """A chord's diagram is changed in the inspector: another shape, a finger, an open or unplayed string, a barre."""
    score_id = open_blank(page, base, created, bars=2, staff="guitar-tab")
    page.keyboard.press("7")
    page.keyboard.type("ee", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    notes = [note.get("id") for note in own_notes(saved_score(base, score_id))]
    for note, text in zip(notes, ["C", "G7"]):
        page.evaluate("([id, text]) => enqueue(() => send('chords', { note: id, text }))", [note, text])
    settle(page)
    click_drawn(page, page.locator("#pages g.harm").first)
    check("a chord clicked without diagrams says how to show them", page.is_visible("#propChord") and page.is_visible("#diagramShow") and page.is_hidden("#chordDiagramTools"))
    page.click("#diagramShow")
    settle(page)
    page.wait_for_selector("#chordShapes button")
    check("with them, the inspector draws the chord's diagram and its other shapes", page.is_visible("#chordDiagram svg") and page.locator("#chordShapes button").count() >= 4 and page.locator('#chordShapes button[aria-pressed="true"]').count() == 1)
    page.locator("#chordDiagram [data-string='5'][data-fret='3']").click()
    settle(page)
    check("a click on a string at a fret puts a finger there", chord_diagram(base, score_id) == {"frets": [None, 3, 2, 0, 1, 3], "barre": None, "first": 1, "custom": True}, chord_diagram(base, score_id))
    frame = saved_score(base, score_id).find(".//harmony/frame")
    check("and the score keeps it as the chord's frame", frame is not None and [(note.findtext("string"), note.findtext("fret")) for note in frame.findall("frame-note")][-1] == ("1", "3"))
    page.locator("#chordDiagram [data-string='0'][data-fret='nut']").click()
    settle(page)
    check("above the nut, a string not played is played open", chord_diagram(base, score_id)["frets"][0] == 0)
    press(page, "Control+z")
    press(page, "Control+z")
    check("and undo takes them back", chord_diagram(base, score_id)["custom"] is False)
    click_drawn(page, page.locator("#pages g.harm").first)
    page.wait_for_selector("#chordShapes button")
    page.locator("#chordShapes button", has=page.locator("text")).first.click()
    settle(page)
    chosen = chord_diagram(base, score_id)
    check("another shape is chosen from those offered", chosen["custom"] and chosen["first"] > 1 and "xrn-diagram" in "".join(score_data(base, score_id)["pages"]), chosen)
    if shots:
        page.screenshot(path=str(shots / "chord-diagram.png"))
    page.click("#diagramUsual")
    settle(page)
    check("and Usual shape gives it the library's again", chord_diagram(base, score_id) == {"frets": [None, 3, 2, 0, 1, 0], "barre": None, "first": 1, "custom": False}, chord_diagram(base, score_id))
    page.click("#diagramBody")
    shown_from = page.evaluate("() => Number(document.querySelector('#chordDiagram [data-fret]:not([data-fret=nut])').dataset.fret)")
    page.click("#diagramNut")
    check("the diagram's window moves up the neck and back", shown_from == 2 and page.evaluate("() => chordPane.first") == 1, shown_from)
    for string in range(2, 6):  # The A string is at the third fret already.
        page.locator(f"#chordDiagram [data-string='{string}'][data-fret='3']").click()
        settle(page)
    page.click("#diagramBarre")
    settle(page)
    check("a barre lies across the strings at the lowest fret", chord_diagram(base, score_id)["barre"] == [3, 1, 5], chord_diagram(base, score_id))
    page.keyboard.press("Escape")
    settle(page)


def test_words_copied_with_a_passage(page, base: str, created: list[str]) -> None:
    """A violinist's pizz., position and sul G come with a copied passage, and only where they belong."""
    score_id = open_blank(page, base, created, bars=3, staff="violin")
    page.keyboard.press("5")
    page.keyboard.type("gabc", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    ids = [note.get("id") for note in own_notes(saved_score(base, score_id))]
    for body in [{"action": "pizzicato", "notes": ids[:1], "on": True}, {"action": "position", "notes": ids[1:2], "number": 3},
                 {"action": "sul", "notes": ids[1:3], "text": "G"}]:
        marks_api(base, score_id, body)
    page.reload()
    page.wait_for_selector("#pages svg")
    page.click('#modeSwitch [data-mode="write"]')
    select_notes(page, 0, 3)
    page.keyboard.press("Control+c")
    settle(page)
    rest = page.evaluate("() => document.querySelectorAll('#pages g.measure')[1].querySelector('g.staff g.mRest, g.staff g.rest').id")
    click_on(page, page.locator(f"[id='{rest}']"))
    settle(page)
    page.keyboard.press("Control+v")
    settle(page)
    said = sorted(words.text for words in saved_score(base, score_id).iter("words"))
    check("a copy brings its pizz., position and sul G", said == ["III pos.", "III pos.", "pizz.", "pizz.", "sul G", "sul G"], said)
    check("and the page draws them", page.locator("#pages g.dir", has_text="sul G").count() == 2)
    press(page, "Control+z")
    check("and one undo takes them away with the notes", sorted(words.text for words in saved_score(base, score_id).iter("words")) == ["III pos.", "pizz.", "sul G"])
    page.keyboard.press("Escape")


def test_marks_drawn_on_notes(page, base: str, shots: Path | None, created: list[str]) -> None:
    """Slides, glissandos, vibratos, harmonics, bowings and articulations are clicked, named, deleted and undone."""
    score_id = open_blank(page, base, created, bars=2, staff="guitar-tab")
    page.keyboard.press("5")
    page.keyboard.type("egabcdef", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    ids = [note.get("id") for note in own_notes(saved_score(base, score_id))]
    for body in [{"action": "join", "notes": ids[0:2], "name": "slide"}, {"action": "join", "notes": ids[2:4], "name": "glissando"},
                 {"action": "vibrato", "notes": ids[4:6]}, {"action": "harmonic", "notes": ids[6:7], "name": "natural"},
                 {"action": "toggle", "notes": ids[7:8], "name": "upbow"}, {"action": "toggle", "notes": ids[5:6], "name": "staccato"}]:
        marks_api(base, score_id, body)
    page.reload()
    page.wait_for_selector("#pages svg")
    page.click('#modeSwitch [data-mode="write"]')
    listed = {mark["kind"]: mark["id"] for mark in score_data(base, score_id)["marks"]}
    for kind, name, selector, gone in [
        ("slide", "Slide", f'#pages g.gliss[data-mark="{listed["slide"]}"]', "slide"),
        ("glissando", "Glissando", f'#pages g.gliss[data-mark="{listed["glissando"]}"]', "glissando"),
        ("vibrato", "Vibrato", f'#pages g.trill[data-mark="{listed["vibrato"]}"]', "wavy-line"),
        ("harmonic", "Harmonic", f'#pages g.artic[data-mark="{listed["harmonic"]}"]', "harmonic"),
        ("upbow", "Up-bow", f'#pages g.artic[data-mark="{listed["upbow"]}"]', "up-bow"),
        ("staccato", "Staccato", f'#pages g.artic[data-mark="{listed["staccato"]}"]', "staccato"),
    ]:
        click_mark(page, selector)
        named = page.evaluate("() => selectedMark") == listed[kind] and page.inner_text("#propMarkKind") == name
        selected = page.locator(f'#pages [data-mark="{listed[kind]}"].selected').count() >= 1
        page.keyboard.press("Delete")
        settle(page)
        removed = not list(saved_score(base, score_id).iter(gone)) and page.locator(selector).count() == 0
        press(page, "Control+z")
        back = len(list(saved_score(base, score_id).iter(gone))) >= 1
        check(f"a {name.lower()} is selected by a click, deleted and undone", named and selected and removed and back, (named, selected, removed, back))
    if shots:
        click_mark(page, f'#pages g.gliss[data-mark="{listed["slide"]}"]')
        page.screenshot(path=str(shots / "slide-selected.png"))
    page.keyboard.press("Escape")
    click_on(page, page.locator(f"[id='{ids[5]}']"))
    check("a click on the note itself still selects the note", page.evaluate("() => selected") == ids[5] and page.evaluate("() => selectedMark") is None)
    page.keyboard.press("Escape")


def tab_point(page, event_id: str, string: int) -> tuple[float, float]:
    """Where a string of the tablature is, at a note or rest of the staff it's linked under."""
    return tuple(page.evaluate("""([id, string]) => {
        const element = document.getElementById(id);
        element.scrollIntoView({ block: 'center' });
        const lines = stringLines(tabStaffFor(element));
        const box = headBox(element);
        return [box.left + box.width / 2, lines.top + (string - 1) * lines.gap];
    }""", [event_id, string]))


def test_tablature_editing(page, base: str, shots: Path | None, created: list[str]) -> None:
    """Fret numbers are clicked, retyped and moved to other strings; notes are written on the tablature itself."""
    score_id = open_blank(page, base, created, bars=2, staff="guitar-tab")
    page.keyboard.type("ec", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    first, second = [note.get("id") for note in own_notes(saved_score(base, score_id))]
    click_on(page, page.locator(f"[id='{first}-tab']"))
    settle(page)
    check("a fret number on the tablature is selected by a click", page.evaluate("() => selected") == f"{first}-tab" and page.locator(f"[id='{first}-tab'].selected").count() == 1)
    check("and the inspector says how to change it", "Type a fret number" in page.inner_text("#stringInfo"), page.inner_text("#stringInfo"))
    page.keyboard.press("5")
    page.wait_for_timeout(800)
    settle(page)
    root = saved_score(base, score_id)
    check("a fret typed on it plays the note there, and the staff above follows", (own_notes(root)[0].findtext("pitch/step"), string_and_fret(root, first)) == ("A", ("1", "5")), (own_notes(root)[0].findtext("pitch/step"), string_and_fret(root, first)))
    click_on(page, page.locator(f"[id='{first}-tab']"))
    settle(page)
    page.keyboard.type("12", delay=60)
    page.wait_for_timeout(800)
    settle(page)
    root = saved_score(base, score_id)
    check("two digits typed together are one fret", (own_notes(root)[0].findtext("pitch/octave"), string_and_fret(root, first)) == ("5", ("1", "12")), string_and_fret(root, first))
    press(page, "Control+z")
    check("and one undo", string_and_fret(saved_score(base, score_id), first) == ("1", "5"))
    click_on(page, page.locator(f"[id='{first}-tab']"))
    settle(page)
    press(page, "Control+ArrowDown")
    root = saved_score(base, score_id)
    check("Ctrl+down moves a fret number to the string below, keeping its sound", (own_notes(root)[0].findtext("pitch/step"), string_and_fret(root, first)) == ("A", ("2", "10")), string_and_fret(root, first))
    click_on(page, page.locator(f"[id='{first}-tab']"))
    settle(page)
    page.keyboard.press("Shift+Digit6")
    settle(page)
    check("Shift and a digit choose its length, as the digits are frets", own_notes(saved_score(base, score_id))[0].findtext("type") == "half")
    press(page, "Control+z")

    # Writing on the tablature: a click on a string writes it open, and a digit the next fret on that string.
    page.keyboard.press("Escape")
    rest = next(note.get("id") for note in saved_score(base, score_id).find("part").findall("measure")[1].iter("note") if (note.findtext("staff") or "1") == "1")
    page.keyboard.press("n")
    x, y = tab_point(page, rest, 3)
    page.mouse.move(x, y)
    check("the ghost on the tablature is a box on a string", page.locator("#ghost.onTab:not([hidden])").count() == 1)
    page.mouse.click(x, y)
    settle(page)
    check("the cursor is a box on the string clicked", page.locator("#caret.onTab:not([hidden])").count() == 1 and page.evaluate("() => editor.string") == 3)
    page.keyboard.press("2")
    settle(page)
    page.keyboard.press("ArrowUp")
    page.keyboard.press("3")
    settle(page)
    root = saved_score(base, score_id)
    written = [(note.findtext("pitch/step"), note.findtext("pitch/octave"), string_and_fret(root, note.get("id"))) for note in own_notes(root)[2:]]
    check("a click writes the open string, and digits frets on the cursor's string", written == [("G", "3", ("3", "0")), ("A", "3", ("3", "2")), ("D", "4", ("2", "3"))], written)
    if shots:
        page.screenshot(path=str(shots / "tab-editing.png"))
    page.keyboard.press("Escape")
    settle(page)

    # A guitar without tablature keeps a string chosen on the fretboard or in the inspector.
    plain = open_blank(page, base, created, bars=1, staff="guitar")
    show_instrument(page)
    page.locator('#fretboard .spot[data-string="2"][data-stop="5"]').click()
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    note = own_notes(saved_score(base, plain))[0].get("id")
    page.wait_for_function("id => playState.data?.events?.[id]", arg=note)
    click_on(page, page.locator(f"[id='{note}']"))
    settle(page)
    check("a guitar without tablature keeps the string a note was played on", spots(page, "fretboard", "chosen") == [[2, 5]] and page.inner_text("#stringInfo") == "String 2, fret 5", (spots(page, "fretboard", "chosen"), page.inner_text("#stringInfo")))
    page.click('#stringButtons [data-string="3"]')
    settle(page)
    check("and another can be chosen in the inspector", spots(page, "fretboard", "chosen") == [[3, 9]])

    # Tablature on its own, chosen for a new score, with its rhythm drawn on it.
    alone = open_blank(page, base, created, bars=2, staff="guitar-tab-only")
    data = score_data(base, alone)
    check("tablature on its own is a guitar of one staff", data["partStaves"] == [1] and data["fretted"][0]["tabStaff"] == 1)
    check("its rests are drawn", page.locator("#pages g.mRest, #pages g.rest").count() >= 2)
    page.keyboard.press("Shift+Digit4")
    page.keyboard.type("eg", delay=0)
    page.keyboard.press("Shift+Digit5")
    page.keyboard.press("7")
    settle(page)
    root = saved_score(base, alone)
    notes = [(note.findtext("pitch/step"), note.findtext("type"), fretted_place(note)) for note in own_notes(root)]
    check("letters and frets are written on it, at the length chosen", notes == [("E", "eighth", ("1", "0")), ("G", "eighth", ("1", "3")), ("B", "quarter", ("1", "7"))], notes)
    check("with stems and beams", page.locator("#pages g.stem").count() >= 3 and page.locator("#pages g.beam").count() >= 1)
    if shots:
        page.screenshot(path=str(shots / "tab-only.png"))
    page.keyboard.press("Escape")
    settle(page)


def fretted_place(note: ET.Element) -> tuple | None:
    for technical in note.iterfind("notations/technical"):
        if technical.find("fret") is not None:
            return (technical.findtext("string"), technical.findtext("fret"))
    return None


def test_practice(context, base: str, shots: Path | None, created: list[str]) -> None:
    page = context.new_page()
    page.add_init_script("""
        window.fakeMidi = { inputs: new Map([["key", { type: "input", state: "connected" }]]), onstatechange: null };
        navigator.requestMIDIAccess = async () => window.fakeMidi;
        window.press = number => window.fakeMidi.inputs.get("key").onmidimessage({ data: [0x90, number, 90] });
    """)
    score_id = open_blank(page, base, created, bars=2, staff="piano")
    page.keyboard.press("5")
    page.keyboard.type("cdef", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    lower = page.evaluate("""() => document.querySelector("#pages g.measure").querySelectorAll("g.staff")[1].querySelector("g.mRest, g.rest").id""")
    click_on(page, page.locator(f"[id='{lower}']"))
    settle(page)
    page.keyboard.press("n")
    settle(page)
    page.keyboard.press("6")
    page.keyboard.type("cg", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    page.keyboard.press("Escape")
    page.wait_for_function("() => playState.data && playState.data.notes.length === 6")
    page.click('#modeSwitch [data-mode="practice"]')
    check("Practice brings its own tools", page.is_visible("#practiceBar") and page.is_hidden("#noteBar") and page.is_hidden("#inspector") and page.is_visible("#dock"))
    hear = page.locator("#practiceHear option").all_inner_texts()
    check("a piano can be heard whole or one hand alone", hear == ["Everything", "Piano", "Piano, right hand", "Piano, left hand"], hear)
    staves = lambda: page.evaluate("""async () => {
        const data = await fetchPlayback();
        return schedule(new OfflineAudioContext(1, 44100, 44100), data, 0, 0, 1, Infinity, { only: hearing() }).notes.map(note => note.staff);
    }""")
    page.select_option("#practiceHear", "staff:0:1")
    check("the right hand alone plays only the upper staff", staves() == [1, 1, 1, 1], staves())
    page.select_option("#practiceHear", "staff:0:2")
    check("and the left hand the lower", staves() == [2, 2], staves())
    page.click("#rewind")
    page.click("#play")
    wait_playing(page, True)
    check("Play sounds that hand alone", page.get_attribute("#play", "data-notes") == "2", page.get_attribute("#play", "data-notes"))
    page.click("#play")
    wait_playing(page, False)
    page.select_option("#practiceHear", "all")
    page.select_option("#countIn", "1")
    page.click("#rewind")
    page.click("#play")
    wait_playing(page, True)
    lead = page.evaluate("() => player.cycles[0].zero - player.audio.currentTime")
    check("a bar counted in comes before the music: four beats at 100", 2.2 < lead < 2.8, lead)
    page.wait_for_function("() => document.querySelector('#waitState').textContent.startsWith('Count in')")
    check("and the count is shown", page.inner_text("#waitState").startswith("Count in"), page.inner_text("#waitState"))
    page.click("#play")
    wait_playing(page, False)
    page.select_option("#countIn", "0")
    select_notes(page, 1)
    page.keyboard.press("a")
    select_notes(page, 2)
    page.keyboard.press("b")
    check("A and B set the loop's ends", page.evaluate("() => [playState.loop.first, playState.loop.last, playState.looping]") == [page.locator("#pages g.note").nth(1).get_attribute("id"), page.locator("#pages g.note").nth(2).get_attribute("id"), True])
    check("and turn the loop on in both places", page.get_attribute("#practiceLoop", "aria-pressed") == "true" and page.get_attribute("#loop", "aria-pressed") == "true" and page.locator(".loopMarker").count() == 2)
    page.click("#practiceLoop")
    check("which turn it off together", page.get_attribute("#loop", "aria-pressed") == "false")
    page.locator("#practiceSpeed").fill("50")
    check("the speed slider slows the tempo shown", page.inner_text("#tempoValue") == "50" and page.inner_text("#practiceSpeedValue") == "50%")
    page.locator("#practiceSpeed").fill("100")
    page.keyboard.press("Escape")
    waits = page.locator("#waitFor option").all_inner_texts()
    check("playback can wait for the whole piano or one hand", waits == ["Nobody: just play", "Piano", "Piano, right hand", "Piano, left hand"], waits)
    page.select_option("#waitFor", "staff:0:1")
    page.wait_for_function("() => practice.midi === 'ready'")
    check("it says the MIDI keyboard is ready", "MIDI keyboard ready" in page.inner_text("#waitState"), page.inner_text("#waitState"))
    box = page.locator("#waitState").bounding_box()
    check("where it can be seen, as the practice tools wrap to fit", box is not None and box["x"] + box["width"] <= page.viewport_size["width"], box)
    check("and the hint says what Practice's keys do", page.inner_text("#toolLabel").startswith("Space plays from the selected note"), page.inner_text("#toolLabel"))
    page.click("#rewind")
    page.keyboard.press(" ")
    page.wait_for_function("() => practice.follower?.arrived")
    check("Play waits at the first of your notes", page.inner_text("#waitState") == "Play C5" and page.locator("#pages g.awaited").count() == 1, page.inner_text("#waitState"))
    check("and the keyboard shows it", page.evaluate("() => [...document.querySelectorAll('#pianoKeys .key.expected')].map(key => key.dataset.midi)") == ["72"])
    page.wait_for_timeout(400)
    check("it doesn't move on while it waits", page.evaluate("() => followerPlace()") == 0)
    page.evaluate("() => press(74)")
    check("a wrong note is named", page.inner_text("#waitState").startswith("That was D5"), page.inner_text("#waitState"))
    page.evaluate("() => press(72)")
    page.wait_for_function("() => practice.follower?.index === 1 && practice.follower.arrived")
    check("the right one on a MIDI keyboard plays on to the next", page.inner_text("#waitState") == "Play D5", page.inner_text("#waitState"))
    page.locator('#pianoKeys [data-midi="74"]').dispatch_event("pointerdown")
    page.wait_for_timeout(300)
    check("and so does the keyboard below", page.evaluate("() => practice.follower?.index") == 2, page.evaluate("() => practice.follower?.index"))
    if shots:
        page.screenshot(path=str(shots / "practice.png"))
    page.keyboard.press(" ")
    wait_playing(page, False)
    check("Space stops waiting", page.evaluate("() => practice.follower") is None and page.locator("#pages g.awaited").count() == 0)
    check("and nothing was written", len([note for note in saved_score(base, score_id).iter("note") if note.find("pitch") is not None]) == 6)
    page.select_option("#waitFor", "off")
    page.close()


def test_practice_details(context, base: str, shots: Path | None, created: list[str]) -> None:
    """A count-in comes in on the music's own beat, waiting for you says how each note fell, and a loop can speed up."""
    page = context.new_page()
    page.add_init_script("""
        window.fakeMidi = { inputs: new Map([["key", { type: "input", state: "connected" }]]), onstatechange: null };
        navigator.requestMIDIAccess = async () => window.fakeMidi;
        window.press = number => window.fakeMidi.inputs.get("key").onmidimessage({ data: [0x90, number, 90] });
        window.lift = number => window.fakeMidi.inputs.get("key").onmidimessage({ data: [0x80, number, 0] });
    """)
    page.goto(base)
    page.click("#blankOpen")
    page.fill("#newChooser input", "treble")
    page.click('#newChooser [data-instrument="treble"] button')
    page.click("#blankNext")
    page.fill("#blankBars", "2")
    page.select_option("#blankPickup", "4")
    page.click("#blankDone")
    page.wait_for_selector("#pages svg")
    score_id = page.evaluate("() => scoreId")
    created.append(score_id)
    page.click('#modeSwitch [data-mode="write"]')  # The mode an earlier test left is kept.
    if not page.evaluate("() => editor.on"):
        page.keyboard.press("n")
    page.keyboard.press("5")
    page.keyboard.type("cdefg", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    page.keyboard.press("Escape")
    page.wait_for_function("() => playState.data && playState.data.notes.length === 5")
    page.click('#modeSwitch [data-mode="practice"]')
    lead = lambda: page.evaluate("() => player.cycles[0].zero - player.audio.currentTime")
    counted = {}
    for choice in ("1", "1w"):
        page.select_option("#countIn", choice)
        page.click("#rewind")
        page.click("#play")
        wait_playing(page, True)
        counted[choice] = lead()
        page.click("#play")
        wait_playing(page, False)
    check("before a pickup a bar counted in is the beats it's missing, 1 2 3", 1.6 < counted["1"] < 2.1, counted)
    check("or a whole bar, then those beats", 4.0 < counted["1w"] < 4.5, counted)
    check("and stopping during the count takes it away", page.inner_text("#waitState") == "")
    select_notes(page, 3)
    page.select_option("#countIn", "1")
    page.click("#play")
    wait_playing(page, True)
    check("from a note on the third beat, the count is the two beats before it", 1.0 < lead() < 1.5, lead())
    page.click("#play")
    wait_playing(page, False)
    page.select_option("#countIn", "0")

    # Waiting for you: each note's timing, against the moment the music reached it.
    page.select_option("#waitFor", "part:0")
    page.wait_for_function("() => practice.midi === 'ready'")
    page.click("#rewind")
    page.keyboard.press(" ")
    page.wait_for_function("() => practice.follower?.arrived")
    page.wait_for_timeout(500)
    page.evaluate("() => press(72)")
    page.wait_for_function("() => practice.follower?.index === 1")
    first = own_notes(saved_score(base, score_id))[0].get("id")
    check("a note played after the music reached it is late, and marked so", "Late by" in page.inner_text("#timingState") and page.locator(f"[id='{first}'].late").count() == 1, page.inner_text("#timingState"))
    page.evaluate("() => lift(72)")
    page.evaluate("() => press(74)")
    page.wait_for_function("() => practice.follower?.index === 2", timeout=5000)
    page.evaluate("() => lift(74)")
    check("one played before is early, and one let go at once is short", "1 early" in page.inner_text("#timingState") and "let go early" in page.inner_text("#timingState"), page.inner_text("#timingState"))
    if shots:
        page.screenshot(path=str(shots / "practice-timing.png"))
    page.keyboard.press(" ")
    wait_playing(page, False)
    check("the counts stay in view once it stops", page.is_visible("#timingState"))
    page.select_option("#waitFor", "off")

    # A loop round one note, 60% to 100% in steps of 20%.
    select_notes(page, 1)
    page.click("#practiceLoop")
    page.locator("#practiceSpeed").fill("60")
    page.click("#speedSteps")
    page.select_option("#speedStep", "20")
    page.select_option("#speedTarget", "100")
    page.click("#play")
    wait_playing(page, True)
    page.wait_for_function("() => document.querySelector('#practiceSpeedValue').textContent === '80%'", timeout=5000)
    page.wait_for_function("() => document.querySelector('#practiceSpeedValue').textContent === '100%'", timeout=5000)
    page.wait_for_timeout(1300)
    check("each time round the loop is faster, up to the speed chosen", page.inner_text("#practiceSpeedValue") == "100%")
    page.click("#play")
    wait_playing(page, False)
    check("and stopped, it goes back to where it started", page.inner_text("#practiceSpeedValue") == "60%")
    page.click("#speedSteps")
    page.click("#practiceLoop")
    page.locator("#practiceSpeed").fill("100")
    page.close()


def test_found_by_the_bug_hunt(context, base: str, created: list[str]) -> None:
    """Things the bug hunt over the instrument workspace found, each as a person would meet it."""
    page = context.new_page()
    page.add_init_script("""
        window.fakeMidi = { inputs: new Map([["key", { type: "input", state: "connected" }]]), onstatechange: null };
        navigator.requestMIDIAccess = async () => window.fakeMidi;
        window.press = number => window.fakeMidi.inputs.get("key").onmidimessage({ data: [0x90, number, 90] });
    """)
    score_id = open_blank(page, base, created, bars=8, staff="piano")
    page.click('#modeSwitch [data-mode="write"]')
    if not page.evaluate("() => editor.on"):
        page.keyboard.press("n")
    page.keyboard.press("5")
    page.keyboard.type("cdefgabc", delay=0)
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    select_notes(page, 5)
    command(page, "staccato")
    settle(page)
    check("Enter in the command palette runs its command and nothing else", 'new-system="yes"' not in fetch_text(base, f"/api/scores/{score_id}/score.musicxml") and len(list(saved_score(base, score_id).iter("staccato"))) == 1)
    page.keyboard.press("Escape")
    page.click('#modeSwitch [data-mode="practice"]')
    page.select_option("#waitFor", "part:0")
    page.click("#rewind")
    page.keyboard.press(" ")
    page.wait_for_function("() => practice.follower?.arrived")
    press(page, "Control+z")
    page.wait_for_timeout(300)
    check("an undo while playback waits for you keeps the notes it waits for marked", page.locator("#pages g.awaited").count() >= 1)
    page.keyboard.press(" ")
    wait_playing(page, False)
    page.select_option("#waitFor", "off")
    page.click('#modeSwitch [data-mode="write"]')
    post_json(base, f"/api/scores/{score_id}/parts", {"action": "add", "names": ["violin", "guitar"]})
    page.reload()
    page.wait_for_selector("#pages svg")
    page.keyboard.press("Control+Shift+P")
    page.fill("#commandInput", "work with violin")
    offered = page.get_attribute("#commandList li:first-child", "aria-disabled")
    page.keyboard.press("Enter")
    check("the palette's instruments and practice commands can be run", offered == "false" and page.inner_text("#partName") == "Violin", (offered, page.inner_text("#partName")))
    page.click('#modeSwitch [data-mode="practice"]')
    page.select_option("#practiceHear", "part:1")
    page.click("#inspectorToggle")
    check("the inspector's button in Practice shows the inspector, in Write", page.evaluate("() => currentMode()") == "write" and page.is_visible("#inspector"))
    page.click('#modeSwitch [data-mode="read"]')
    page.keyboard.press("F6")
    check("and F6 in Read shows the library beside the score, in Write", page.evaluate("() => currentMode()") == "write" and page.is_visible("#library"))
    page.click('#modeSwitch [data-mode="practice"]')
    page.evaluate("() => { choosePart(0); $('partRemove').click(); }")
    settle(page)
    page.wait_for_function("() => score.parts.length === 2")
    check("removing the instrument above the one heard keeps hearing that one", page.evaluate("() => practice.hear") == "part:0" and page.evaluate("() => score.parts[0]") == "Violin", page.evaluate("() => [practice.hear, score.parts]"))

    # Playback: a count-in isn't heard again when the speed changes, and Stop before it starts stops it.
    page.select_option("#practiceHear", "all")
    page.select_option("#countIn", "1")
    page.click("#rewind")
    page.click("#play")
    wait_playing(page, True)
    page.wait_for_timeout(2600)
    page.locator("#practiceSpeed").fill("80")
    page.wait_for_function("() => player?.cycles.length")
    lead = page.evaluate("() => player.cycles[0].zero - player.audio.currentTime")
    check("a new speed while playing carries on without counting in again", lead < 0.5, lead)
    page.click("#play")
    wait_playing(page, False)
    page.select_option("#countIn", "0")
    page.locator("#practiceSpeed").fill("100")
    page.route("**/playback", lambda route: (page.wait_for_timeout(300), route.continue_()))
    page.click("#play")
    page.click("#play")
    page.wait_for_timeout(800)
    check("Play and Stop at once leave nothing playing", page.evaluate("() => player === null && practice.follower === null") and page.get_attribute("#play", "aria-pressed") == "false")
    page.unroute("**/playback")

    page.close()

    # Read: the end of the score is the end.
    reader = context.new_page()
    reader.set_viewport_size({"width": 700, "height": 900})
    long_id = open_blank(reader, base, created, bars=60, staff="piano")
    reader.click('#modeSwitch [data-mode="read"]')
    reader.evaluate("() => setZoom('width')")  # A page taller than the window, read down before it's turned.
    reader.keyboard.press("End")
    reader.wait_for_timeout(200)
    places = []
    for _ in range(5):
        reader.keyboard.press(" ")
        places.append(reader.evaluate("() => [$('desk').scrollLeft, $('desk').scrollTop]"))
    check("Space at the bottom of the last page stays there", places[-1][1] > 0 and places[-1] == places[-2], places)
    reader.evaluate("() => closeScore()")
    check("a score closed in Read leaves the library to open another", reader.is_visible("#library") and reader.is_visible("#blankOpen"))
    reader.evaluate("() => setMode('write')")
    reader.close()

    # A line over a page break stays clickable when only the second page is drawn again.
    pages = context.new_page()
    two = open_blank(pages, base, created, bars=120, staff="treble")
    pages.keyboard.press("7")
    pages.keyboard.type("cd" * 60, delay=0)
    settle(pages)
    pages.keyboard.press("Escape")
    settle(pages)
    ends = pages.evaluate("""() => {
        const drawn = [...document.querySelectorAll('#pages > .page')];
        return [[...drawn[0].querySelectorAll('g.note')].at(-1).id, drawn[1].querySelector('g.note').id, [...drawn[1].querySelectorAll('g.note')].at(-1).id];
    }""")
    pages.evaluate("ids => enqueue(() => send('marks', { action: 'pedal', notes: [ids[0], ids[1]], name: 'line' }))", ends)
    settle(pages)
    pages.evaluate("id => enqueue(() => send(`notes/${id}`, { action: 'repitch', step: 'F' }))", ends[2])
    settle(pages)
    check("a line over a page break is named on both pages after the second is drawn again", pages.evaluate("() => [...document.querySelectorAll('#pages g.pedal')].every(piece => piece.dataset.mark)"))
    pages.close()


def test_phone(context, base: str, shots: Path | None, created: list[str]) -> None:
    page = context.new_page()
    page.set_viewport_size({"width": 390, "height": 800})
    page.goto(base)
    new_score(page, bars=4)
    score_id = page.evaluate("() => scoreId")
    created.append(score_id)
    page.locator("#pitches button").first.click()
    settle(page)
    check("note buttons work on a phone", pitched(read_notes(base, score_id)) == ["C5"])
    overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check("nothing overflows at phone width", overflow == 0, overflow)
    size = page.locator("#pages g.note").first.bounding_box()
    check("notes are big enough to tap on a phone", size["width"] >= 8, size["width"])
    if shots:
        page.screenshot(path=str(shots / "phone.png"))
    page.keyboard.press("Escape")
    for mode in ("read", "practice"):
        page.click(f'#modeSwitch [data-mode="{mode}"]')
        overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check(f"{mode.title()} fits a phone", overflow == 0 and page.is_visible("#pages svg") and page.is_visible("#play"), overflow)
        if mode == "read":
            right = page.evaluate("() => document.querySelector('#pages .page').getBoundingClientRect().right")
            check("Read fits the page in a phone's width", right <= 390, right)
        if shots:
            page.screenshot(path=str(shots / f"phone-{mode}.png"))
    page.click('#modeSwitch [data-mode="write"]')
    page.click("#libraryToggle")
    check("on a phone the library opens over the score", page.is_visible("#library"))
    page.click("#libraryToggle")
    # A guitar's note high on the neck is brought into view on a phone's short fretboard.
    page.goto(base)
    new_score(page, "guitar-tab", bars=2)
    created.append(page.evaluate("() => scoreId"))
    page.evaluate("() => enqueue(() => send(`notes/${editor.cursor}`, { action: 'write', type: 'quarter', dots: 0, midi: 79 }))")
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    show_instrument(page)
    page.wait_for_function("() => playState.data !== null")
    page.evaluate("() => select(allEvents()[0].id)")
    settle(page)
    seen = page.evaluate("""() => {
        const spot = document.querySelector('#fretboard .spot.chosen'), panel = document.getElementById('fretboardPanel');
        const box = spot.getBoundingClientRect(), view = panel.getBoundingClientRect();
        return [Number(spot.dataset.stop), box.left >= view.left && box.right <= view.right];
    }""")
    check("a guitar note high on the neck is brought into view on a phone", seen == [15, True], seen)
    page.evaluate("() => showInspector(true)")
    overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check("and the guitarist's box fits a phone", overflow == 0 and page.is_visible("#stringButtons"), overflow)
    if shots:
        page.screenshot(path=str(shots / "phone-guitar.png"))
    page.close()


def test_recognized(page, base: str, shots: Path | None, created: list[str]) -> None:
    page.goto(base)
    page.set_input_files("#file", str(ROOT / "data" / "images" / "jazz_chart__clean.png"))
    page.wait_for_selector("#pages svg", timeout=120000)
    score_id = page.evaluate("() => scoreId")
    created.append(score_id)
    page.wait_for_selector(".box")
    boxes = page.locator(".box").count()
    chords = json.loads(fetch_text(base, f"/api/scores/{score_id}"))["chords"]
    check("a recognized sheet shows its chord boxes", boxes == len(chords) and boxes > 0, boxes)
    click_on(page, page.locator("#pages g.note").first)
    menu(page, "Add", "Measures", "Insert one measure before selection")
    settle(page)
    after = json.loads(fetch_text(base, f"/api/scores/{score_id}"))["chords"]
    check("adding a bar keeps every chord linked to its box", sum(chord["box"] is not None for chord in after) == len(chords))
    check("chord boxes survive on the page", page.locator(".box").count() == boxes)
    check("chords after the new bar moved along", after[0]["measure"] == chords[0]["measure"] + 1, (chords[0]["measure"], after[0]["measure"]))
    if shots:
        page.screenshot(path=str(shots / "recognized.png"), full_page=True)
    size = page.viewport_size
    page.set_viewport_size({"width": 390, "height": 844})
    page.click('#modeSwitch [data-mode="read"]')
    page.wait_for_timeout(200)
    check("on a phone, Read shows a sheet read from a photo without scrolling the window sideways", page.evaluate("() => document.documentElement.scrollWidth <= innerWidth") and page.is_hidden("#original"))
    if shots:
        page.screenshot(path=str(shots / "phone-read-recognized.png"))
    page.click('#modeSwitch [data-mode="write"]')
    page.set_viewport_size(size)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--shots", type=Path)
    parser.add_argument("--clean", action="store_true", help="delete the scores this test created")
    parser.add_argument("--browser", choices=["chromium", "firefox"], default="chromium")
    arguments = parser.parse_args()
    base = f"http://127.0.0.1:{arguments.port}"
    if arguments.shots:
        arguments.shots.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    try:
        with sync_playwright() as driver:
            chosen = {"executable_path": os.environ.get("XRNSHEET_BROWSER")} if arguments.browser == "chromium" else {}
            browser = getattr(driver, arguments.browser).launch(**chosen)
            context = browser.new_context(viewport={"width": 1360, "height": 900})
            page = context.new_page()
            problems: list[str] = []
            page.on("pageerror", lambda error: problems.append(str(error)))
            # A refused edit or file is shown to the person, and these tests ask for some on purpose.
            refused = ("status of 415", "status of 422")
            page.on("console", lambda item: problems.append(item.text) if item.type == "error" and not any(code in item.text for code in refused) else None)
            test_writing(page, base, arguments.shots, created)
            test_clicking_notes(page, base, arguments.shots, created)
            test_end_of_score(page, base, created)
            test_quick_keys(page, base, created)
            test_triplets(page, base, created)
            test_selection(page, base, arguments.shots, created)
            test_musescore_keys(page, base, created)
            test_measure_selection(page, base, arguments.shots, created)
            test_desk(page, base, created)
            test_time_signs(page, base, arguments.shots, created)
            test_barlines(page, base, arguments.shots, created)
            test_context_menu(page, base, created)
            test_lyrics(page, base, arguments.shots, created)
            test_titles_and_repeats(page, base, arguments.shots, created)
            test_sound(page, base, created)
            test_note_marks(page, base, arguments.shots, created)
            test_octave_and_pedal_lines(page, base, arguments.shots, created)
            test_marks_over_a_line_break(page, base, created)
            test_placed_marks(page, base, arguments.shots, created)
            test_voices(page, base, arguments.shots, created)
            test_voice_key_waits_its_turn(page, base, created)
            test_transposing(page, base, created)
            test_instruments_and_layout(page, base, arguments.shots, created)
            test_concert_pitch(page, base, arguments.shots, created)
            test_continuous_view(page, base, arguments.shots, created)
            test_one_line_keeps_its_start_in_sight(page, base, arguments.shots, created)
            test_page_settings(page, base, arguments.shots, created)
            test_more_clefs_and_instruments(page, base, arguments.shots, created)
            test_quick_entry(page, base, created)
            test_tuplets_and_verses(page, base, arguments.shots, created)
            test_instrument_sounds(page, base, created)
            test_pickup(page, base, created)
            test_audio_file(page, base, created)
            test_mixer(page, base, arguments.shots, created)
            test_piano_keyboard(page, base, arguments.shots, created)
            test_timeline(page, base, arguments.shots, created)
            test_midi_keyboard(context, base, created)
            test_opening_a_file(page, base, arguments.shots, created)
            test_sound_of_marks(page, base, created)
            test_playback_toolbar(page, base, arguments.shots, created)
            test_modes(page, base, arguments.shots, created)
            test_library(page, base, created)
            test_inspector(page, base, arguments.shots, created)
            test_command_palette(page, base, created)
            test_fretboard(page, base, arguments.shots, created)
            test_fingerboard_and_switcher(page, base, arguments.shots, created)
            test_cello_and_double_bass_fingerboards(page, base, arguments.shots, created)
            test_new_scores_for_players(page, base, arguments.shots, created)
            test_guitar_notation(page, base, arguments.shots, created)
            test_violin_notation(page, base, arguments.shots, created)
            test_piano_notation(page, base, arguments.shots, created)
            test_every_technique_mark(page, base, arguments.shots, created)
            test_techniques_at_concert_pitch(page, base, arguments.shots, created)
            test_techniques_copied_and_the_capo(page, base, arguments.shots, created)
            test_tablature_editing(page, base, arguments.shots, created)
            test_marks_drawn_on_notes(page, base, arguments.shots, created)
            test_words_copied_with_a_passage(page, base, created)
            test_chord_diagram_editing(page, base, arguments.shots, created)
            test_practice(context, base, arguments.shots, created)
            test_practice_details(context, base, arguments.shots, created)
            test_found_by_the_bug_hunt(context, base, created)
            test_phone(context, base, arguments.shots, created)
            test_recognized(page, base, arguments.shots, created)
            check("no errors in the page", not problems, problems[:4])
            browser.close()
    finally:
        print("scores created:", " ".join(created))
        if arguments.clean:
            for score_id in created:
                shutil.rmtree(ROOT / "data" / "jobs" / score_id, ignore_errors=True)
    print(f"{len(FAILURES)} failed: {FAILURES}" if FAILURES else "all passed")
    raise SystemExit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()

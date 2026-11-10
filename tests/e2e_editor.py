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


def test_writing(page, base: str, shots: Path | None, created: list[str]) -> None:
    page.goto(base)
    page.click("#blankOpen")
    page.select_option("#blankTime", "4/4")
    page.fill("#blankBars", "8")
    page.click("#blankForm button[type=submit]")
    page.wait_for_selector("#pages svg")
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
    page.click("#addBar")
    settle(page)
    check("add bar adds one at the end", bars() == 9)
    click_on(page, page.locator("#pages g.note").first)
    page.click("#deleteBar")
    settle(page)
    check("delete bar removes it", bars() == 8)
    check("the deleted bar's signature moved on", ET.fromstring(fetch_text(base, f"/api/scores/{score_id}/score.musicxml")).find("part").find("measure").findtext("attributes/divisions") is not None)

    click_on(page, page.locator("#pages g.note").first)
    page.select_option("#keyChoice", "-2")
    settle(page)
    xml = fetch_text(base, f"/api/scores/{score_id}/score.musicxml")
    check("key change is saved", "<fifths>-2</fifths>" in xml)
    check("the key picker resets", page.input_value("#keyChoice") == "")

    page.keyboard.press("Escape")
    page.select_option("#timeChoice", "3/4")
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
    page.wait_for_function("() => document.querySelector('#play').textContent === 'Stop'")
    check("space starts playback", int(page.get_attribute("#play", "data-notes")) >= 5)
    page.wait_for_selector("#pages g.playing", timeout=4000)
    check("the playing note lights up", True)
    page.keyboard.press(" ")
    check("space stops playback", page.inner_text("#play") == "Play" and page.locator("#pages g.playing").count() == 0)

    pdf = urllib.request.urlopen(f"{base}/api/scores/{score_id}/score.pdf").read()
    check("the PDF downloads", pdf[:4] == b"%PDF", len(pdf))

    kept = read_notes(base, score_id)
    page.reload()
    page.wait_for_selector("#pages svg")
    check("a reload keeps the score", read_notes(base, score_id) == kept)
    page.click("#again")
    page.wait_for_selector("#historyList a")
    check("it's listed as written from scratch", "written from scratch" in page.inner_text("#historyList"))


def open_blank(page, base: str, created: list[str], bars: int = 4, staff: str = "treble") -> str:
    page.goto(base)
    page.click("#blankOpen")
    page.select_option("#blankClef", staff)
    page.fill("#blankBars", str(bars))
    page.click("#blankForm button[type=submit]")
    page.wait_for_selector("#pages svg")
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
    check("an action moves the whole stretch", pitched(read_notes(base, score_id))[:4] == ["D5", "E5", "F5", "F5"], pitched(read_notes(base, score_id)))
    check("the selection is still there after the edit", highlighted() == 3, highlighted())
    page.keyboard.press("Control+z")
    settle(page)
    check("one undo reverses the whole stretch", pitched(read_notes(base, score_id))[:4] == ["C5", "D5", "E5", "F5"] and version)
    page.keyboard.press("Control+c")
    settle(page)
    check("copying enables paste", page.is_enabled("#paste"))
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
    check("the clip survives opening another score", page.is_enabled("#paste"))
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
    page.fill("#titleInput", "Test Song")
    page.fill("#composerInput", "A. Writer")
    page.keyboard.press("Enter")
    settle(page)
    page.locator("#composerInput").blur()
    settle(page)
    root = saved_score(base, score_id)
    check("the title is saved", root.findtext("work/work-title") == "Test Song", root.findtext("work/work-title"))
    printed = page.evaluate("""() => document.querySelector("#pages .pgHead")?.textContent ?? "" """)
    check("title and composer are printed", "Test Song" in printed and "A. Writer" in printed, printed.split())
    page.reload()
    page.wait_for_selector("#pages svg")
    check("the title is shown again after reloading", page.input_value("#titleInput") == "Test Song")

    rests = page.locator("#pages g.mRest, #pages g.rest")
    click_on(page, rests.nth(1))
    page.select_option("#repeatChoice", "backward")
    settle(page)
    click_on(page, page.locator("#pages g.mRest, #pages g.rest").nth(1))
    page.select_option("#repeatChoice", "1")
    settle(page)
    click_on(page, page.locator("#pages g.mRest, #pages g.rest").nth(2))
    page.select_option("#repeatChoice", "2")
    settle(page)
    played = json.loads(fetch_text(base, f"/api/scores/{score_id}/playback"))["total"]
    root = saved_score(base, score_id)
    check("repeats are saved", len(list(root.iter("repeat"))) == 1 and len(list(root.iter("ending"))) >= 2)
    check("the repeat plays bars 1, 2, 1, 3, 4", played == 20, played)
    check("the menu goes back to its label", page.input_value("#repeatChoice") == "")
    page.keyboard.press("Control+z")
    page.keyboard.press("Control+z")
    settle(page)
    click_on(page, page.locator("#pages g.mRest, #pages g.rest").nth(1))
    bar_three = page.locator("#pages g.mRest, #pages g.rest").nth(2).bounding_box()
    page.keyboard.down("Shift")
    page.mouse.click(bar_three["x"] + bar_three["width"] / 2, bar_three["y"] + bar_three["height"] / 2)
    page.keyboard.up("Shift")
    settle(page)
    page.select_option("#repeatChoice", "1")
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


def click_mark(page, selector: str) -> None:
    """Click a mark where a mouse would, in the middle of what's drawn."""
    box = page.evaluate("""selector => {
        const element = document.querySelector(selector);
        element.scrollIntoView({ block: "center" });
        const box = element.getBoundingClientRect();
        return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
    }""", selector)
    page.mouse.click(box["x"], box["y"])
    settle(page)


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
    page.select_option("#layoutChoice", "2")
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
    check("its second piece can be clicked too", page.is_visible("#editor") and page.is_visible("#chordDelete"))
    page.click("#chordDelete")
    settle(page)
    check("and removes the whole slur", not notes_with(saved_score(base, score_id), "notations/slur"))


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
    page.select_option("#markChoice", "accent")
    settle(page)
    check("a mark goes on a whole selection", len(notes_with(saved_score(base, score_id), "notations/articulations/accent")) == 3)
    check("the menu goes back to its label", page.input_value("#markChoice") == "")
    select_notes(page, 4)
    page.select_option("#markChoice", "fermata")
    settle(page)
    check("a fermata is drawn", page.locator("#pages g.fermata").count() == 1)
    select_notes(page, 5)
    page.select_option("#markChoice", "trill")
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
    check("a click just off a slur still finds it", page.is_visible("#editor") and page.is_visible("#chordDelete") and page.is_hidden("#chordInput"))
    page.keyboard.press("Escape")
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
    check("clicking a slur offers to remove it", page.is_visible("#editor") and page.is_visible("#chordDelete") and page.is_hidden("#chordInput"))
    page.click("#chordDelete")
    settle(page)
    check("the slur can be removed by clicking it", not notes_with(saved_score(base, score_id), "notations/slur"))

    select_notes(page, 2, 5)
    page.keyboard.press("<")
    settle(page)
    check("< draws a crescendo", page.locator("#pages g.hairpin").count() == 1)
    select_notes(page, 2, 5)
    page.keyboard.press("<")
    settle(page)
    check("the same again takes it away", page.locator("#pages g.hairpin").count() == 0)
    select_notes(page, 2, 5)
    page.click("#dimButton")
    settle(page)
    root = saved_score(base, score_id)
    check("the button draws a diminuendo", [wedge.get("type") for wedge in root.iter("wedge")] == ["diminuendo", "stop"])
    click_mark(page, "#pages g.hairpin")
    page.click("#chordDelete")
    settle(page)
    check("a hairpin can be removed by clicking it", not list(saved_score(base, score_id).iter("wedge")))

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
    page.select_option("#dynamicChoice", "p")
    settle(page)
    check("a dynamic is drawn", page.locator("#pages g.dynam").count() == 1)
    check("a drawn dynamic knows what it is", page.get_attribute("#pages g.dynam", "data-mark") is not None)
    click_mark(page, "#pages g.dynam")
    check("clicking a dynamic shows its name", page.input_value("#chordInput") == "p", page.input_value("#chordInput"))
    page.fill("#chordInput", "ff")
    page.keyboard.press("Enter")
    settle(page)
    root = saved_score(base, score_id)
    check("a dynamic can be changed where it's drawn", root.find(".//dynamics/ff") is not None)
    click_mark(page, "#pages g.dynam")
    page.fill("#chordInput", "loud")
    page.keyboard.press("Enter")
    settle(page)
    check("a nonsense dynamic is refused, and says why", page.is_visible("#editorError") and "dynamic" in page.inner_text("#editorError"))
    page.keyboard.press("Escape")
    click_mark(page, "#pages g.dynam")
    page.click("#chordDelete")
    settle(page)
    check("a dynamic can be removed by clicking it", saved_score(base, score_id).find(".//dynamics") is None)

    select_notes(page, 0)
    page.select_option("#tempoChoice", "Allegro 132")
    settle(page)
    root = saved_score(base, score_id)
    check("a tempo from the menu is saved", root.find(".//metronome/per-minute").text == "132")
    check("the tempo is drawn", page.locator("#pages g.tempo").count() == 1)
    select_notes(page, 4)
    page.select_option("#tempoChoice", "type")
    page.fill("#chordInput", "Adagio 66")
    page.keyboard.press("Enter")
    settle(page)
    check("a typed tempo is saved", [found.text for found in saved_score(base, score_id).iter("per-minute")] == ["132", "66"])

    select_notes(page, 1)
    page.select_option("#textChoice", "staff")
    page.fill("#chordInput", "solo")
    page.keyboard.press("Enter")
    settle(page)
    check("staff text is drawn", "solo" in page.inner_text("#pages"))
    click_mark(page, "#pages g.dir")
    check("clicking text shows it", page.input_value("#chordInput") == "solo")
    page.fill("#chordInput", "tutti")
    page.keyboard.press("Enter")
    settle(page)
    check("text can be changed where it's drawn", "tutti" in page.inner_text("#pages") and "solo" not in page.inner_text("#pages"))
    select_notes(page, 2)
    page.select_option("#textChoice", "rehearsal")
    settle(page)
    check("a rehearsal letter is drawn", page.locator("#pages g.reh").count() == 1)

    select_notes(page, 0)
    page.select_option("#jumpChoice", "segno")
    settle(page)
    check("a segno is drawn", page.locator("#pages g.repeatMark").count() == 1)
    click_mark(page, "#pages g.repeatMark")
    check("clicking a sign offers to remove it", page.is_visible("#chordDelete") and page.is_hidden("#chordInput"))
    page.click("#chordDelete")
    settle(page)
    check("a sign can be removed by clicking it", page.locator("#pages g.repeatMark").count() == 0)
    select_notes(page, 7)
    page.select_option("#jumpChoice", "dc")
    settle(page)
    check("a jump is drawn", "D.C." in page.inner_text("#pages"))

    select_notes(page, 3)
    page.select_option("#clefChoice", "bass")
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
    page.click("#addChord")
    page.fill("#chordInput", "Cmaj7")
    page.keyboard.press("Enter")
    settle(page)
    page.keyboard.press("Escape")
    settle(page)
    page.select_option("#transposeChoice", "key:2")
    settle(page)
    root = saved_score(base, score_id)
    check("the whole score moves to D", root.find(".//fifths").text == "2")
    check("its notes move with it", [note.findtext("pitch/step") for note in root.iter("note") if note.find("pitch") is not None][:4] == list("DEFG"))
    check("its chords move with it", page.evaluate("() => score.chords[0].text") == "Dmaj7", page.evaluate("() => score.chords[0].text"))
    page.keyboard.press("Control+z")
    settle(page)
    check("one undo takes it all back", saved_score(base, score_id).find(".//fifths").text == "0")
    select_notes(page, 1, 2)
    page.select_option("#transposeChoice", "M2:up")
    settle(page)
    root = saved_score(base, score_id)
    check("a selection moves alone", [note.findtext("pitch/step") for note in root.iter("note") if note.find("pitch") is not None][:4] == list("CEFF"))
    check("the key stays", root.find(".//fifths").text == "0")


def test_instruments_and_layout(page, base: str, shots: Path | None, created: list[str]) -> None:
    score_id = written_melody(page, base, created, bars=12, tune="cdef")
    page.select_option("#partChoice", "trumpet")
    settle(page)
    root = saved_score(base, score_id)
    check("an instrument adds a part", len(root.findall("part")) == 2)
    check("its bars match", len(root.findall("part")[1].findall("measure")) == 12)
    check("a B flat trumpet reads two sharps more", root.findall("part")[1].find(".//fifths").text == "2")
    check("both staves are drawn", page.locator("#pages g.measure").first.locator("g.staff").count() == 2)
    check("a part can be printed", page.is_visible("#partPdf"))
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
    page.select_option("#partChoice", "remove")
    settle(page)
    check("an instrument can be removed", len(saved_score(base, score_id).findall("part")) == 1)

    bars_in_lines = """() => [...document.querySelectorAll("#pages g.system")].map(system => system.querySelectorAll("g.measure").length)"""
    page.select_option("#layoutChoice", "4")
    settle(page)
    check("bars can be set to 4 a line", page.evaluate(bars_in_lines) == [4, 4, 4], page.evaluate(bars_in_lines))
    page.select_option("#layoutChoice", "0")
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
    page.select_option("#timeChoice", "pickup:4")
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
        page.click("#audio")
    check("the audio file has a sensible name", download.value.suggested_filename == "score.wav")
    with wave.open(str(download.value.path())) as sound:
        rate, channels, width, length = sound.getframerate(), sound.getnchannels(), sound.getsampwidth(), sound.getnframes()
        frames = sound.readframes(length)
    samples = array.array("h", frames)
    tempo = page.evaluate("async () => (await (await fetch(`/api/scores/${scoreId}/playback`)).json()).tempo")
    beat = 60 / tempo
    check("it's a plain WAV another program can read", (rate, channels, width) == (44100, 1, 2), (rate, channels, width))
    check("it lasts as long as the score", abs(length / rate - (8 * beat + 0.5)) < 0.01, length / rate)
    check("the notes can be heard", max(abs(sample) for sample in samples[: int(rate * beat * 3)]) > 3000)
    check("the empty bar is silent", max(abs(sample) for sample in samples[int(rate * beat * 4.5) :]) == 0)
    turned_down = page.evaluate("""async () => {
        const file = new DataView(await wavFile(Float32Array.of(0.5, 2, -1), 44100).arrayBuffer());
        return [file.getInt16(44, true), file.getInt16(46, true), file.getInt16(48, true)];
    }""")
    check("a sound too loud for the file is turned down, not wrapped round", turned_down == [8191, 32767, -16383], turned_down)


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
    page.select_option("#dynamicChoice", "mf")
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
    page.select_option("#tempoChoice", "type")
    page.fill("#chordInput", "60")
    page.keyboard.press("Enter")
    settle(page)
    select_notes(page, 0)
    page.select_option("#dynamicChoice", "pp")
    settle(page)
    select_notes(page, 4)
    page.select_option("#dynamicChoice", "ff")
    settle(page)
    select_notes(page, 2)
    page.select_option("#tempoChoice", "type")
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
    page.select_option("#repeatChoice", "backward")
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


def test_phone(context, base: str, shots: Path | None, created: list[str]) -> None:
    page = context.new_page()
    page.set_viewport_size({"width": 390, "height": 800})
    page.goto(base)
    page.click("#blankOpen")
    page.fill("#blankBars", "4")
    page.click("#blankForm button[type=submit]")
    page.wait_for_selector("#pages svg")
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
    page.click("#addBar")
    settle(page)
    after = json.loads(fetch_text(base, f"/api/scores/{score_id}"))["chords"]
    check("adding a bar keeps every chord linked to its box", sum(chord["box"] is not None for chord in after) == len(chords))
    check("chord boxes survive on the page", page.locator(".box").count() == boxes)
    check("chords after the new bar moved along", after[0]["measure"] == chords[0]["measure"] + 1, (chords[0]["measure"], after[0]["measure"]))
    if shots:
        page.screenshot(path=str(shots / "recognized.png"), full_page=True)


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
            test_lyrics(page, base, arguments.shots, created)
            test_titles_and_repeats(page, base, arguments.shots, created)
            test_sound(page, base, created)
            test_note_marks(page, base, arguments.shots, created)
            test_marks_over_a_line_break(page, base, created)
            test_placed_marks(page, base, arguments.shots, created)
            test_voices(page, base, arguments.shots, created)
            test_voice_key_waits_its_turn(page, base, created)
            test_transposing(page, base, created)
            test_instruments_and_layout(page, base, arguments.shots, created)
            test_quick_entry(page, base, created)
            test_tuplets_and_verses(page, base, arguments.shots, created)
            test_instrument_sounds(page, base, created)
            test_pickup(page, base, created)
            test_audio_file(page, base, created)
            test_midi_keyboard(context, base, created)
            test_opening_a_file(page, base, arguments.shots, created)
            test_sound_of_marks(page, base, created)
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

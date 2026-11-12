"""Draw what Verovio leaves out of guitar and string notation, and name the marks it draws without ids.

Verovio draws MusicXML's fingerings, slides, glissandos, vibratos, harmonics' circles, and the words
and dashed lines over a passage. It leaves out p-i-m-a, string numbers, hammer-ons and pull-offs,
bends, dead notes and harmonics on a tablature staff, and an artificial harmonic's A.H. So:

- Before it draws (prepare), a pluck and a string number become fingerings, which it places as it
  places any fingering, a hammer-on or pull-off becomes a slur from one note to the next, on the
  standard staff and on the tablature, and the tablature's fret numbers are joined by the slides
  and glissandos of the notes they copy, and a guitar with a capo has "Capo 2" over its start (its
  drawing is then named as the app's, not one of the score's marks). This is only for the drawing;
  the score keeps its marks.
- After it draws (decorate), a string number's fingering is put in a circle on a guitar, a pluck is
  set in italics, an H or P goes over each hammer-on's or pull-off's slur on the tablature, or on
  the staff when there's no tablature, a bend gets its arrow
  and amount, and a fret number on the tablature becomes an X for a dead note or <12> for a
  harmonic.

Each mark a click can name carries its id in a data-mark attribute, as the page's own marks do, so
a click on a fingering, a string number, a bend, a hammer-on, a slide, a vibrato, an articulation,
a bowing or a harmonic selects it and Delete removes it. Verovio makes up its own ids for most of
them: fingerings and vibratos are matched to the score's in the order each bar draws them, and left
unnamed in a bar where the counts differ, and articulations and bowings by the symbol drawn on
each note. A slide or glissando keeps its own id, and its copy on the tablature is named after it.
"""

import re
import xml.etree.ElementTree as ET
from html import escape

from pipeline.fretted import Fretboard, boards, tab_staves
from pipeline.marks import BEND_NAMES
from pipeline.rhythm import NOTE_PAIRS, TAB_COPY
from pipeline.technique import BOWED, family_of, harmonic_kind

JOINED = {"hammer-on": "H", "pull-off": "P"}
DRAWN_SLUR = "-slur"  # A hammer-on's slur on the standard staff is named after it with this on the end.
DRAWN_TAB_SLUR = "-tabslur"
DRAWN_TAB_JOINT = "-tabjoint"  # A slide's or a glissando's copy between the fret numbers.
HARMONIC_MARK = "xrn-harmonic"  # The id of an artificial harmonic the drawing leaves off its note.
# The music font's symbols for marks drawn on a note, and the marks they are (see marks.ON_NOTES).
ARTICULATION_GLYPHS = {
    "E4A0": "accent", "E4A1": "accent", "E4A2": "staccato", "E4A3": "staccato", "E4A4": "tenuto", "E4A5": "tenuto",
    "E4A6": "staccatissimo", "E4A7": "staccatissimo", "E4A8": "staccatissimo", "E4A9": "staccatissimo",
    "E4AC": "strong-accent", "E4AD": "strong-accent", "E5E5": "stopped", "E5E7": "open-string",
    "E610": "down-bow", "E611": "down-bow", "E612": "up-bow", "E613": "up-bow", "E614": "harmonic",
}
HIGHEST_SLUR_NUMBER = 16  # MusicXML numbers slurs to 16. Drawn ones count down from there, away from the score's own.
FIRST_DRAWN_SLUR = 7
ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
NOTE_GROUP = re.compile(r'<g id="([^"]+)" class="note">')
HEAD_AT = re.compile(r'translate\((-?\d+), (-?\d+)\)')
TEXT_AT = re.compile(r'<text x="(-?\d+)" y="(-?\d+)"[^>]*>\s*<tspan font-size="(\d+)px">([^<]*)</tspan>')
MEASURE_GROUP = re.compile(r'<g id="([^"]+)" class="measure">')
FING_GROUP = re.compile(r'<g id="([^"]+)" class="fing">')
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
HEAD_WIDTH = 1.25  # A notehead's width, in staff spaces.
DIGIT_CENTRE = 0.39  # How far above its baseline a digit's middle is, as a share of its size.


def drawn_notes(svg: str) -> dict[str, tuple[float, float, float | None]]:
    """Find where each note is drawn: its middle, and the size of its fret number if it's on a tablature staff."""
    found = {}
    for match in NOTE_GROUP.finditer(svg):
        piece = svg[match.end(): match.end() + 800]
        head, text = HEAD_AT.search(piece), TEXT_AT.search(piece)
        if text is not None and (head is None or text.start() < head.start()):
            size = float(text.group(3))
            found[match.group(1)] = (float(text.group(1)), float(text.group(2)) - DIGIT_CENTRE * size, size)
        elif head is not None:
            found[match.group(1)] = (float(head.group(1)), float(head.group(2)), None)
    return found


def pairs_of(part: ET.Element, tag: str, group: str) -> list[tuple[ET.Element, ET.Element, ET.Element]]:
    """Return each mark joining two notes of a part, as its start, its first note and its last note."""
    found = []
    waiting: dict[str, tuple[ET.Element, ET.Element]] = {}
    for note in part.iter("note"):
        for mark in note.iterfind(f"notations/{group}/{tag}" if group else f"notations/{tag}"):
            number = mark.get("number", "1")
            if mark.get("type") == "start":
                waiting[number] = (mark, note)
            elif mark.get("type") == "stop" and number in waiting:
                start, first = waiting.pop(number)
                found.append((start, first, note))
    return found


def notes_by_id(part: ET.Element) -> dict[str, ET.Element]:
    return {note.get("id"): note for note in part.iter("note") if note.get("id")}


def add_slur(note: ET.Element, kind: str, number: int, name: str | None = None) -> None:
    add_joint(note, "slur", kind, number, attributes={"id": name} if name else {})


def add_joint(note: ET.Element, tag: str, kind: str, number: int, line: str | None = None, attributes: dict | None = None) -> None:
    """Join a note to another in the drawing, numbered apart from the score's own marks of that kind."""
    notations = note.find("notations")
    if notations is None:
        notations = ET.SubElement(note, "notations")  # Order matters to nothing reading only the drawing.
    notations.append(ET.Element(tag, {"type": kind, "number": str(number), **({"line-type": line} if line else {}), **(attributes or {})}))


DRAWN_AS = "xrn-mark"  # What a fingering the drawing made stands for, kept on it for after it's drawn.


def as_fingering(mark: ET.Element, text: str, placement: str, kind: str) -> ET.Element:
    made = ET.Element("fingering", {"placement": placement, DRAWN_AS: kind, **({"id": mark.get("id")} if mark.get("id") else {})})
    made.text = text
    return made


def drawn_slur_numbers():
    """Hand out the numbers drawn slurs take, from the top of MusicXML's sixteen down, and round again."""
    while True:
        yield from range(HIGHEST_SLUR_NUMBER, FIRST_DRAWN_SLUR - 1, -1)


def prepare(root: ET.Element) -> None:
    """Turn marks Verovio doesn't draw into ones it does, for the drawing alone."""
    for part in root.findall("part"):
        tabs = set(tab_staves(part))
        bowed = family_of(root, part) == BOWED
        stopped = None  # The id of the last stopped note's artificial harmonic, for the diamond touched above it.
        for note in part.iter("note"):
            if (note.findtext("staff") or "1") in tabs:
                continue
            kind, which = harmonic_kind(note)
            if kind == "artificial":
                # A violin's diamond, or a guitar's A.H., says it's a harmonic, without a circle on each note.
                note.set(DRAWN_AS, which or kind)
                for technical in note.iterfind("notations/technical"):
                    for harmonic in technical.findall("harmonic"):
                        if which != "touching-pitch":
                            stopped = harmonic.get("id")
                        technical.remove(harmonic)
                if stopped:
                    note.set(HARMONIC_MARK, stopped)
            for technical in note.iterfind("notations/technical"):
                has_fret = technical.find("fret") is not None
                for position, mark in enumerate(list(technical)):
                    text = (mark.text or "").strip()
                    if mark.tag == "pluck":
                        replacement = as_fingering(mark, text, mark.get("placement", "below"), "pluck")
                    elif mark.tag == "string" and not has_fret:
                        roman = bowed and text.isdigit() and int(text) < len(ROMAN)
                        replacement = as_fingering(mark, ROMAN[int(text)] if roman else text, "above", "roman" if roman else "string")
                    else:
                        continue
                    technical.remove(mark)
                    technical.insert(position, replacement)
        copies = notes_by_id(part)
        numbers = drawn_slur_numbers()
        for tag in (*JOINED, "slide", "glissando"):
            for start, first, last in pairs_of(part, tag, NOTE_PAIRS[tag]):
                name = start.get("id") or ""
                if tag in JOINED:
                    number = next(numbers)
                    add_slur(first, "start", number, name + DRAWN_SLUR if name else None)
                    add_slur(last, "stop", number)
                first_copy, last_copy = copies.get((first.get("id") or "") + TAB_COPY), copies.get((last.get("id") or "") + TAB_COPY)
                if first_copy is not None and last_copy is not None and first_copy.find("pitch") is not None and last_copy.find("pitch") is not None:
                    # A number of its own: two slurs of one part open with one number across a barline
                    # are ended at random, and drawn from one staff to the other.
                    number = next(numbers)
                    if tag in JOINED:
                        add_slur(first_copy, "start", number, name + DRAWN_TAB_SLUR if name else None)
                        add_slur(last_copy, "stop", number)
                    else:
                        add_joint(first_copy, tag, "start", number, start.get("line-type", "solid"), {"id": name + DRAWN_TAB_JOINT} if name else None)
                        add_joint(last_copy, tag, "stop", number)


def boards_of(root: ET.Element, part: ET.Element) -> Fretboard | None:
    return boards(root)[root.findall("part").index(part)]


def group_end(svg: str, start: int) -> int:
    """Return where the <g> opening at this point closes, just after its </g>.

    Verovio writes some empty groups as one tag, <g ... />, which opens nothing.
    """
    depth, position = 0, start
    while True:
        opening, closing = svg.find("<g", position), svg.find("</g>", position)
        if closing < 0:
            return len(svg)
        if 0 <= opening < closing:
            tag_end = svg.find(">", opening)
            if svg[tag_end - 1] != "/":
                depth += 1
            position = tag_end + 1
        else:
            depth -= 1
            position = closing + 4
            if depth == 0:
                return position


def tag_fingerings(svg: str, root: ET.Element) -> tuple[str, dict[str, str]]:
    """Put the score's ids on Verovio's fingerings, bar by bar in the order they're drawn, and say which is which."""
    expected: dict[str, list[ET.Element]] = {}
    top = [measure.get("id") for measure in root.find("part").findall("measure")]
    for part in root.findall("part"):
        for index, measure in enumerate(part.findall("measure")):
            if index < len(top):
                # A chord diagram's dots can carry fingers too, which Verovio doesn't draw.
                expected.setdefault(top[index], []).extend(found for note in measure.iter("note") for found in note.iter("fingering"))
    starts = [(match.start(), match.group(1)) for match in MEASURE_GROUP.finditer(svg)]
    drawn: dict[str, list[re.Match]] = {}
    for match in FING_GROUP.finditer(svg):
        bar = next((name for position, name in reversed(starts) if position < match.start()), None)
        if bar is not None:
            drawn.setdefault(bar, []).append(match)
    texts = {}
    renamed = []
    for bar, matches in drawn.items():
        wanted = expected.get(bar, [])
        if len(wanted) != len(matches):
            continue
        for match, mark in zip(matches, wanted):
            if mark.get("id"):
                renamed.append((match, mark.get("id")))
                texts[mark.get("id")] = (mark.text or "").strip()
    for match, mark_id in sorted(renamed, key=lambda item: item[0].start(), reverse=True):
        svg = svg[: match.end() - 1] + f' data-mark="{escape(mark_id)}"' + svg[match.end() - 1:]
    return svg, texts


def circle_strings(svg: str, string_marks: set[str], space: float) -> str:
    """Put a guitar's string numbers in circles, around the digit Verovio drew as a fingering."""
    for mark_id in string_marks:
        found = re.search(rf'<g id="[^"]+" class="fing" data-mark="{re.escape(mark_id)}">\s*<text x="(-?\d+)" y="(-?\d+)"[^>]*>\s*<tspan[^>]*>\s*<tspan font-size="(\d+)px">', svg)
        if found is None:
            continue
        x, y, size = float(found.group(1)), float(found.group(2)), float(found.group(3))
        circle = f'<circle cx="{x:.0f}" cy="{y - DIGIT_CENTRE * size:.0f}" r="{0.62 * size:.0f}" fill="none" stroke="currentColor" stroke-width="{0.06 * space:.0f}" />'
        end = group_end(svg, found.start())
        svg = svg[: end - 4] + circle + svg[end - 4:]
    return svg


def italic_plucks(svg: str, pluck_marks: set[str]) -> str:
    for mark_id in pluck_marks:
        svg = re.sub(rf'(<g id="[^"]+" class="fing" data-mark="{re.escape(mark_id)}">\s*<text )', r'\1font-style="italic" ', svg)
    return svg


def replace_fret(svg: str, copy_id: str, text: str) -> str:
    """Write something else in place of a fret number on the tablature."""
    found = re.search(rf'<g id="{re.escape(copy_id)}" class="note">\s*<text [^>]*>\s*<tspan font-size="\d+px">([^<]*)</tspan>', svg)
    if found is None:
        return svg
    return svg[: found.start(1)] + escape(text) + svg[found.end(1):]


def letter(x: float, y: float, text: str, space: float, mark_id: str | None, kind: str, italic: bool = False) -> str:
    style = ' font-style="italic"' if italic else ""
    named = f' data-mark="{escape(mark_id)}"' if mark_id else ""
    return f'<g class="xrn-{kind}"{named}><text x="{x:.0f}" y="{y:.0f}" font-size="{1.6 * space:.0f}px" text-anchor="middle"{style}>{escape(text)}</text></g>'


def slur_letter(svg: str, slur_id: str, text: str, space: float, mark_id: str) -> tuple[str, str]:
    """Name a drawn slur after its hammer-on, and return an H or P to go over its middle."""
    found = re.search(rf'<g id="{re.escape(slur_id)}" class="slur">\s*<path d="([^"]+)"', svg)
    if found is None:
        return svg, ""
    points = [float(value) for value in NUMBER.findall(found.group(1))]
    xs, ys = points[0::2], points[1::2]
    above = min(ys) < min(ys[0], ys[3] if len(ys) > 3 else ys[-1])
    middle = (xs[0] + (xs[3] if len(xs) > 3 else xs[-1])) / 2
    y = min(ys) - 0.4 * space if above else max(ys) + 1.4 * space
    named = svg[: found.start()] + f'<g id="{slur_id}" class="slur" data-mark="{escape(mark_id)}">' + svg[found.start() + len(f'<g id="{slur_id}" class="slur">'):]
    return named, letter(middle, y, text, space, mark_id, "joined")


def bend_arrow(x: float, y: float, alter: str, space: float, mark_id: str, on_tab: bool, size: float | None) -> str:
    """Draw a bend: a curve up and to the right ending in an arrowhead, with how far it bends over it."""
    start_x = x + (0.45 * size if on_tab and size else HEAD_WIDTH * space * 0.9)
    start_y = y if not on_tab else y - 0.1 * space
    end_x, end_y = start_x + 1.2 * space, start_y - 3.0 * space
    stroke = 0.12 * space
    head = 0.35 * space
    path = f'M{start_x:.0f} {start_y:.0f} Q{end_x:.0f} {start_y:.0f} {end_x:.0f} {end_y + head:.0f}'
    arrow = f'M{end_x - head * 0.7:.0f} {end_y + head:.0f} L{end_x:.0f} {end_y:.0f} L{end_x + head * 0.7:.0f} {end_y + head:.0f} Z'
    label = BEND_NAMES.get(alter, alter)
    return (f'<g class="xrn-bend" data-mark="{escape(mark_id)}"><path d="{path}" fill="none" stroke="currentColor" stroke-width="{stroke:.0f}" />'
            f'<path d="{arrow}" fill="currentColor" />'
            f'<text x="{end_x:.0f}" y="{end_y - 0.3 * space:.0f}" font-size="{1.5 * space:.0f}px" text-anchor="middle">{escape(label)}</text></g>')


def harmonic_of(note: ET.Element) -> str | None:
    """Say what harmonic a drawn note is: natural, a guitar's artificial one, or a violin's stopped or touched note."""
    return note.get(DRAWN_AS) or harmonic_kind(note)[0]


def harmonic_text(note: ET.Element, copy: ET.Element) -> str | None:
    """Write a harmonic on the tablature: <12> for a natural one, and 5<17> for an artificial one touched twelve frets up."""
    kind = harmonic_of(note)
    fret = copy.findtext("notations/technical/fret")
    if kind is None or fret is None:
        return None
    return f"<{fret}>" if kind == "natural" else f"{fret}<{int(fret) + 12}>"


CAPO_WORDS = "Capo {}"
CAPO_LABELS = "xrn-capos"  # How many "Capo 2" words the drawing was given, kept on the score for after it's drawn.
DRAWN_DIRECTION = re.compile(r'<g id="[^"]+" class="dir">(?:(?!</g>).)*?>(Capo \d+)</', re.S)


def say_capo(root: ET.Element) -> None:
    """Put "Capo 2" over the start of a guitar with a capo, as words Verovio leaves room for.

    The words are counted on the score, as the tablature's capo is taken out of the drawing before
    the words are named (see name_capo).
    """
    said = 0
    for part, board in zip(root.findall("part"), boards(root)):
        measure = part.find("measure")
        if board is None or not board.capo or measure is None:
            continue
        said += 1
        direction = ET.Element("direction", placement="above")
        ET.SubElement(ET.SubElement(direction, "direction-type"), "words").text = CAPO_WORDS.format(board.capo)
        if board.staves > 1:
            ET.SubElement(direction, "staff").text = board.source or "1"
        first = next((index for index, child in enumerate(measure) if child.tag == "note"), len(measure))
        measure.insert(first, direction)
    if said:
        root.set(CAPO_LABELS, str(said))


def name_capo(svg: str, root: ET.Element) -> str:
    """Name each "Capo 2" as the app's own words, so the page doesn't take it for one of the score's marks.

    Every guitar with a capo has its own, and they're the first such words drawn.
    """
    left = int(root.get(CAPO_LABELS) or 0)
    if not left:
        return svg

    def renamed(found: re.Match) -> str:
        nonlocal left
        if not left:
            return found.group(0)
        left -= 1
        return found.group(0).replace('class="dir"', 'class="xrn-capo"', 1)

    return DRAWN_DIRECTION.sub(renamed, svg)


REST_GROUP = re.compile(r'<g id="([^"]+)" class="(?:rest|mRest)"')
STAFF_GROUP = re.compile(r'<g id="[^"]+" class="staff">')


def name_tab_rests(svg: str, root: ET.Element) -> str:
    """Give the rests of tablature on its own the score's ids, as Verovio makes up its own there.

    A click on one, or the cursor on one while entering notes, then finds it as it finds any rest.
    They're matched in the order each measure draws them, and left as they are where the counts differ.
    """
    alone = []
    first = 0
    for part, board in zip(root.findall("part"), boards(root)):
        staves = int(part.findtext("measure/attributes/staves") or 1)
        if board is not None and board.tab and not board.linked:
            alone.extend((first + int(staff) - 1, part, staff) for staff in board.tab)
        first += staves
    if not alone:
        return svg
    bars = {measure.get("id"): index for index, measure in enumerate(root.find("part").findall("measure"))}
    renamed = []
    for match in MEASURE_GROUP.finditer(svg):
        index = bars.get(match.group(1))
        if index is None:
            continue
        end = group_end(svg, match.start())
        staves = []
        position = match.end()
        while (found := STAFF_GROUP.search(svg, position, end)) is not None:
            staves.append((found.start(), group_end(svg, found.start())))
            position = staves[-1][1]
        for number, part, staff in alone:
            measures = part.findall("measure")
            if number >= len(staves) or index >= len(measures):
                continue
            wanted = [note.get("id") for note in measures[index].iter("note") if note.find("rest") is not None and (note.findtext("staff") or "1") == staff and note.get("print-object") != "no" and note.get("id")]
            drawn = list(REST_GROUP.finditer(svg, *staves[number]))
            if len(drawn) == len(wanted):
                renamed.extend(zip(drawn, wanted))
    for found, note_id in sorted(renamed, key=lambda item: item[0].start(), reverse=True):
        svg = svg[: found.start(1)] + escape(note_id) + svg[found.end(1):]
    return svg


def named(svg: str, found: re.Match, mark_id: str) -> str:
    """Put a mark's id on the group a match opens, as data-mark."""
    close = svg.find(">", found.start())
    close -= 1 if svg[close - 1] == "/" else 0
    return svg[:close] + f' data-mark="{escape(mark_id)}"' + svg[close:]


GLISS_GROUP = re.compile(r'<g id="([^"]+)" class="gliss[^"]*"')
TRILL_GROUP = re.compile(r'<g id="[^"]+" class="trill"')
OWNER_GROUP = re.compile(r'<g id="([^"]+)" class="(note|chord)"')
ARTIC_GROUP = re.compile(r'<g id="[^"]+" class="artic[^"]*"')
GLYPH = re.compile(r'xlink:href="#(E[0-9A-F]{3})-')


def name_joins(svg: str, root: ET.Element) -> str:
    """Name each slide and glissando, which keep their ids, and their copies between the fret numbers."""
    from pipeline.marks import JOINS

    starts = {mark.get("id") for note in root.iter("note") for tag in JOINS for mark in note.iterfind(f"notations/{tag}") if mark.get("type") == "start" and mark.get("id")}
    for found in reversed(list(GLISS_GROUP.finditer(svg))):
        mark_id = found.group(1).removesuffix(DRAWN_TAB_JOINT)
        if mark_id in starts:
            svg = named(svg, found, mark_id)
    return svg


def name_vibratos(svg: str, root: ET.Element) -> str:
    """Name each vibrato's wavy line, in the order each bar draws them, as Verovio makes up their ids.

    A trill is drawn the same way, so it's counted, and left unnamed.
    """
    expected: dict[str, list[str | None]] = {}
    top = [measure.get("id") for measure in root.find("part").findall("measure")]
    for part in root.findall("part"):
        for index, measure in enumerate(part.findall("measure")):
            if index >= len(top):
                continue
            for note in measure.iter("note"):
                for ornaments in note.iterfind("notations/ornaments"):
                    if ornaments.find("trill-mark") is not None:
                        expected.setdefault(top[index], []).append(None)
                    else:
                        expected.setdefault(top[index], []).extend(mark.get("id") for mark in ornaments.findall("wavy-line") if mark.get("type") == "start")
    if not any(expected.values()):
        return svg
    renamed = []
    for bar in MEASURE_GROUP.finditer(svg):
        wanted = expected.get(bar.group(1), [])
        drawn = list(TRILL_GROUP.finditer(svg, bar.end(), group_end(svg, bar.start())))
        if wanted and len(drawn) == len(wanted):
            renamed.extend((found, mark_id) for found, mark_id in zip(drawn, wanted) if mark_id)
    for found, mark_id in sorted(renamed, key=lambda item: item[0].start(), reverse=True):
        svg = named(svg, found, mark_id)
    return svg


def name_articulations(svg: str, root: ET.Element) -> str:
    """Name the articulations, bowings and harmonics drawn on each note or chord, by the symbol each is drawn with.

    A violin's artificial harmonic is named on the diamond touched above its note.
    """
    from pipeline.marks import ON_NOTES

    marks_of: dict[str, dict[str, str | None]] = {}  # By note, each kind of mark's id, or None where it's there twice.
    diamonds = {}
    for note in root.iter("note"):
        if note.get(DRAWN_AS) == "touching-pitch" and note.get(HARMONIC_MARK):
            diamonds[note.get("id")] = note.get(HARMONIC_MARK)
        for holder in [*note.iterfind("notations/articulations"), *note.iterfind("notations/technical")]:
            for mark in holder:
                if mark.tag in ON_NOTES and mark.get("id"):
                    kinds = marks_of.setdefault(note.get("id"), {})
                    kinds[mark.tag] = None if mark.tag in kinds else mark.get("id")
    if not marks_of and not diamonds:
        return svg
    owners = [(found, group_end(svg, found.start())) for found in OWNER_GROUP.finditer(svg)]
    renamed = []
    for owner, end in owners:
        if owner.group(2) == "note" and owner.group(1) in diamonds:
            renamed.append((owner, diamonds[owner.group(1)]))
        inner = [found for found, _ in owners if owner.start() < found.start() < end]
        kinds: dict[str, str | None] = {}
        for note_id in [owner.group(1), *(found.group(1) for found in inner)] if owner.group(2) == "chord" else [owner.group(1)]:
            for tag, mark_id in marks_of.get(note_id, {}).items():
                kinds[tag] = None if tag in kinds else mark_id
        if not kinds:
            continue
        for artic in ARTIC_GROUP.finditer(svg, owner.end(), end):
            # An articulation of a note inside a chord is the chord's, and is named once, there.
            if owner.group(2) == "chord" or not any(found.start() < artic.start() < found_end for found, found_end in owners if owner.start() < found.start() < end):
                glyphs = GLYPH.findall(svg, artic.end(), group_end(svg, artic.start()))
                tag = ARTICULATION_GLYPHS.get(glyphs[0]) if len(glyphs) == 1 else None
                if tag and kinds.get(tag):
                    renamed.append((artic, kinds[tag]))
    for found, mark_id in sorted(renamed, key=lambda item: item[0].start(), reverse=True):
        svg = named(svg, found, mark_id)
    return svg


def decorate(svg: str, root: ET.Element, space: float) -> str:
    """Draw a page's guitar and string marks that Verovio left out. `space` is a staff space in the drawing's units."""
    if "<g" not in svg:
        return svg
    svg = name_tab_rests(svg, root)
    svg = name_capo(svg, root)
    svg = name_joins(svg, root)
    svg = name_vibratos(svg, root)
    svg = name_articulations(svg, root)
    svg, texts = tag_fingerings(svg, root)
    places = drawn_notes(svg)
    pieces: list[str] = []
    circled, plucked = set(), set()
    for part in root.findall("part"):
        tabs = set(tab_staves(part))
        copies = notes_by_id(part)
        for note in part.iter("note"):
            if (note.findtext("staff") or "1") in tabs or not note.get("id"):
                continue
            note_id = note.get("id")
            copy = copies.get(note_id + TAB_COPY)
            for technical in note.iterfind("notations/technical"):
                for mark in technical.findall("fingering"):
                    if mark.get("id") in texts and mark.get(DRAWN_AS) == "string":
                        circled.add(mark.get("id"))
                    elif mark.get("id") in texts and mark.get(DRAWN_AS) == "pluck":
                        plucked.add(mark.get("id"))
                for bend in technical.findall("bend"):
                    on_tab = copy is not None and copy.get("id") in places
                    where = places.get(copy.get("id")) if on_tab else places.get(note_id)
                    if where is not None:
                        pieces.append(bend_arrow(where[0], where[1], bend.findtext("bend-alter") or "", space, bend.get("id") or "", on_tab, where[2]))
            if copy is not None and copy.find("pitch") is not None:
                if note.findtext("notehead") == "x":
                    svg = replace_fret(svg, copy.get("id"), "X")
                elif (text := harmonic_text(note, copy)) is not None:
                    svg = replace_fret(svg, copy.get("id"), text)
            if harmonic_of(note) == "artificial" and note_id in places:
                x, y, _ = places[note_id]
                pieces.append(letter(x + HEAD_WIDTH * space / 2, y - 2.2 * space, "A.H.", space, note.get(HARMONIC_MARK), "harmonic", italic=True))
        for tag, text in JOINED.items():
            for start, first, last in pairs_of(part, tag, NOTE_PAIRS[tag]):
                if not start.get("id"):
                    continue
                # The letter goes on the tablature, as MuseScore has it, or on the staff where the
                # tablature has no slur: there's none, or a note of the two no string can play.
                ends = [copies.get((note.get("id") or "") + TAB_COPY) for note in (first, last)]
                lettered = DRAWN_TAB_SLUR if all(end is not None and end.find("pitch") is not None for end in ends) else DRAWN_SLUR
                for suffix in (DRAWN_SLUR, DRAWN_TAB_SLUR):
                    svg, made = slur_letter(svg, start.get("id") + suffix, text, space, start.get("id"))
                    if made and suffix == lettered:
                        pieces.append(made)
    svg = circle_strings(svg, circled, space)
    svg = italic_plucks(svg, plucked)
    if not pieces:
        return svg
    inner = svg.rfind("</svg>", 0, svg.rfind("</svg>"))
    end = svg.rfind("</g>", 0, inner)
    return svg[:end] + "".join(pieces) + svg[end:]

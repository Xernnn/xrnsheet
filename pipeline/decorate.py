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
a click on a fingering, a string number, a bend or a hammer-on selects it and Delete removes it.
Verovio makes up its own ids for fingerings, so they're matched to the score's in the order each
bar draws them, and left unnamed in a bar where the counts differ.
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
        for note in part.iter("note"):
            if (note.findtext("staff") or "1") in tabs:
                continue
            kind, which = harmonic_kind(note)
            if kind == "artificial":
                # A violin's diamond, or a guitar's A.H., says it's a harmonic, without a circle on each note.
                note.set(DRAWN_AS, which or kind)
                for technical in note.iterfind("notations/technical"):
                    for harmonic in technical.findall("harmonic"):
                        technical.remove(harmonic)
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
                        add_joint(first_copy, tag, "start", number, start.get("line-type", "solid"))
                        add_joint(last_copy, tag, "stop", number)


def boards_of(root: ET.Element, part: ET.Element) -> Fretboard | None:
    return boards(root)[root.findall("part").index(part)]


def group_end(svg: str, start: int) -> int:
    """Return where the <g> opening at this point closes, just after its </g>."""
    depth, position = 0, start
    while True:
        opening, closing = svg.find("<g", position), svg.find("</g>", position)
        if closing < 0:
            return len(svg)
        if 0 <= opening < closing:
            depth += 1
            position = opening + 2
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
                expected.setdefault(top[index], []).extend(measure.iter("fingering"))
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
DRAWN_DIRECTION = re.compile(r'<g id="[^"]+" class="dir">(?:(?!</g>).)*?>(Capo \d+)</', re.S)


def say_capo(root: ET.Element) -> None:
    """Put "Capo 2" over the start of a guitar with a capo, as words Verovio leaves room for."""
    for part, board in zip(root.findall("part"), boards(root)):
        measure = part.find("measure")
        if board is None or not board.capo or measure is None:
            continue
        direction = ET.Element("direction", placement="above")
        ET.SubElement(ET.SubElement(direction, "direction-type"), "words").text = CAPO_WORDS.format(board.capo)
        if board.staves > 1:
            ET.SubElement(direction, "staff").text = board.source or "1"
        first = next((index for index, child in enumerate(measure) if child.tag == "note"), len(measure))
        measure.insert(first, direction)


def name_capo(svg: str, root: ET.Element) -> str:
    """Name each "Capo 2" as the app's own words, so the page doesn't take it for one of the score's marks."""
    wanted = {CAPO_WORDS.format(board.capo) for board in boards(root) if board is not None and board.capo}
    return DRAWN_DIRECTION.sub(lambda found: found.group(0).replace('class="dir"', 'class="xrn-capo"', 1) if found.group(1) in wanted else found.group(0), svg, count=len(wanted))


def decorate(svg: str, root: ET.Element, space: float) -> str:
    """Draw a page's guitar and string marks that Verovio left out. `space` is a staff space in the drawing's units."""
    if "<g" not in svg:
        return svg
    svg = name_capo(svg, root)
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
                pieces.append(letter(x + HEAD_WIDTH * space / 2, y - 2.2 * space, "A.H.", space, None, "harmonic", italic=True))
        for tag, text in JOINED.items():
            for start, first, _ in pairs_of(part, tag, NOTE_PAIRS[tag]):
                if not start.get("id"):
                    continue
                # The letter goes on the tablature, as MuseScore has it, or on the staff if there's none.
                lettered = DRAWN_TAB_SLUR if copies.get((first.get("id") or "") + TAB_COPY) is not None else DRAWN_SLUR
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

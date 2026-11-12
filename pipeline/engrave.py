"""Turn MusicXML back into pages, as SVG for the screen and PDF for printing."""

import functools
import json
import re
import subprocess
import tempfile
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import verovio

from pipeline import decorate, diagrams
from pipeline.layout import page_layout
from pipeline.musicxml import UnreadableScore
from pipeline.fretted import tab_staves
from pipeline.rhythm import OCTAVE_SHIFTS, contexts, octave_lines, place, read_measure
from pipeline.structure import BREAK_PREFIX, bars_per_line
from pipeline.technique import drawn_on_other_staves

# Verovio keeps its font location per thread, so a web server's worker threads must set it.
RESOURCE_PATH = str(Path(verovio.__file__).parent / "data")
RENDER_LOCK = threading.Lock()
LYRIC_WORD_SPACE = 2.5  # Verovio's default leaves neighbouring words almost touching.

# The page follows MuseScore's defaults, so a score looks the same here as it does there: its
# music font, a 1.75 mm staff space on A4 with 15 mm margins, and its line weights. Page settings
# can change the paper, the margins, the staff space and the spacing (see layout.py).
MUSIC_FONT = "Leland"
LINE_WEIGHTS = {  # In staff spaces, as the music font's own defaults give them.
    "staffLineThickness": 0.11,
    "stemThickness": 0.10,
    "legerLineThickness": 0.16,
    "legerLineExtension": 0.35,
    "slurEndpointThickness": 0.07,
    "slurMidpointThickness": 0.21,
    "tieEndpointThickness": 0.07,
    "tieMidpointThickness": 0.21,
    "thinBarlineThickness": 0.18,
    "thickBarlineThickness": 0.55,
    "barlineSeparation": 0.37,
    "repeatBarlineDotSeparation": 0.37,
    "bracketThickness": 0.45,
    "hairpinThickness": 0.12,
    "tupletBracketThickness": 0.10,
    "textEnclosureThickness": 0.16,
    "octaveLineThickness": 0.11,
    "pedalLineThickness": 0.11,
    "repeatEndingLineThickness": 0.11,
    "lyricLineThickness": 0.10,
}
# MuseScore sets its text in Edwin, which is drawn from the same Century design as C059. Verovio
# only knows how wide Times is, and these are wider, so the text is laid out in Times a little
# larger than it's wanted and then drawn in the wider font at the size that fills the same room.
TEXT_FONTS = "Edwin, C059, 'Century Schoolbook', 'New Century Schoolbook', 'Times New Roman', Times, serif"
TIMES_WIDTH = 0.864  # How wide Times is beside those fonts, measured over ordinary words and chords.
POINT = 35.28  # One point of type, in the hundredths of a millimetre a page is drawn in.
TEXT_POINTS = 10  # Lyrics, chord symbols and staff text.
TITLE_POINTS = 22
COMPOSER_POINTS = 10
# The title frame is ten staff spaces tall, with the title at its top and the composer at its
# bottom. These are in the hundredths of a millimetre a page is drawn in, from the top margin.
FRAME_SPACES = 10
TITLE_BASELINE = 580
COMPOSER_ABOVE_FOOT = 80  # The composer's baseline, above the foot of the frame.
ONE_LINE_OF_TEXT = 353  # What Verovio has already left for the header.
MOST_HEADER_MARGIN = 24  # Verovio's limit, in half staff spaces.
FONT_SIZE = re.compile(r'font-size="(\d+)px"')
# The drawing's own size, in the hundredths of a millimetre a page is drawn in.
DRAWN_SIZE = re.compile(r'class="definition-scale"[^>]*viewBox="0 0 (\d+) (\d+)"')
SCALE = 45  # Verovio draws a page at this percent, so a millimetre of paper is 4.5 pixels of the drawing.
VIEWS = ("page", "horizontal", "vertical")
TAB_NEARER = 4  # How much nearer the staff above a TAB staff is drawn than another staff, in half staff spaces.
LONGEST_SYSTEM = 100000  # Verovio's widest page, ten metres, which a continuous view is cut down to fit.
LONGEST_PAGE = 60000
# A sharp or flat inside words, as in a chord symbol. Verovio sizes it for its music font, where a
# sign is drawn small within a tall box, and the ordinary font that takes its place fills the box.
SIGN_IN_TEXT = re.compile(r'(<tspan font-family="Noto Music" font-size=")(\d+)(px">[♭♮♯])')
SIGN_SCALE = 0.75
PLACED_TEXT = re.compile(r'(<text x="[^"]*" y="[^"]*")')

# Verovio writes these symbols in a private music font that the SVG rasterizer cannot
# find, so they would come out as garbage. Swap them for their ordinary Unicode versions.
MUSIC_TEXT_GLYPHS = {
    "": "♭",  # flat
    "": "♮",  # natural
    "": "♯",  # sharp
    "": "\U0001d15e",  # half note
    "": "♩",  # quarter note
    "": "♪",  # eighth note
    "\ueca0": "\U0001d15c",  # double whole note
    "\ueca2": "\U0001d15d",  # whole note
    "\ueca9": "\U0001d161",  # sixteenth note
    "\uecab": "\U0001d162",  # thirty-second note
    "\uecb7": ".",  # the dot of a dotted note in a tempo mark
}


def replace_private_glyphs(svg: str) -> str:
    for private, standard in MUSIC_TEXT_GLYPHS.items():
        svg = svg.replace(private, standard)
    # The benchmark's pages are drawn in Leipzig, so either font's name may be here.
    return svg.replace(f'font-family="{MUSIC_FONT}"', 'font-family="Noto Music"').replace('font-family="Leipzig"', 'font-family="Noto Music"')


def drop_degrees(root: ET.Element) -> None:
    """Stop Verovio printing a chord twice.

    A chord like A7b9 stores its spelling and also its altered ninth as a degree. That
    detail is worth keeping in the file, but Verovio prints both and shows A7b9(b9).
    """
    for harmony in root.iter("harmony"):
        kind = harmony.find("kind")
        if kind is not None and kind.get("text") is not None:
            for degree in harmony.findall("degree"):
                harmony.remove(degree)


def drop_spelled_degrees(musicxml: str) -> str:
    root = ET.fromstring(musicxml)
    drop_degrees(root)
    return ET.tostring(root, encoding="unicode")


def beam_group_length(beats: int, beat_type: int, shortest: float) -> float:
    """Return how many quarter notes one beamed group may span."""
    if beat_type == 8 and beats % 3 == 0:
        return 1.5
    if beats == 4 and beat_type == 4 and shortest >= 0.5:
        return 2.0  # Plain eighths in 4/4 group across two beats.
    return 1.0


def write_beam_group(group: list[tuple[ET.Element, float]]) -> None:
    """Mark a run of notes as beamed, with a second beam where sixteenths sit together."""
    for level, longest_allowed in ((1, 0.5), (2, 0.25)):
        run: list[ET.Element] = []
        for note, length in [*group, (None, 1.0)]:
            if note is not None and length <= longest_allowed:
                run.append(note)
                continue
            if len(run) >= 2:
                for index, member in enumerate(run):
                    value = "begin" if index == 0 else "end" if index == len(run) - 1 else "continue"
                    beam = ET.Element("beam", number=str(level))
                    beam.text = value
                    children = list(member)
                    position = next((i for i, child in enumerate(children) if child.tag in ("notations", "lyric", "play")), len(children))
                    member.insert(position, beam)
            run = []


def add_beams(root: ET.Element) -> None:
    """Beam eighth notes and shorter, because recognized scores arrive with none.

    This runs each time a page is drawn, so beams stay right after notes are edited. Fret numbers
    on a tablature staff have no stems to beam.
    """
    for part in root.findall("part"):
        tabs = set(tab_staves(part))
        divisions, beats, beat_type = 1, 4, 4
        for measure in part.findall("measure"):
            divisions = int(measure.findtext(".//attributes/divisions") or divisions)
            time = measure.find(".//attributes/time")
            if time is not None:
                beats, beat_type = int(time.findtext("beats")), int(time.findtext("beat-type"))
            streams: dict[tuple[str, str], list[tuple[ET.Element, float, float]]] = {}
            position = 0.0
            for child in measure:
                length = float(child.findtext("duration") or 0) / divisions
                if child.tag == "backup":
                    position -= length
                elif child.tag == "forward":
                    position += length
                elif child.tag == "note" and child.find("grace") is None:
                    for beam in child.findall("beam"):
                        child.remove(beam)
                    if child.find("chord") is not None or (child.findtext("staff") or "1") in tabs:
                        continue
                    key = (child.findtext("staff") or "1", child.findtext("voice") or "1")
                    streams.setdefault(key, []).append((child, position, length))
                    position += length
            for notes in streams.values():
                shortest = min((length for _, _, length in notes), default=1.0)
                span = beam_group_length(beats, beat_type, shortest)
                group: list[tuple[ET.Element, float]] = []
                current = None
                for note, onset, length in notes:
                    beamable = note.find("rest") is None and 0 < length <= 0.5
                    bucket = int(onset / span + 1e-6)
                    if not beamable or bucket != current:
                        write_beam_group(group)
                        group = []
                    if beamable:
                        group.append((note, length))
                        current = bucket
                    else:
                        current = None
                write_beam_group(group)


def set_line_breaks(root: ET.Element) -> None:
    """Keep only the line breaks a person asked for.

    A recognized page keeps the lines it was read with in its file, and those only fit that page.
    """
    every = bars_per_line(root)
    for part in root.findall("part"):
        measures = part.findall("measure")
        start = 1 if measures and measures[0].get("implicit") == "yes" else 0
        for measure in measures:
            for found in measure.findall("print"):
                if found.get("new-system") and not (found.get("id") or "").startswith(BREAK_PREFIX):
                    del found.attrib["new-system"]
        if every:
            for index in range(start + every, len(measures), every):
                if not any(found.get("new-system") for found in measures[index].findall("print")):
                    measures[index].insert(0, ET.Element("print", {"new-system": "yes"}))


def octave_direction(kind: str, size: str, number: str, staff: str) -> ET.Element:
    direction = ET.Element("direction", placement="below" if kind == "up" else "above")
    ET.SubElement(ET.SubElement(direction, "direction-type"), "octave-shift", type=kind, size=size, number=number)
    ET.SubElement(direction, "staff").text = staff
    return direction


def place_octave_lines(root: ET.Element) -> None:
    """Put each octave line where Verovio will read it right, and draw the notes under it in place.

    Verovio starts a line at the next note it reads after the line's start, whichever staff that's
    on, and ends it at the last note read before its stop. The notes of the line's staff it reads
    in between are drawn an octave away. That goes by the order of the file, which is voice by
    voice and staff by staff, not by time, so a line on a piano's lower staff, or one over two
    voices, would be drawn on the wrong staff and move the wrong notes. Each line's ends are moved
    beside the first and last notes it covers on its own staff, and any note Verovio would still
    draw wrongly is given the octave that makes it come out where it belongs. This is only for the
    drawing: the score keeps its notes' true pitches and its lines where they are.
    """
    for part in root.findall("part"):
        lines = octave_lines(part)
        if not lines and next(part.iter("octave-shift"), None) is None:
            continue
        for measure in part.findall("measure"):
            for direction in measure.findall("direction"):
                for holder in direction.findall("direction-type"):
                    if holder.find("octave-shift") is not None:
                        direction.remove(holder)
                if direction.find("direction-type") is None:
                    measure.remove(direction)
        under: dict[ET.Element, int] = {}  # How many octaves each note is drawn below its pitch.
        covered: dict[int, list] = {number: [] for number in range(len(lines))}
        for context in contexts(part):
            children = list(context.measure)
            for (staff, _), voice in read_measure(context.measure, context.divisions).lines.items():
                for event in voice:
                    for number, line in enumerate(lines):
                        if line.covers(staff, (context.index, event.onset)):
                            covered[number].append(((context.index, event.onset, children.index(event.notes[0])), context.measure, event))
                            for note in [*event.grace, *event.notes]:
                                under[note] = line.octaves
        for number, line in enumerate(lines):
            if not covered[number]:
                continue
            kind, size = next(key for key, octaves in OCTAVE_SHIFTS.items() if octaves == line.octaves)
            _, measure, first = min(covered[number], key=lambda item: item[0])
            opening = [*first.grace, *first.notes][0]
            measure.insert(list(measure).index(opening), octave_direction(kind, size, line.number, line.staff))
            _, measure, last = max(covered[number], key=lambda item: item[0])
            measure.insert(list(measure).index(last.notes[-1]) + 1, octave_direction("stop", size, line.number, line.staff))
        drawn: dict[str, int] = {}  # What Verovio is drawing each staff's notes as, where it's read to.
        for measure in part.findall("measure"):
            for child in measure:
                if child.tag == "direction":
                    for shift in child.iter("octave-shift"):
                        drawn[child.findtext("staff") or "1"] = OCTAVE_SHIFTS.get((shift.get("type"), shift.get("size", "8")), 0)
                elif child.tag == "note" and child.find("pitch/octave") is not None:
                    wrong = drawn.get(child.findtext("staff") or "1", 0) - under.get(child, 0)
                    if wrong:
                        octave = child.find("pitch/octave")
                        octave.text = str(int(octave.text) + wrong)


def prepare_tablature(root: ET.Element) -> None:
    """Draw tablature as fret numbers alone, and a standard staff as one whatever tuning it keeps.

    A TAB staff shows no rests and no stems here, as its standard staff above shows the rhythm, and
    a note with no fret, which no string could play, isn't drawn: Verovio would print nonsense for
    it. Verovio draws any staff whose <staff-details> tune strings as tablature, so a guitar with no
    TAB staff keeps its tuning in the file but not in the drawing.
    """
    for part in root.findall("part"):
        tabs = set(tab_staves(part))
        for details in part.iter("staff-details"):
            if details.get("number", "1") not in tabs:
                for child in [*details.findall("staff-tuning"), *details.findall("capo")]:
                    details.remove(child)
        if not tabs:
            continue
        for note in part.iter("note"):
            if (note.findtext("staff") or "1") not in tabs:
                continue
            if note.find("rest") is not None or note.findtext("notations/technical/fret") is None:
                note.set("print-object", "no")
            elif note.find("stem") is None:
                place(note, ET.Element("stem"))
            if note.find("stem") is not None:
                note.find("stem").text = "none"


def prepare_for_drawing(musicxml: str | ET.Element) -> str:
    """Return the score as it's handed to Verovio. A score passed already read is changed in place.

    Read and written once: with a long score, reading and writing it again took a third as long as
    Verovio's drawing.
    """
    root = ET.fromstring(musicxml) if isinstance(musicxml, str) else musicxml
    prepare_tablature(root)
    decorate.prepare(root)
    add_beams(root)
    set_line_breaks(root)
    place_octave_lines(root)
    drawn_on_other_staves(root)  # Last, as beams and octave lines go by the staff a note's voice is on.
    drop_degrees(root)
    return ET.tostring(root, encoding="unicode")


TAB_STAFF_DEF = re.compile(r'(<staffDef [^>]*notationtype="tab[^"]*"[^>]*?)(/?>)')


def closer_tablature(toolkit, spacing: int) -> None:
    """Bring each tablature staff closer to the staff above it.

    Verovio draws tablature lines further apart than a staff's, and spaces a TAB staff from the one
    above in its own larger units, which left it three times as far away as a piano's lower staff. MEI
    can say how far a staff is from the one above, which MusicXML can't tell Verovio, so the score
    goes through MEI to say it. That distance is the least there is: notes far below the staff
    above still push the tablature down.
    """
    mei = toolkit.getMEI()
    toolkit.loadData(TAB_STAFF_DEF.sub(lambda found: f'{found.group(1)} spacing="{spacing}"{found.group(2)}', mei))


@functools.lru_cache(maxsize=1)
def glyph_outlines() -> dict[str, list]:
    """Return each symbol of the music font as its outline, its box and how far it moves the pen.

    The toolbar and palettes draw their symbols from these, so they show the very shapes a page is
    engraved with. They come with Verovio. Distances are in thousandths of four staff spaces, with
    y going up.
    """
    folder = Path(RESOURCE_PATH)
    found = {}
    for box in ET.parse(folder / f"{MUSIC_FONT}.xml").getroot().findall("g"):
        code = box.get("c")
        path = folder / MUSIC_FONT / f"{code}.xml"
        outline = " ".join(piece.get("d") for piece in ET.parse(path).getroot().iter("path")) if path.exists() else ""
        if outline:
            found[code] = [outline, *(round(float(box.get(name))) for name in ("x", "y", "w", "h", "h-a-x"))]
    return found


def glyph_script(codes: set[str] | None = None) -> str:
    """The outlines as a script the page loads before its own: all of them, or those of the codes given."""
    outlines = glyph_outlines()
    chosen = outlines if codes is None else {code: outlines[code] for code in sorted(codes) if code in outlines}
    return f"const GLYPHS = {json.dumps(chosen, separators=(',', ':'))};\n"


def set_text_font(svg: str) -> str:
    """Draw the words in MuseScore's text font, at the width Verovio left room for."""
    svg = FONT_SIZE.sub(lambda found: f'font-size="{round(int(found.group(1)) * TIMES_WIDTH)}px"', svg)
    svg = SIGN_IN_TEXT.sub(lambda found: f"{found.group(1)}{round(int(found.group(2)) * SIGN_SCALE)}{found.group(3)}", svg)
    return svg.replace('font-family="Times, serif"', f'font-family="{TEXT_FONTS}"').replace("font-family:Times,serif", f"font-family:{TEXT_FONTS}")


def frame_height(space: float) -> float:
    """The title frame's height, in the hundredths of a millimetre a page is drawn in."""
    return FRAME_SPACES * space * 100


def size_header(svg: str, space: float = 1.75, lifted: float = 0) -> str:
    """Lay the title and the composer out as MuseScore's title frame does.

    Verovio prints every credit in one size on one line. The title goes at the top of the frame in
    its own size, and the composer at the bottom right. The room under the header makes way for them.
    Where Verovio can't leave room enough under the frame, as for chord diagrams over the first
    system, the composer is lifted by what's missing, in the hundredths of a millimetre a page is drawn in.
    """
    head = svg.find('class="pgHead"')
    if head < 0:
        return svg
    end = svg.find("</g>", head)
    pieces = re.split(r"(?=<text )", svg[head:end])
    for index, piece in enumerate(pieces[1:], start=1):
        is_title = 'text-anchor="middle"' in piece  # The title is the centred line.
        points, baseline = (TITLE_POINTS, TITLE_BASELINE) if is_title else (COMPOSER_POINTS, round(frame_height(space) - COMPOSER_ABOVE_FOOT - lifted))
        piece = FONT_SIZE.sub(lambda found: f'font-size="{round(points * POINT) if int(found.group(1)) else 0}px"', piece)
        pieces[index] = re.sub(r' y="-?\d+"', f' y="{baseline}"', piece)
    return svg[:head] + "".join(pieces) + svg[end:]


def keep_spaces(svg: str) -> str:
    """Keep the spaces in "Allegro ♩ = 132".

    Each piece of it is text of its own, and a space at the edge of one is dropped when a PDF is
    made. The line breaks between the pieces are kept too, but those are written at no size.
    """
    return PLACED_TEXT.sub(r'\1 xml:space="preserve"', svg)


def page_size(svg: str) -> list[float]:
    """Return a drawn page's width and height in millimetres, which is the size it prints at."""
    found = DRAWN_SIZE.search(svg)
    return [int(found.group(1)) / 100, int(found.group(2)) / 100] if found else [210.0, 297.0]


def render_pages(musicxml: str, view: str = "page") -> list[str]:
    """Draw a score's pages, or in a continuous view one long system across or one long page down.

    MuseScore's continuous views leave out its frames, so the horizontal one has no title. The
    vertical one is a single page as long as the music, which keeps its title at the top.
    """
    if view not in VIEWS:
        raise ValueError(f"Unknown view {view}")
    has_title = "<credit" in musicxml and view != "horizontal"
    score = ET.fromstring(musicxml)
    layout = page_layout(score)
    room = diagrams.ROOM * 2 if diagrams.shown(score) else 0  # Half staff spaces above each system for its chord diagrams.
    unit = layout.space * 10 / 2  # Half a staff space, in tenths of a millimetre, which is how Verovio counts.
    prepared = prepare_for_drawing(score)  # Changes score, which isn't read again below.
    header_margin = 2 + room + (frame_height(layout.space) - ONE_LINE_OF_TEXT) / (unit * 10)
    lifted = max(header_margin - MOST_HEADER_MARGIN, 0) * unit * 10
    with RENDER_LOCK:
        toolkit = verovio.toolkit(False)  # It would look for fonts itself, and fail on a worker thread.
        toolkit.setResourcePath(RESOURCE_PATH)
        breaks = "smart" if 'new-system="yes"' in prepared else "auto"
        toolkit.setOptions({
            "pageWidth": LONGEST_SYSTEM if view == "horizontal" else round(layout.width * 10),
            "pageHeight": LONGEST_PAGE if view == "vertical" else round(layout.height * 10),
            "adjustPageWidth": view == "horizontal",
            "adjustPageHeight": view != "page",
            "scale": SCALE,
            "font": MUSIC_FONT,
            "unit": unit,
            "pageMarginLeft": round(layout.left * 10),
            "pageMarginRight": round(layout.right * 10),
            "pageMarginTop": round(layout.top * 10 + (0 if has_title else room * unit)),
            "pageMarginBottom": round(layout.bottom * 10),
            "spacingSystem": round(layout.systems * 2) + room,
            "spacingStaff": round(layout.staves * 2),
            "engravingDefaults": LINE_WEIGHTS,
            "lyricSize": TEXT_POINTS * POINT / TIMES_WIDTH / (unit * 10),
            "header": "auto" if has_title else "none",
            "bottomMarginHeader": min(header_margin, MOST_HEADER_MARGIN),  # In half staff spaces.
            "footer": "none",
            "breaks": "none" if view == "horizontal" else breaks,
            "breaksSmartSb": 0.0,  # A break someone asked for always counts, however short the line.
            "lyricWordSpace": LYRIC_WORD_SPACE,
            "octaveAlternativeSymbols": True,  # "8va" and "8vb", as MuseScore writes them, not a bare 8.
        })
        if not toolkit.loadData(prepared):
            raise UnreadableScore("Could not read this MusicXML")
        if "<sign>TAB</sign>" in prepared:
            closer_tablature(toolkit, max(round(layout.staves * 2) - TAB_NEARER, 0))
        drawn = ET.fromstring(prepared)
        space = layout.space * 100  # A staff space, in the hundredths of a millimetre a page is drawn in.
        chords = diagrams.Drawing.of(drawn)
        pages = []
        for page in range(1, toolkit.getPageCount() + 1):
            svg = diagrams.draw(decorate.decorate(replace_private_glyphs(toolkit.renderToSVG(page)), drawn, space), chords, space)
            pages.append(keep_spaces(size_header(set_text_font(svg), layout.space, lifted)))
        return pages


def export_pdf(musicxml: str, out_path: Path) -> None:
    with tempfile.TemporaryDirectory() as folder:
        pages = []
        for number, svg in enumerate(render_pages(musicxml)):
            page = Path(folder) / f"page{number:03d}.svg"
            page.write_text(svg, encoding="utf-8")
            pages.append(str(page))
        subprocess.run(["rsvg-convert", "-f", "pdf", "-o", str(out_path), *pages], check=True)

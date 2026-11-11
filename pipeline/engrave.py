"""Turn MusicXML back into pages, as SVG for the screen and PDF for printing."""

import re
import subprocess
import tempfile
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import verovio

from pipeline.musicxml import UnreadableScore
from pipeline.structure import BREAK_PREFIX, bars_per_line

# Verovio keeps its font location per thread, so a web server's worker threads must set it.
RESOURCE_PATH = str(Path(verovio.__file__).parent / "data")
RENDER_LOCK = threading.Lock()
LYRIC_WORD_SPACE = 2.5  # Verovio's default leaves neighbouring words almost touching.

# The page follows MuseScore's defaults, so a score looks the same here as it does there: its
# music font, a 1.75 mm staff space on A4 with 15 mm margins, and its line weights.
MUSIC_FONT = "Leland"
STAFF_UNIT = 8.75  # Half a staff space, in tenths of a millimetre.
PAGE_MARGIN = 150
SYSTEM_SPACING = 12  # In half staff spaces, between what one system draws and the next.
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
FRAME_HEIGHT = 1750
TITLE_BASELINE = 580
COMPOSER_BASELINE = 1670
ONE_LINE_OF_TEXT = 353  # What Verovio has already left for the header.
HEADER_ROOM = 2 + (FRAME_HEIGHT - ONE_LINE_OF_TEXT) / (STAFF_UNIT * 10)  # Under the header, in half staff spaces.
FONT_SIZE = re.compile(r'font-size="(\d+)px"')
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


def drop_spelled_degrees(musicxml: str) -> str:
    """Stop Verovio printing a chord twice.

    A chord like A7b9 stores its spelling and also its altered ninth as a degree. That
    detail is worth keeping in the file, but Verovio prints both and shows A7b9(b9).
    """
    root = ET.fromstring(musicxml)
    for harmony in root.iter("harmony"):
        kind = harmony.find("kind")
        if kind is not None and kind.get("text") is not None:
            for degree in harmony.findall("degree"):
                harmony.remove(degree)
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

    This runs each time a page is drawn, so beams stay right after notes are edited.
    """
    for part in root.findall("part"):
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
                    if child.find("chord") is not None:
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


def prepare_for_drawing(musicxml: str) -> str:
    root = ET.fromstring(musicxml)
    add_beams(root)
    set_line_breaks(root)
    return drop_spelled_degrees(ET.tostring(root, encoding="unicode"))


def set_text_font(svg: str) -> str:
    """Draw the words in MuseScore's text font, at the width Verovio left room for."""
    svg = FONT_SIZE.sub(lambda found: f'font-size="{round(int(found.group(1)) * TIMES_WIDTH)}px"', svg)
    svg = SIGN_IN_TEXT.sub(lambda found: f"{found.group(1)}{round(int(found.group(2)) * SIGN_SCALE)}{found.group(3)}", svg)
    return svg.replace('font-family="Times, serif"', f'font-family="{TEXT_FONTS}"').replace("font-family:Times,serif", f"font-family:{TEXT_FONTS}")


def size_header(svg: str) -> str:
    """Lay the title and the composer out as MuseScore's title frame does.

    Verovio prints every credit in one size on one line. The title goes at the top of the frame in
    its own size, and the composer at the bottom right. HEADER_ROOM is what makes room for them.
    """
    head = svg.find('class="pgHead"')
    if head < 0:
        return svg
    end = svg.find("</g>", head)
    pieces = re.split(r"(?=<text )", svg[head:end])
    for index, piece in enumerate(pieces[1:], start=1):
        is_title = 'text-anchor="middle"' in piece  # The title is the centred line.
        points, baseline = (TITLE_POINTS, TITLE_BASELINE) if is_title else (COMPOSER_POINTS, COMPOSER_BASELINE)
        piece = FONT_SIZE.sub(lambda found: f'font-size="{round(points * POINT) if int(found.group(1)) else 0}px"', piece)
        pieces[index] = re.sub(r' y="-?\d+"', f' y="{baseline}"', piece)
    return svg[:head] + "".join(pieces) + svg[end:]


def keep_spaces(svg: str) -> str:
    """Keep the spaces in "Allegro ♩ = 132".

    Each piece of it is text of its own, and a space at the edge of one is dropped when a PDF is
    made. The line breaks between the pieces are kept too, but those are written at no size.
    """
    return PLACED_TEXT.sub(r'\1 xml:space="preserve"', svg)


def render_pages(musicxml: str, page_width: int = 2100, page_height: int = 2970) -> list[str]:
    has_title = "<credit" in musicxml
    prepared = prepare_for_drawing(musicxml)
    with RENDER_LOCK:
        toolkit = verovio.toolkit(False)  # It would look for fonts itself, and fail on a worker thread.
        toolkit.setResourcePath(RESOURCE_PATH)
        toolkit.setOptions({
            "pageWidth": page_width,
            "pageHeight": page_height,
            "scale": 45,
            "font": MUSIC_FONT,
            "unit": STAFF_UNIT,
            "pageMarginLeft": PAGE_MARGIN,
            "pageMarginRight": PAGE_MARGIN,
            "pageMarginTop": PAGE_MARGIN,
            "pageMarginBottom": PAGE_MARGIN,
            "spacingSystem": SYSTEM_SPACING,
            "engravingDefaults": LINE_WEIGHTS,
            "lyricSize": TEXT_POINTS * POINT / TIMES_WIDTH / (STAFF_UNIT * 10),
            "header": "auto" if has_title else "none",
            "bottomMarginHeader": HEADER_ROOM,
            "footer": "none",
            "breaks": "smart" if 'new-system="yes"' in prepared else "auto",
            "breaksSmartSb": 0.0,  # A break someone asked for always counts, however short the line.
            "lyricWordSpace": LYRIC_WORD_SPACE,
        })
        if not toolkit.loadData(prepared):
            raise UnreadableScore("Could not read this MusicXML")
        return [keep_spaces(size_header(set_text_font(replace_private_glyphs(toolkit.renderToSVG(page))))) for page in range(1, toolkit.getPageCount() + 1)]


def export_pdf(musicxml: str, out_path: Path) -> None:
    with tempfile.TemporaryDirectory() as folder:
        pages = []
        for number, svg in enumerate(render_pages(musicxml)):
            page = Path(folder) / f"page{number:03d}.svg"
            page.write_text(svg, encoding="utf-8")
            pages.append(str(page))
        subprocess.run(["rsvg-convert", "-f", "pdf", "-o", str(out_path), *pages], check=True)

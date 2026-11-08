"""Turn MusicXML back into pages, as SVG for the screen and PDF for printing."""

import re
import subprocess
import tempfile
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import verovio

from pipeline.structure import BREAK_PREFIX, bars_per_line

# Verovio keeps its font location per thread, so a web server's worker threads must set it.
RESOURCE_PATH = str(Path(verovio.__file__).parent / "data")
RENDER_LOCK = threading.Lock()
TITLE_SCALE = 2
LYRIC_WORD_SPACE = 2.5  # Verovio's default leaves neighbouring words almost touching.

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
    return svg.replace('font-family="Leipzig"', 'font-family="Noto Music"')


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


def enlarge_title(svg: str) -> str:
    """Make the title stand out. Verovio prints credits in one size, whatever they ask for."""
    head = svg.find('class="pgHead"')
    if head < 0:
        return svg
    # The title is the centred line. A composer on their own is right-aligned and stays as it is.
    centred = svg.find('text-anchor="middle"', head)
    if centred < 0:
        return svg
    return svg[:centred] + re.sub(r'font-size="(\d{3,})px"', lambda found: f'font-size="{int(found.group(1)) * TITLE_SCALE}px"', svg[centred:], count=1)


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
            "header": "auto" if has_title else "none",
            "footer": "none",
            "breaks": "smart" if 'new-system="yes"' in prepared else "auto",
            "breaksSmartSb": 0.0,  # A break someone asked for always counts, however short the line.
            "lyricWordSpace": LYRIC_WORD_SPACE,
        })
        if not toolkit.loadData(prepared):
            raise ValueError("Could not read this MusicXML")
        return [enlarge_title(replace_private_glyphs(toolkit.renderToSVG(page))) for page in range(1, toolkit.getPageCount() + 1)]


def export_pdf(musicxml: str, out_path: Path) -> None:
    with tempfile.TemporaryDirectory() as folder:
        pages = []
        for number, svg in enumerate(render_pages(musicxml)):
            page = Path(folder) / f"page{number:03d}.svg"
            page.write_text(svg, encoding="utf-8")
            pages.append(str(page))
        subprocess.run(["rsvg-convert", "-f", "pdf", "-o", str(out_path), *pages], check=True)

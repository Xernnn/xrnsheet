"""Read and edit MusicXML: chord symbols, measures, and where each beat falls.

Only the parts of MusicXML this project needs are handled. Positions are in quarter notes
from the start of a measure.
"""

import io
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from chords.vocab import Chord, parse_chord

# meaning: (MusicXML kind, added or altered degrees as (degree, alteration))
QUALITY_TO_KIND: dict[str, tuple[str, list[tuple[int, int]]]] = {
    "": ("major", []),
    "m": ("minor", []),
    "7": ("dominant", []),
    "maj7": ("major-seventh", []),
    "m7": ("minor-seventh", []),
    "m7b5": ("half-diminished", []),
    "dim": ("diminished", []),
    "dim7": ("diminished-seventh", []),
    "aug": ("augmented", []),
    "7#5": ("augmented-seventh", []),
    "sus4": ("suspended-fourth", []),
    "7sus4": ("suspended-fourth", [(7, -1)]),
    "9sus4": ("suspended-fourth", [(7, -1), (9, 0)]),
    "6": ("major-sixth", []),
    "m6": ("minor-sixth", []),
    "6/9": ("major-sixth", [(9, 0)]),
    "9": ("dominant-ninth", []),
    "m9": ("minor-ninth", []),
    "maj9": ("major-ninth", []),
    "11": ("dominant-11th", []),
    "m11": ("minor-11th", []),
    "13": ("dominant-13th", []),
    "7b9": ("dominant", [(9, -1)]),
    "7#9": ("dominant", [(9, 1)]),
    "7#11": ("dominant", [(11, 1)]),
    "7b13": ("dominant", [(13, -1)]),
    "7alt": ("dominant", [(5, 1), (9, 1)]),
    "13b9": ("dominant-13th", [(9, -1)]),
    "13#11": ("dominant-13th", [(11, 1)]),
    "maj7#11": ("major-seventh", [(11, 1)]),
    "m(maj7)": ("major-minor", []),
    "add9": ("major", [(9, 0)]),
}
KIND_TO_QUALITY = {kind: quality for quality, (kind, degrees) in QUALITY_TO_KIND.items() if not degrees}
ALTER_TEXT = {-1: "b", 0: "", 1: "#"}
MOST_UNPACKED_BYTES = 64 * 1024 * 1024  # A small compressed file can unpack to something huge.


@dataclass
class Event:
    onset: float
    is_rest: bool


@dataclass
class Measure:
    element: ET.Element
    duration: float
    events: list[Event] = field(default_factory=list)
    starts_system: bool = False


def load_root(path: Path) -> ET.Element:
    """Parse .musicxml, or the main file inside a compressed .mxl."""
    if path.suffix == ".mxl":
        with zipfile.ZipFile(path) as archive:
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            name = next(item.get("full-path") for item in container.iter("rootfile"))
            return ET.fromstring(archive.read(name))
    return ET.parse(path).getroot()


def read_score_file(data: bytes, suffix: str) -> ET.Element:
    """Read a MusicXML file someone opened, raising ValueError if it isn't one this app can edit."""
    try:
        if suffix == ".mxl":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                container = ET.fromstring(archive.read("META-INF/container.xml"))
                name = next(item.get("full-path") for item in container.iter("rootfile"))
                if archive.getinfo(name).file_size > MOST_UNPACKED_BYTES:
                    raise ValueError("That file is too large.")
                data = archive.read(name)
        root = ET.fromstring(data)
    except (ET.ParseError, zipfile.BadZipFile, KeyError, StopIteration) as error:
        raise ValueError("That file couldn't be read as MusicXML.") from error
    if root.tag != "score-partwise" or root.find("part") is None:
        raise ValueError("Only MusicXML scores written part by part can be opened.")
    return root


def to_text(root: ET.Element) -> str:
    buffer = io.StringIO()
    ET.ElementTree(root).write(buffer, encoding="unicode", xml_declaration=False)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + buffer.getvalue()


def split_pitch(name: str) -> tuple[str, int]:
    return name[0], {"b": -1, "#": 1}.get(name[1:], 0)


def add_pitch(parent: ET.Element, prefix: str, name: str) -> None:
    step, alter = split_pitch(name)
    ET.SubElement(parent, f"{prefix}-step").text = step
    if alter:
        ET.SubElement(parent, f"{prefix}-alter").text = str(alter)


def harmony_element(chord: Chord) -> ET.Element:
    kind, degrees = QUALITY_TO_KIND[chord.quality]
    element = ET.Element("harmony")
    add_pitch(ET.SubElement(element, "root"), "root", chord.root)
    ET.SubElement(element, "kind", text=chord.quality).text = kind
    if chord.bass:
        add_pitch(ET.SubElement(element, "bass"), "bass", chord.bass)
    for degree, alter in degrees:
        node = ET.SubElement(element, "degree")
        ET.SubElement(node, "degree-value").text = str(degree)
        ET.SubElement(node, "degree-alter").text = str(alter)
        ET.SubElement(node, "degree-type").text = "add"
    return element


def read_pitch(element: ET.Element | None, prefix: str) -> str | None:
    if element is None or element.findtext(f"{prefix}-step") is None:
        return None
    alter = round(float(element.findtext(f"{prefix}-alter") or 0))
    return element.findtext(f"{prefix}-step") + ALTER_TEXT.get(alter, "")


def chord_from_harmony(element: ET.Element) -> Chord | None:
    """Read a chord written by any program, as faithfully as its markup allows."""
    root = read_pitch(element.find("root"), "root")
    kind = element.find("kind")
    if root is None or kind is None:
        return None
    bass = read_pitch(element.find("bass"), "bass")
    suffix = f"/{bass}" if bass else ""
    printed = kind.get("text")
    if printed is not None:
        chord = parse_chord(root + printed + suffix)
        if chord is not None:
            return chord
    quality = KIND_TO_QUALITY.get(kind.text or "")
    if quality is None:
        return None
    for degree in element.findall("degree"):
        alter = round(float(degree.findtext("degree-alter") or 0))
        quality += ALTER_TEXT.get(alter, "") + (degree.findtext("degree-value") or "")
    return parse_chord(root + quality + suffix)


def measure_duration(element: ET.Element, divisions: int, beats: float) -> float:
    position = longest = 0.0
    for child in element:
        length = float(child.findtext("duration") or 0) / divisions
        if child.tag == "note" and child.find("chord") is None and child.find("grace") is None:
            position += length
        elif child.tag == "forward":
            position += length
        elif child.tag == "backup":
            position -= length
        longest = max(longest, position)
    return longest or beats


def read_measures(part: ET.Element) -> list[Measure]:
    """List a part's measures with the onsets of its first voice-stream, before any backup."""
    measures = []
    divisions = 1
    beats = 4.0
    for index, element in enumerate(part.findall("measure")):
        found = element.findtext(".//attributes/divisions")
        divisions = int(found) if found else divisions
        time = element.find(".//attributes/time")
        if time is not None:
            beats = float(time.findtext("beats")) * 4 / float(time.findtext("beat-type"))
        printed = element.find("print")
        starts_system = index == 0 or (printed is not None and printed.get("new-system") == "yes")
        measure = Measure(element, measure_duration(element, divisions, beats), starts_system=starts_system)
        position = 0.0
        for child in element:
            if child.tag == "backup":
                break
            if child.tag == "forward":
                position += float(child.findtext("duration") or 0) / divisions
            elif child.tag == "note" and child.find("chord") is None and child.find("grace") is None:
                measure.events.append(Event(position, child.find("rest") is not None))
                position += float(child.findtext("duration") or 0) / divisions
        measures.append(measure)
    return measures


def insert_harmony(measure: Measure, chord: Chord, onset: float, divisions_hint: int | None = None) -> None:
    """Add a chord in front of the note at this onset, which is where MusicXML expects it."""
    position = 0.0
    divisions = divisions_hint or 1
    for index, child in enumerate(list(measure.element)):
        if child.tag == "backup":
            break
        if child.tag == "note" and child.find("chord") is None and child.find("grace") is None:
            if abs(position - onset) < 1e-6:
                measure.element.insert(index, harmony_element(chord))
                return
            position += float(child.findtext("duration") or 0) / divisions
        elif child.tag == "forward":
            position += float(child.findtext("duration") or 0) / divisions
    measure.element.append(harmony_element(chord))

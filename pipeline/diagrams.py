"""Chord diagrams: the fretboard grid over a chord symbol, showing a guitarist where to put each finger.

A score shows them or doesn't, as a setting it keeps. Each chord's shape comes from a library of the
shapes guitarists learn first in standard tuning: the open chords, then the barre chords built on
the low E string and on the A string, moved up the neck. A chord those don't cover, or any chord in
another tuning, is worked out: the lowest frets that sound its root in the bass and its other notes
above, on strings next to each other. With a capo, a shape is fingered from the capo as if it were
the nut, so a D chord with the capo on the second fret is a C shape.

The diagrams are drawn into the page above their chord symbols (Verovio doesn't draw MusicXML's
<frame>), and written into a MusicXML file as each <harmony>'s <frame>, so MuseScore shows them too.
A frame a file brings that isn't the library's, someone's own fingering, is kept and drawn as it is.
"""

import functools
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from chords.vocab import Chord, clean_text, parse_chord
from pipeline.fretted import Fretboard, boards, midi_of
from pipeline.musicxml import chord_from_harmony
from pipeline.playback import CHORD_TONES
from pipeline.structure import GUITAR_STRINGS, pitch_of_name, read_setting, write_setting

DIAGRAMS = "xrnsheet-chord-diagrams"
PITCH_CLASSES = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
X = None  # A string that isn't played.
# Open chords, each string's fret from the low E up.
OPEN = {
    "C": (X, 3, 2, 0, 1, 0), "C7": (X, 3, 2, 3, 1, 0), "Cmaj7": (X, 3, 2, 0, 0, 0), "Cadd9": (X, 3, 2, 0, 3, 0), "C6": (X, 3, 2, 2, 1, 0), "Csus4": (X, 3, 3, 0, 1, 1),
    "D": (X, X, 0, 2, 3, 2), "D7": (X, X, 0, 2, 1, 2), "Dm": (X, X, 0, 2, 3, 1), "Dm7": (X, X, 0, 2, 1, 1), "Dmaj7": (X, X, 0, 2, 2, 2), "Dsus4": (X, X, 0, 2, 3, 3), "D6": (X, X, 0, 2, 0, 2),
    "E": (0, 2, 2, 1, 0, 0), "E7": (0, 2, 0, 1, 0, 0), "Em": (0, 2, 2, 0, 0, 0), "Em7": (0, 2, 0, 0, 0, 0), "Emaj7": (0, 2, 1, 1, 0, 0), "Esus4": (0, 2, 2, 2, 0, 0), "E6": (0, 2, 2, 1, 2, 0),
    "Fmaj7": (X, X, 3, 2, 1, 0),
    "G": (3, 2, 0, 0, 0, 3), "G7": (3, 2, 0, 0, 0, 1), "Gmaj7": (3, 2, 0, 0, 0, 2), "G6": (3, 2, 0, 0, 0, 0),
    "A": (X, 0, 2, 2, 2, 0), "A7": (X, 0, 2, 0, 2, 0), "Am": (X, 0, 2, 2, 1, 0), "Am7": (X, 0, 2, 0, 1, 0), "Amaj7": (X, 0, 2, 1, 2, 0), "Asus4": (X, 0, 2, 2, 3, 0),
    "A7sus4": (X, 0, 2, 0, 3, 0), "Am6": (X, 0, 2, 2, 1, 2), "A6": (X, 0, 2, 2, 2, 2),
    "B7": (X, 2, 1, 2, 0, 2),
}
# Barre chords moved up the neck: each string's fret from the barre, with the root on the low E string or the A string.
ON_E = {
    "": (0, 2, 2, 1, 0, 0), "m": (0, 2, 2, 0, 0, 0), "7": (0, 2, 0, 1, 0, 0), "m7": (0, 2, 0, 0, 0, 0), "maj7": (0, X, 1, 1, 0, X),
    "sus4": (0, 2, 2, 2, 0, 0), "7sus4": (0, 2, 0, 2, 0, 0), "6": (0, 2, 2, 1, 2, 0), "m6": (0, 2, 2, 0, 2, 0), "9": (0, X, 0, 1, 0, 2),
    "m9": (0, X, 0, 0, 0, 2), "maj9": (0, X, 1, 1, 0, 2), "add9": (0, 2, 2, 1, 0, 2), "dim": (0, 1, 2, 0, X, X), "dim7": (0, X, -1, 0, -1, X),
    "m7b5": (0, X, 0, 0, -1, X), "aug": (0, X, 2, 1, 1, 0), "7#5": (0, X, 0, 1, 1, 0), "13": (0, X, 0, 1, 2, X),
}
ON_A = {
    "": (X, 0, 2, 2, 2, 0), "m": (X, 0, 2, 2, 1, 0), "7": (X, 0, 2, 0, 2, 0), "m7": (X, 0, 2, 0, 1, 0), "maj7": (X, 0, 2, 1, 2, 0),
    "sus4": (X, 0, 2, 2, 3, 0), "7sus4": (X, 0, 2, 0, 3, 0), "6": (X, 0, 2, 2, 2, 2), "m6": (X, 0, 2, 2, 1, 2), "9": (X, 0, -1, 0, 0, X),
    "m9": (X, 0, -2, 0, 0, X), "maj9": (X, 0, -1, 1, 0, X), "add9": (X, 0, 2, 4, 2, 0), "dim7": (X, 0, 1, -1, 1, X), "m7b5": (X, 0, 1, 0, 1, X),
    "dim": (X, 0, 1, 2, 1, X), "aug": (X, 0, 3, 2, 2, 1), "7#9": (X, 0, -1, 0, 1, X), "7b9": (X, 0, -1, 0, -1, X), "13": (X, 0, X, 0, 2, 2),
}
LOW_E, LOW_A = 4, 9  # Their pitch classes.
SHOWN_FRETS = 4  # A diagram's height in frets, or more for a shape that spans more.
HIGHEST_POSITION = 12
WINDOW = 3  # A worked-out shape keeps its fretted notes within four frets.
# A diagram's size, in staff spaces: the gap between strings and between frets, and its gap above the chord symbol.
STRING_GAP, FRET_GAP, ABOVE_TEXT = 1.0, 1.15, 0.6
DOT, MARK = 0.34, 0.3
FINGERS = 4  # A shape needing more fingers than a hand has is played with a barre.
ROOM = 7  # Staff spaces kept free above each system for its diagrams.
DIAGRAM_GAP = 1.5  # The least room between neighbouring diagrams, in staff spaces.
SMALLEST_DIAGRAM = 0.6


def shown(root: ET.Element) -> bool:
    return read_setting(root, DIAGRAMS) == "yes"


def set_shown(root: ET.Element, on: bool) -> None:
    """Show chord diagrams over the chord symbols, or stop. It's kept with the score."""
    write_setting(root, DIAGRAMS, "yes" if on else None)


@dataclass(frozen=True)
class Shape:
    """Where each string is played, from the lowest up: a fret counted from the capo, 0 for open, None for not played."""

    frets: tuple[int | None, ...]

    @property
    def fretted(self) -> list[int]:
        return [fret for fret in self.frets if fret]

    @property
    def first(self) -> int:
        """The fret a diagram starts from: 1 for a shape near the nut, else its lowest fret."""
        return 1 if not self.fretted or max(self.fretted) <= SHOWN_FRETS else min(self.fretted)

    @property
    def barre(self) -> tuple[int, int, int] | None:
        """Return a barre, as its fret and the lowest and highest strings it crosses, counted from the lowest.

        A shape with more fretted notes than four fingers has the first finger lying across its
        lowest fret, from the first string fretted there to the last, if every string between them
        is played no lower.
        """
        if len(self.fretted) <= FINGERS:
            return None
        low = min(self.fretted)
        on = [index for index, fret in enumerate(self.frets) if fret == low]
        if len(on) < 2 or any(fret is None or fret < low for fret in self.frets[on[0]: on[-1] + 1]):
            return None
        return low, on[0], on[-1]


def tones_of(chord: Chord) -> set[int]:
    root = PITCH_CLASSES[chord.root]
    return {(root + interval) % 12 for interval in CHORD_TONES[chord.quality]}


def needed_of(chord: Chord) -> set[int]:
    """The notes a shape can't leave out: the root, the third or the fourth that stands for it, and the seventh."""
    root = PITCH_CLASSES[chord.root]
    intervals = CHORD_TONES[chord.quality]
    return {(root + interval) % 12 for interval in intervals if interval % 12 in (0, 3, 4, 5, 10, 11, 6, 8) and interval < 12}


def moved(chord: Chord, semitones: int) -> Chord:
    def shift(name: str | None) -> str | None:
        return NAMES[(PITCH_CLASSES[name] - semitones) % 12] if name else None

    return Chord(shift(chord.root), chord.quality, shift(chord.bass))


def library_shape(chord: Chord) -> Shape | None:
    """Return the shape a guitarist would learn for a chord in standard tuning, open if there is one."""
    if chord.bass:
        return None
    if str(chord) in OPEN:
        return Shape(OPEN[str(chord)])
    found = []
    for forms, string in ((ON_E, LOW_E), (ON_A, LOW_A)):
        if chord.quality in forms:
            barre = (PITCH_CLASSES[chord.root] - string) % 12
            relative = forms[chord.quality]
            barre += 12 if barre == 0 or barre + min(fret for fret in relative if fret is not None) < 0 else 0
            if barre <= HIGHEST_POSITION + 2:
                found.append(Shape(tuple(None if fret is None else barre + fret for fret in relative)))
    return min(found, key=lambda shape: min(shape.fretted)) if found else None


def worked_out(chord: Chord, opens: tuple[int, ...]) -> Shape | None:
    """Find a shape for any chord on any strings: its bass on the lowest string played, its notes above.

    Strings are played from the bass string up without a gap, each on a note of the chord, and every
    note the chord can't do without is there. The lowest such shape on the neck wins, and then the
    one with the most strings. A ukulele's strings don't go from low to high, so its chords have no
    bass to keep at the bottom.
    """
    tones = tones_of(chord)
    needed = needed_of(chord)
    bass = PITCH_CLASSES[chord.bass] if chord.bass else PITCH_CLASSES[chord.root]
    rising = all(lower < higher for lower, higher in zip(opens, opens[1:]))
    best = None
    for position in range(0, HIGHEST_POSITION + 1):
        frets = range(max(position, 1), position + WINDOW + 1)
        for lowest in range(len(opens) - 1):
            options = []
            for index, sound in enumerate(opens):
                if index < lowest:
                    options.append([None])
                    continue
                choices = [fret for fret in [0, *frets] if (sound + fret) % 12 in (tones if index > lowest or not rising else {bass})]
                options.append(choices if index == lowest else choices or [None])
            for shape in combinations(options):
                if None in shape[lowest:] and any(fret is not None for fret in shape[shape.index(None, lowest):]):
                    continue
                sounding = {(opens[index] + fret) % 12 for index, fret in enumerate(shape) if fret is not None}
                played = sum(fret is not None for fret in shape)
                if not needed <= sounding or played < 3:
                    continue
                score = (position, -played)
                if best is None or score < best[0]:
                    best = (score, Shape(tuple(shape)))
        if best is not None:
            return best[1]
    return None


def combinations(options: list[list]) -> list[tuple]:
    found = [()]
    for choices in options:
        found = [(*made, choice) for made in found for choice in choices]
    return found


@functools.lru_cache(maxsize=512)
def shape_for(text: str, strings: tuple[str, ...] = GUITAR_STRINGS, capo: int = 0) -> Shape | None:
    """Return the shape for a chord symbol on these strings, with this capo."""
    chord = parse_chord(clean_text(text))
    if chord is None:
        return None
    fingered = moved(chord, capo)
    if strings == GUITAR_STRINGS:
        found = library_shape(fingered)
        if found is not None:
            return found
    return worked_out(fingered, tuple(midi_of(*pitch_of_name(name)) for name in strings))


def board_for(root: ET.Element) -> Fretboard | None:
    """The guitar a score's diagrams are for: its first fretted part with six strings or more, else its first fretted part."""
    found = [board for board in boards(root) if board is not None]
    return next((board for board in found if board.count >= 6), found[0] if found else None)


def frame_of(shape: Shape) -> ET.Element:
    """Write a shape as MusicXML's <frame>: strings numbered from the highest, and a barre on its lowest and highest strings."""
    count = len(shape.frets)
    frame = ET.Element("frame")
    ET.SubElement(frame, "frame-strings").text = str(count)
    ET.SubElement(frame, "frame-frets").text = str(max(SHOWN_FRETS, (max(shape.fretted) - shape.first + 1) if shape.fretted else SHOWN_FRETS))
    if shape.first > 1:
        ET.SubElement(frame, "first-fret").text = str(shape.first)
    barre = shape.barre
    for index, fret in enumerate(shape.frets):
        if fret is None:
            continue
        note = ET.SubElement(frame, "frame-note")
        ET.SubElement(note, "string").text = str(count - index)
        ET.SubElement(note, "fret").text = str(fret)
        if barre is not None and fret == barre[0] and index in (barre[1], barre[2]):
            ET.SubElement(note, "barre", type="start" if index == barre[1] else "stop")
    return frame


def shape_of_frame(frame: ET.Element) -> Shape | None:
    """Read a <frame> back as a shape."""
    count = int(frame.findtext("frame-strings") or 0)
    if count <= 0:
        return None
    frets: list[int | None] = [None] * count
    for note in frame.findall("frame-note"):
        string, fret = note.findtext("string"), note.findtext("fret")
        if string and fret and string.isdigit() and fret.isdigit() and 1 <= int(string) <= count:
            frets[count - int(string)] = int(fret)
    return Shape(tuple(frets))


def library_frame(harmony: ET.Element, board: Fretboard | None) -> ET.Element | None:
    chord = chord_from_harmony(harmony)
    shape = shape_for(str(chord), board.names if board else GUITAR_STRINGS, board.capo if board else 0) if chord else None
    return frame_of(shape) if shape else None


def with_frames(root: ET.Element) -> ET.Element:
    """Give each chord symbol its diagram as a <frame>, for a MusicXML file, when the score shows them."""
    if not shown(root):
        return root
    board = board_for(root)
    for harmony in root.iter("harmony"):
        if harmony.find("frame") is None and (frame := library_frame(harmony, board)) is not None:
            position = next((index for index, child in enumerate(harmony) if child.tag in ("offset", "footnote", "level", "staff")), len(harmony))
            harmony.insert(position, frame)
    return root


def keep_imported(root: ET.Element) -> None:
    """Show diagrams for a file that brings them, keeping only the frames that aren't the library's own."""
    frames = [(harmony, harmony.find("frame")) for harmony in root.iter("harmony") if harmony.find("frame") is not None]
    if not frames:
        return
    set_shown(root, True)
    board = board_for(root)
    for harmony, frame in frames:
        made = library_frame(harmony, board)
        if made is not None and shape_of_frame(made) == shape_of_frame(frame):
            harmony.remove(frame)


HARM_GROUP = re.compile(r'<g id="[^"]+" class="harm">')
TEXT_AT = re.compile(r'<text x="(-?\d+)" y="(-?\d+)"')
SIZE = re.compile(r'font-size="(\d+)px"')


@dataclass
class Drawing:
    """The score's chord symbols in the order they're drawn, for drawing their diagrams page by page."""

    harmonies: list[ET.Element] = field(default_factory=list)
    board: Fretboard | None = None
    next: int = 0

    @classmethod
    def of(cls, root: ET.Element) -> "Drawing | None":
        if not shown(root):
            return None
        placed = []
        for part_number, part in enumerate(root.findall("part")):
            for index, measure in enumerate(part.findall("measure")):
                placed.extend((index, part_number, order, harmony) for order, harmony in enumerate(measure.findall("harmony")))
        return cls([item[3] for item in sorted(placed, key=lambda item: item[:3])], board_for(root))

    def shape_of(self, text: str) -> Shape | None:
        """Return the shape for the next chord drawn, the score's own frame if it has one."""
        chord = parse_chord(clean_text(text))
        for ahead in range(self.next, min(self.next + 3, len(self.harmonies))):
            harmony = self.harmonies[ahead]
            if chord is not None and chord_from_harmony(harmony) == chord:
                self.next = ahead + 1
                frame = harmony.find("frame")
                if frame is not None:
                    return shape_of_frame(frame)
                break
        if chord is None:
            return None
        return shape_for(str(chord), self.board.names if self.board else GUITAR_STRINGS, self.board.capo if self.board else 0)


def diagram(shape: Shape, centre: float, bottom: float, space: float) -> str:
    """Draw a fretboard grid with its dots, its open and unplayed strings, and where it starts on the neck."""
    count = len(shape.frets)
    shown_frets = max(SHOWN_FRETS, (max(shape.fretted) - shape.first + 1) if shape.fretted else SHOWN_FRETS)
    width, height = (count - 1) * STRING_GAP * space, shown_frets * FRET_GAP * space
    left, top = centre - width / 2, bottom - height
    thin, thick = 0.08 * space, 0.3 * space
    parts = []
    for index in range(count):
        x = left + index * STRING_GAP * space
        parts.append(f'<path d="M{x:.0f} {top:.0f} L{x:.0f} {bottom:.0f}" stroke="currentColor" stroke-width="{thin:.0f}" />')
    for fret in range(shown_frets + 1):
        y = top + fret * FRET_GAP * space
        weight = thick if fret == 0 and shape.first == 1 else thin
        parts.append(f'<path d="M{left - thin / 2:.0f} {y:.0f} L{left + width + thin / 2:.0f} {y:.0f}" stroke="currentColor" stroke-width="{weight:.0f}" />')
    if shape.first > 1:
        parts.append(f'<text x="{left + width + 0.4 * space:.0f}" y="{top + 0.75 * FRET_GAP * space:.0f}" font-size="{1.2 * space:.0f}px">{shape.first}fr</text>')
    barre = shape.barre
    if barre is not None and barre[2] > barre[1]:
        y = top + (barre[0] - shape.first + 0.5) * FRET_GAP * space
        x1, x2 = left + barre[1] * STRING_GAP * space, left + barre[2] * STRING_GAP * space
        parts.append(f'<path d="M{x1:.0f} {y:.0f} L{x2:.0f} {y:.0f}" stroke="currentColor" stroke-width="{2 * DOT * space:.0f}" stroke-linecap="round" />')
    for index, fret in enumerate(shape.frets):
        x = left + index * STRING_GAP * space
        above = top - 0.7 * space
        if fret is None:
            reach = MARK * space
            parts.append(f'<path d="M{x - reach:.0f} {above - reach:.0f} L{x + reach:.0f} {above + reach:.0f} M{x - reach:.0f} {above + reach:.0f} L{x + reach:.0f} {above - reach:.0f}" stroke="currentColor" stroke-width="{thin:.0f}" />')
        elif fret == 0:
            parts.append(f'<circle cx="{x:.0f}" cy="{above:.0f}" r="{MARK * space:.0f}" fill="none" stroke="currentColor" stroke-width="{thin:.0f}" />')
        elif barre is None or fret != barre[0] or not barre[1] <= index <= barre[2]:
            y = top + (fret - shape.first + 0.5) * FRET_GAP * space
            parts.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{DOT * space:.0f}" />')
    return f'<g class="xrn-diagram">{"".join(parts)}</g>'


def harm_text(piece: str) -> str:
    return "".join(re.findall(r">([^<>]+)</tspan>", piece)).strip()


def draw(svg: str, drawing: Drawing | None, space: float) -> str:
    """Draw each chord symbol's diagram above it, inside its own group so a click on either chooses the chord.

    Chords close together on a line get smaller diagrams, so that neighbours don't run into each other.
    """
    if drawing is None:
        return svg
    placed = []
    for match in HARM_GROUP.finditer(svg):
        end = svg.find("</g>", match.end())
        piece = svg[match.end(): end]
        text, where = harm_text(piece), TEXT_AT.search(piece)
        shape = drawing.shape_of(text)
        if shape is None or where is None:
            continue
        sizes = [float(found) for found in SIZE.findall(piece) if float(found)]
        size = max(sizes) if sizes else 2 * space
        centre = float(where.group(1)) + len(text) * 0.55 * size / 2
        placed.append((end, shape, centre, float(where.group(2)) - 0.8 * size - ABOVE_TEXT * space))
    pieces = []
    for number, (end, shape, centre, bottom) in enumerate(placed):
        neighbours = [other[2] for other in (placed[number - 1] if number else None, placed[number + 1] if number + 1 < len(placed) else None) if other is not None and abs(other[3] - bottom) < space]
        room = min((abs(other - centre) for other in neighbours), default=float("inf"))
        wanted = ((len(shape.frets) - 1) * STRING_GAP + DIAGRAM_GAP) * space
        scale = max(min(room / wanted, 1.0), SMALLEST_DIAGRAM)
        pieces.append((end, diagram(shape, centre, bottom, space * scale)))
    for end, drawn in reversed(pieces):
        svg = svg[:end] + drawn + svg[end:]
    return svg

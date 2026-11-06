"""What chord symbols look like on a chart, and what each spelling means.

One chord can be printed many ways: Cmaj7, CM7 and CΔ7 are the same chord. The model
transcribes the characters it sees, and parse_chord turns those into a single meaning.
"""

import re
from dataclasses import dataclass

NATURAL_ROOTS = ["C", "D", "E", "F", "G", "A", "B"]
ROOTS = ["C", "Db", "D", "Eb", "E", "F", "F#", "Gb", "G", "Ab", "A", "Bb", "B", "C#", "G#", "D#", "A#"]

# meaning: (how common it is on a jazz chart, spellings charts use)
QUALITIES: dict[str, tuple[float, list[str]]] = {
    "": (10, [""]),
    "m": (6, ["m", "min", "-", "mi"]),
    "7": (14, ["7"]),
    "maj7": (10, ["maj7", "Maj7", "M7", "ma7", "Δ7", "Δ"]),
    "m7": (14, ["m7", "min7", "-7", "mi7"]),
    "m7b5": (5, ["m7b5", "m7(b5)", "ø", "ø7", "-7b5", "min7b5"]),
    "dim": (1, ["dim", "°"]),
    "dim7": (2, ["dim7", "°7", "o7"]),
    "aug": (1, ["aug", "+"]),
    "7#5": (1, ["7#5", "7+", "+7", "aug7", "7(#5)"]),
    "sus4": (1, ["sus4", "sus"]),
    "7sus4": (3, ["7sus4", "7sus"]),
    "9sus4": (1, ["9sus4", "9sus"]),
    "6": (3, ["6"]),
    "m6": (2, ["m6", "-6", "min6"]),
    "6/9": (2, ["6/9", "69"]),
    "9": (4, ["9"]),
    "m9": (3, ["m9", "-9", "min9"]),
    "maj9": (2, ["maj9", "Maj9", "M9", "Δ9"]),
    "11": (0.5, ["11"]),
    "m11": (1, ["m11", "-11", "min11"]),
    "13": (3, ["13"]),
    "7b9": (4, ["7b9", "7(b9)"]),
    "7#9": (2, ["7#9", "7(#9)"]),
    "7#11": (2, ["7#11", "7(#11)"]),
    "7b13": (1, ["7b13", "7(b13)"]),
    "7alt": (2, ["7alt", "alt"]),
    "13b9": (1, ["13b9", "13(b9)"]),
    "13#11": (1, ["13#11", "13(#11)"]),
    "maj7#11": (1, ["maj7#11", "Maj7#11", "Δ7#11", "M7#11"]),
    "m(maj7)": (1, ["m(maj7)", "mMaj7", "-Δ7", "mM7"]),
    "add9": (1, ["add9", "(add9)"]),
}

SPELLING_TO_MEANING = {
    spelling: meaning
    for meaning, (_, spellings) in QUALITIES.items()
    for spelling in spellings
}

# Everything the model can output. Flats and sharps are written b and # whether the
# chart prints them as letters or as music glyphs.
ALPHABET = sorted({c for spelling in SPELLING_TO_MEANING for c in spelling} | set("ABCDEFG#b/"))

CHORD_PATTERN = re.compile(r"^(?P<root>[A-G][b#]?)(?P<quality>.*?)(?:/(?P<bass>[A-G][b#]?))?$")


@dataclass(frozen=True)
class Chord:
    root: str
    quality: str
    bass: str | None = None

    def __str__(self) -> str:
        return f"{self.root}{self.quality}" + (f"/{self.bass}" if self.bass else "")


def clean_text(text: str) -> str:
    """Convert music glyphs and look-alike characters to the characters the parser knows."""
    for glyph, plain in (("♭", "b"), ("♯", "#"), ("△", "Δ"), ("∆", "Δ"), ("Ø", "ø"), ("º", "°")):
        text = text.replace(glyph, plain)
    return text.replace(" ", "").strip()


def parse_chord(text: str) -> Chord | None:
    """Return the chord this text spells, or None if it isn't a chord."""
    match = CHORD_PATTERN.match(clean_text(text))
    if match is None or match["quality"] not in SPELLING_TO_MEANING:
        return None
    return Chord(match["root"], SPELLING_TO_MEANING[match["quality"]], match["bass"])

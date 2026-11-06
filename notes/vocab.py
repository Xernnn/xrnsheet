"""The symbols the note reader can output.

Tokens describe what's printed, not what it means: a note is a staff position and a length.
The clef, key signature and accidentals turn that into a pitch afterwards, using the rules
of notation. That leaves the model to learn looking, and the rules stay exact.

Positions count staff steps from the bottom line, so 0 is the bottom line, 1 the space above
it, and 8 the top line.
"""

from dataclasses import dataclass

LENGTHS = {"1": 4.0, "2": 2.0, "4": 1.0, "8": 0.5, "16": 0.25}
LENGTH_NAMES = {"1": "whole", "2": "half", "4": "quarter", "8": "eighth", "16": "16th"}
LOWEST_POSITION = -7
HIGHEST_POSITION = 15
TIMES = ["4/4", "3/4", "2/4", "6/8", "2/2", "12/8", "3/8"]
ACCIDENTALS = {"sharp": 1, "flat": -1, "natural": 0}
STEPS = "CDEFGAB"
SHARP_ORDER = "FCGDAEB"
LOWEST = {"G": ("E", 4), "F": ("G", 2)}  # The note on the bottom staff line in each clef.


@dataclass
class Reading:
    """What the model read from one staff."""

    tokens: list[str]
    xs: list[float]  # Strip x of each token, which is how notes get a place on the page.
    confidences: list[float]


def length_codes() -> list[str]:
    return [*LENGTHS, *[f"{code}." for code in LENGTHS]]


TOKENS = [
    "<blank>",
    "bar",
    "triplet",  # The next three notes share the time of two.
    "clef:G",
    "clef:F",
    *[f"key:{fifths}" for fifths in range(-7, 8) if fifths != 0],
    *[f"time:{time}" for time in TIMES],
    *[f"acc:{name}" for name in ACCIDENTALS],
    *[f"rest:{code}" for code in length_codes()],
    *[f"note:{position}:{code}" for position in range(LOWEST_POSITION, HIGHEST_POSITION + 1) for code in length_codes()],
]
BLANK = 0
TOKEN_INDEX = {token: index for index, token in enumerate(TOKENS)}


def length_of(code: str) -> float:
    """Return a length code's duration in quarter notes."""
    if code.endswith("."):
        return LENGTHS[code[:-1]] * 1.5
    return LENGTHS[code]


def position_to_pitch(clef: str, position: int) -> tuple[str, int]:
    """Return the step and octave at a staff position."""
    step, octave = LOWEST[clef]
    index = STEPS.index(step) + 7 * octave + position
    return STEPS[index % 7], index // 7


def key_alteration(fifths: int, step: str) -> int:
    """Return the sharp or flat a key signature puts on a step."""
    if fifths > 0 and step in SHARP_ORDER[:fifths]:
        return 1
    if fifths < 0 and step in SHARP_ORDER[::-1][:-fifths]:
        return -1
    return 0

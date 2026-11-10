"""Put right two recognition mistakes that the notes themselves give away.

Neither repair looks at the page or changes a note. homr can misread a time signature, which
leaves every bar the wrong length even though each one was read correctly. It can also lose the
step back in time between two staves playing together, so one staff starts late and the bar runs
long. The note scores can't see either mistake, but the bar check can.
"""

import xml.etree.ElementTree as ET
from collections import Counter
from fractions import Fraction

from pipeline.rhythm import Event, contexts, line_end, read_measure, write_measure
from pipeline.structure import set_time

FEWEST_BARS = 4  # Too few bars can't outvote a signature.
SHARE_THAT_MUST_AGREE = 0.6  # Misread bars disagree with each other, so most agreeing is rare by chance.
LARGEST_BEATS = 12


def voice_length(line: list[Event]) -> Fraction:
    """Return how long a voice's notes last, which doesn't depend on where they were placed."""
    return sum((event.length for event in line), Fraction(0))


def measured_lengths(root: ET.Element) -> list[Fraction]:
    """Return how long the notes run in each bar that can say anything about the signature.

    Empty bars can't, because a whole-bar rest stretches to fit whatever was read. Neither can the
    first and last bars, because a pickup and the bar that makes up for it are meant to be short.
    """
    found = []
    for part in root.findall("part"):
        for context in list(contexts(part))[1:-1]:
            lines = read_measure(context.measure, context.divisions).lines.values()
            sounding = [line for line in lines if any(not event.is_rest for event in line)]
            if sounding:
                found.append(max(voice_length(line) for line in sounding))
    return found


def agreed_length(root: ET.Element) -> Fraction | None:
    lengths = measured_lengths(root)
    if len(lengths) < FEWEST_BARS:
        return None
    length, votes = Counter(lengths).most_common(1)[0]
    return length if votes / len(lengths) >= SHARE_THAT_MUST_AGREE else None


def signature_for(length: Fraction, beat_type: int) -> tuple[int, int] | None:
    """Return a signature of this length, keeping the kind of beat that was read if it can."""
    for unit in dict.fromkeys((beat_type, 4, 8)):
        beats = length * unit / 4
        if beats.denominator == 1 and 1 <= beats <= LARGEST_BEATS:
            return int(beats), unit
    return None


def mark_bar_rests(root: ET.Element, length: Fraction) -> None:
    """Mark rests that fill a bar, so they change size along with it."""
    for part in root.findall("part"):
        for context in contexts(part):
            for line in read_measure(context.measure, context.divisions).lines.values():
                if len(line) == 1 and line[0].is_rest and line[0].length == length:
                    line[0].notes[0].find("rest").set("measure", "yes")


def fix_time_signature(root: ET.Element) -> None:
    """Change the signature if most bars agree on another length.

    The staves of one score can be read differently, so a part is judged on its own. A part that
    really does change signature is left alone, and so is the whole score then.
    """
    parts = [list(contexts(part)) for part in root.findall("part")]
    if any(len({(context.beats, context.beat_type) for context in known}) != 1 for known in parts):
        return
    length = agreed_length(root)
    if length is None or all(known[0].length == length for known in parts):
        return
    signature = signature_for(length, parts[0][0].beat_type)
    if signature is not None:
        for known in parts:
            mark_bar_rests(root, known[0].length)
        set_time(root, 0, *signature)


def lay_out_voices(root: ET.Element) -> None:
    """Close the gaps in voices that overrun their bar, if that makes the whole bar fit.

    A voice that fits keeps its gaps, because those can be real. A bar that can't be made to fit
    is left as it is for the bar check to flag, since moving part of it only hides the problem.
    """
    for part in root.findall("part"):
        for context in contexts(part):
            timeline = read_measure(context.measure, context.divisions)
            overrun = [line for line in timeline.lines.values() if line_end(line) > context.length]
            if overrun and all(voice_length(line) <= context.length for line in overrun):
                for line in overrun:
                    position = Fraction(0)
                    for event in sorted(line, key=lambda item: item.onset):
                        event.onset = position
                        position += event.length
                write_measure(context.measure, timeline, context.divisions)


def repair(root: ET.Element) -> None:
    fix_time_signature(root)
    lay_out_voices(root)

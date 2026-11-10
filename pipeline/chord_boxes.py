"""Turn a text detector's boxes into one box per chord.

Text detectors are built for sentences, and chord symbols break them in two opposite ways.
In type, neighbouring chords look like words on a line, so they get glued into one box. In
handwriting, the gap after a root looks like the end of a word, so one chord gets broken
into pieces. A better reader can't fix either, because the box itself is wrong.

Only boxes that failed to read as a chord get changed, so a clean page passes straight through.
"""

from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

from chords.vocab import parse_chord
from pipeline import align

Box = tuple[float, float, float, float]
Read = Callable[[list[np.ndarray]], list[tuple[str, float]]]

MINIMUM_CONFIDENCE = 0.4
PADDING = 3
SMALLEST_BOX = 6
# Gaps inside one printed chord are narrower than this share of its height.
SPLIT_GAP = 0.22
MOST_PIECES = 10  # Every way of grouping them is read, and a box of 7 can hold 5 chords.
# A fragment joins the chord on its left if it's this close, as a share of that chord's height.
JOIN_DISTANCE = 0.9
SMALLEST_FRAGMENT = 0.3
# Below the staff above, boxes are kept only if this share of them are chords. Lyrics fail this.
CHORD_ROW_SHARE = 0.4
# Chords on a line share a height, so a box this far off it (in chord heights) is something else.
ROW_TOLERANCE = 0.9


@dataclass
class Candidate:
    edges: Box
    system: int
    below_staff: bool  # Closer to the staff above it, which is where lyrics sit.
    text: str = ""
    confidence: float = 0.0
    dropped: bool = False

    @property
    def is_chord(self) -> bool:
        return not self.dropped and self.confidence >= MINIMUM_CONFIDENCE and parse_chord(self.text) is not None


def crop(page: np.ndarray, edges: Box, padding: int = PADDING) -> np.ndarray:
    left, top, right, bottom = edges
    height, width = page.shape[:2]
    return page[max(0, int(top) - padding) : min(height, int(bottom) + padding), max(0, int(left) - padding) : min(width, int(right) + padding)]


def ink_runs(image: np.ndarray, smallest_gap: int) -> list[tuple[int, int]]:
    """Return the first and last column of each stretch of ink.

    A column with only a few dark pixels counts as empty, so a thin slur crossing the box
    doesn't hide a gap.
    """
    _, binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    ink = np.count_nonzero(binary, axis=0)
    columns = np.flatnonzero(ink > max(1, int(image.shape[0] * 0.04)))
    runs: list[list[int]] = []
    for column in columns.tolist():
        if runs and column - runs[-1][1] - 1 < smallest_gap:
            runs[-1][1] = column
        else:
            runs.append([column, column])
    return [(first, last) for first, last in runs]


def best_partition(count: int, is_chord: dict[tuple[int, int], bool]) -> list[tuple[int, int]]:
    """Choose spans of pieces that read as chords, covering the most pieces with the fewest spans.

    Fewest spans matters because a wide gap can sit inside one chord: "C" and "maj7" should
    stay together even though the gap between them could also be a cut.
    """
    best: list[tuple[int, int, list[tuple[int, int]]]] = [(0, 0, [])]
    for end in range(1, count + 1):
        options = [best[end - 1]]
        for start in range(end):
            if is_chord.get((start, end)):
                covered, spans, chosen = best[start]
                options.append((covered + end - start, spans - 1, [*chosen, (start, end)]))
        best.append(max(options, key=lambda option: option[:2]))
    return best[count][2]


def split_glued(page: np.ndarray, candidates: list[Candidate], read: Read) -> list[Candidate]:
    """Cut boxes that hold several chords at their white gaps, then read the pieces."""
    results = []
    for candidate in candidates:
        results.append(candidate)
        if candidate.is_chord:
            continue
        image = crop(page, candidate.edges, padding=0)
        if min(image.shape) < SMALLEST_BOX:
            continue
        runs = ink_runs(image, max(4, int(SPLIT_GAP * image.shape[0])))
        if not 2 <= len(runs) <= MOST_PIECES:
            continue
        spans = [(start, end) for start in range(len(runs)) for end in range(start + 1, len(runs) + 1)]
        pieces = [image[:, max(0, runs[start][0] - PADDING) : runs[end - 1][1] + PADDING + 1] for start, end in spans]
        readings = dict(zip(spans, read(pieces)))
        chosen = best_partition(
            len(runs),
            {span: confidence >= MINIMUM_CONFIDENCE and parse_chord(text) is not None for span, (text, confidence) in readings.items()},
        )
        if not chosen:
            continue
        candidate.dropped = True
        left, top, _, bottom = candidate.edges
        for start, end in chosen:
            text, confidence = readings[(start, end)]
            edges = (left + runs[start][0], top, left + runs[end - 1][1], bottom)
            results.append(Candidate(edges, candidate.system, candidate.below_staff, text, confidence))
    return results


def join(first: Box, second: Box) -> Box:
    return (min(first[0], second[0]), min(first[1], second[1]), max(first[2], second[2]), max(first[3], second[3]))


def merge_fragments(page: np.ndarray, candidates: list[Candidate], read: Read) -> None:
    """Attach pieces that can't be chords, like a lone "7", to the chord on their left."""
    groups: dict[int, tuple[Candidate, Box, list[Candidate]]] = {}
    for system in {candidate.system for candidate in candidates}:
        line = sorted((c for c in candidates if c.system == system and not c.dropped), key=lambda c: c.edges[0])
        for index, fragment in enumerate(line):
            if fragment.is_chord:
                continue
            for head in reversed(line[:index]):
                if not head.is_chord:
                    continue
                edges = groups[id(head)][1] if id(head) in groups else head.edges
                height = head.edges[3] - head.edges[1]
                if fragment.edges[0] - edges[2] > JOIN_DISTANCE * height:
                    break
                tall_enough = fragment.edges[3] - fragment.edges[1] >= SMALLEST_FRAGMENT * height
                level = min(edges[3], fragment.edges[3]) > max(edges[1], fragment.edges[1])
                if tall_enough and level:
                    parts = groups[id(head)][2] if id(head) in groups else []
                    groups[id(head)] = (head, join(edges, fragment.edges), [*parts, fragment])
                break
    merged = list(groups.values())
    if not merged:
        return
    for (head, edges, parts), (text, confidence) in zip(merged, read([crop(page, edges) for _, edges, _ in merged])):
        if confidence >= MINIMUM_CONFIDENCE and parse_chord(text) is not None:
            head.edges, head.text, head.confidence = edges, text, confidence
            for part in parts:
                part.dropped = True


def reject_stray_rows(candidates: list[Candidate]) -> None:
    """Judge a line of text together, because one box can't show what it's part of.

    "Am" could be a chord or a lyric, but a row of mostly chords is chords. Lyrics are told
    apart this way instead of by distance, which fails on pages with tight staves.
    """
    for system in {candidate.system for candidate in candidates}:
        line = [c for c in candidates if c.system == system and not c.dropped]
        below = [c for c in line if c.below_staff]
        if below and sum(c.is_chord for c in below) / len(below) < CHORD_ROW_SHARE:
            for candidate in below:
                candidate.dropped = True
        chords = [c for c in line if c.is_chord]
        if len(chords) >= 3:
            centers = [(c.edges[1] + c.edges[3]) / 2 for c in chords]
            middle = float(np.median(centers))
            height = float(np.median([c.edges[3] - c.edges[1] for c in chords]))
            for candidate, center in zip(chords, centers):
                if abs(center - middle) > ROW_TOLERANCE * height:
                    candidate.dropped = True


def find_chords(page: np.ndarray, boxes: list[Box], layouts: list[align.SystemLayout], read: Read) -> list[Candidate]:
    """Return one candidate per chord found on a grayscale page, in no particular order."""
    candidates = []
    for edges in boxes:
        system = align.find_system(edges, layouts)
        if system is not None and min(edges[2] - edges[0], edges[3] - edges[1]) >= SMALLEST_BOX:
            candidates.append(Candidate(edges, system, align.closer_to_staff_above(edges, layouts, system)))
    if not candidates:
        return []
    for candidate, (text, confidence) in zip(candidates, read([crop(page, c.edges) for c in candidates])):
        candidate.text, candidate.confidence = text, confidence
    candidates = split_glued(page, candidates, read)
    merge_fragments(page, candidates, read)
    reject_stray_rows(candidates)
    return [candidate for candidate in candidates if candidate.is_chord]

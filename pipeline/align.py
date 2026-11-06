"""Work out which beat a chord belongs to from where it sits on the page.

Each detected chord has an x position, the staff gives barline and notehead positions, and
the recognized score lists each measure's onsets. Matching those two descriptions of the
same line of music tells us the measure and beat under a chord.
"""

from dataclasses import dataclass

import numpy as np

from pipeline.musicxml import Measure


@dataclass
class SystemLayout:
    """One line of music as seen on the page."""

    left: float
    right: float
    top: float
    bottom: float
    unit: float  # distance between staff lines
    bars: list[float]
    notes: list[float]


def note_columns(notes: list[float], unit: float) -> list[float]:
    """Merge noteheads stacked in a chord into one horizontal position."""
    columns: list[list[float]] = []
    for x in sorted(notes):
        if columns and x - columns[-1][-1] < unit:
            columns[-1].append(x)
        else:
            columns.append([x])
    return [float(np.mean(column)) for column in columns]


def measure_edges(layout: SystemLayout, measures: list[Measure]) -> list[float]:
    """Return the x position of each measure boundary on this line."""
    margin = layout.unit * 2
    inner = sorted(x for x in layout.bars if layout.left + margin < x < layout.right - margin)
    merged: list[float] = []
    for x in inner:  # A repeat sign or double bar is two barlines close together.
        if not merged or x - merged[-1] > layout.unit * 2:
            merged.append(x)
    if len(merged) == len(measures) - 1:
        return [layout.left, *merged, layout.right]

    # The barline count disagrees with the score, so divide the line by notes instead.
    columns = note_columns(layout.notes, layout.unit)
    onsets = [sum(not event.is_rest for event in measure.events) for measure in measures]
    if columns and sum(onsets) == len(columns):
        edges = [layout.left]
        seen = 0
        for count in onsets[:-1]:
            seen += count
            edges.append((columns[seen - 1] + columns[seen]) / 2 if 0 < seen < len(columns) else edges[-1])
        return [*edges, layout.right]

    total = sum(measure.duration for measure in measures) or 1.0
    widths = np.cumsum([measure.duration for measure in measures]) / total
    return [layout.left, *(layout.left + (layout.right - layout.left) * widths).tolist()]


def event_positions(measure: Measure, left: float, right: float, columns: list[float]) -> list[float]:
    """Estimate each onset's x. Rests have no notehead, so they're interpolated."""
    if not measure.events:
        return []
    onsets = [event.onset for event in measure.events]
    pitched = [event.onset for event in measure.events if not event.is_rest]
    inside = [x for x in columns if left <= x < right]
    if pitched and len(inside) == len(pitched):
        return np.interp(onsets, pitched, inside).tolist() if len(pitched) > 1 else [
            inside[0] + (onset - pitched[0]) / (measure.duration or 1) * (right - left) for onset in onsets
        ]
    return [left + onset / (measure.duration or 1) * (right - left) for onset in onsets]


def place_chord(x: float, layout: SystemLayout, measures: list[Measure]) -> tuple[int, float]:
    """Return (index into measures, onset) for a chord whose left edge is at x."""
    edges = measure_edges(layout, measures)
    index = int(np.clip(np.searchsorted(edges, x, side="right") - 1, 0, len(measures) - 1))
    measure = measures[index]
    positions = event_positions(measure, edges[index], edges[index + 1], note_columns(layout.notes, layout.unit))
    if not positions:
        return index, 0.0
    nearest = int(np.argmin(np.abs(np.array(positions) - x)))
    return index, measure.events[nearest].onset


def split_into_systems(measures: list[Measure]) -> list[list[Measure]]:
    systems: list[list[Measure]] = []
    for measure in measures:
        if measure.starts_system or not systems:
            systems.append([])
        systems[-1].append(measure)
    return systems


def find_system(box: tuple[float, float, float, float], layouts: list[SystemLayout]) -> int | None:
    """Return the line of music a text box sits above, or None if it can't be a chord."""
    bottom = box[3]
    for index, layout in enumerate(layouts):
        if bottom > layout.top + layout.unit:
            continue
        return None if layout.top - bottom > layout.unit * 8 else index
    return None


def closer_to_staff_above(box: tuple[float, float, float, float], layouts: list[SystemLayout], system: int) -> bool:
    """Tell whether a box sits nearer the staff above, where a lyric would be."""
    if system == 0:
        return False
    center = (box[1] + box[3]) / 2
    return center - layouts[system - 1].bottom < layouts[system].top - center

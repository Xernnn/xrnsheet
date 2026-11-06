"""Read a whole page with our own note reader, without homr.

Unlike homr 0.7, this knows where its barlines and notes sit on the page, because the model
marks where each token fired. The chord placement code can then use real positions.
"""

from functools import lru_cache

import cv2
import numpy as np

from chords.fonts import ROOT
from notes.build import build_score
from notes.model import load_reader, read_strips
from notes.staves import crop_staff, find_staves, straighten
from pipeline.align import SystemLayout

MODEL_PATH = ROOT / "models" / "note_reader.pt"


@lru_cache(maxsize=1)
def note_model(device: str):
    return load_reader(MODEL_PATH, device)


def read_page(image: np.ndarray, device: str = "cpu"):
    """Return the score, one layout per line of music, and the straightened page."""
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    level = straighten(gray)
    staves = find_staves(level)
    if not staves:
        raise ValueError("No staffs found")
    cuts = [crop_staff(level, staff) for staff in staves]
    readings = read_strips(note_model(device), [strip for strip, _, _ in cuts], device)
    root, kept, all_marks = build_score(readings)
    layouts = []
    for index, marks in zip(kept, all_marks):
        staff = staves[index]
        _, left, scale = cuts[index]
        sample = np.linspace(staff.left, staff.right, 9)
        layouts.append(
            SystemLayout(
                left=staff.left,
                right=staff.right,
                top=float(np.median(np.polyval(staff.top, sample))),
                bottom=float(np.median(np.polyval(staff.bottom, sample))),
                unit=staff.spacing,
                bars=[left + x / scale for x in marks.bars],
                notes=[left + x / scale for x in marks.notes],
            )
        )
    return root, layouts, cv2.cvtColor(level, cv2.COLOR_GRAY2BGR)

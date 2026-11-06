"""Find staves on a page and cut each into a flat strip for the note reader.

This is plain image processing, so it brings no licence restrictions. A photographed staff
isn't straight, so it's traced in narrow vertical slices, where five dark rows still look
like a comb. A smooth curve through those slices then follows the bend of the page, and
cutting along that curve gives a level strip.
"""

from dataclasses import dataclass

import cv2
import numpy as np

STRIP_HEIGHT = 96
SPACES_ABOVE = 4  # Room kept above and below the staff, which high and low notes need.
MAX_STRIP_WIDTH = 2000
MOST_TILT = 10.0
SLICES = 48
LINE_SHARE = 0.45  # A staff line fills at least this much of a slice.
SHORTEST_STAFF = 0.12  # Share of the page width.
CURVE_DEGREE = 2
SAME_TRACK = 0.6  # A comb this many spaces away is the same staff. A false one is a full space off.
OVERLAP = 4.5  # Two staves can't sit closer than this many spaces.


@dataclass
class Staff:
    top: np.ndarray  # Coefficients of the curve along the top line, for np.polyval.
    bottom: np.ndarray
    left: float
    right: float

    @property
    def spacing(self) -> float:
        middle = (self.left + self.right) / 2
        return float(np.polyval(self.bottom, middle) - np.polyval(self.top, middle)) / 4

    def top_at(self, x: float) -> float:
        return float(np.polyval(self.top, x))

    def bottom_at(self, x: float) -> float:
        return float(np.polyval(self.bottom, x))


def to_ink(page: np.ndarray) -> np.ndarray:
    """Mark ink as 1, allowing for shadows that make a single threshold fail."""
    size = max(31, page.shape[1] // 40) | 1
    return cv2.adaptiveThreshold(page, 1, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, size, 12)


def straighten(page: np.ndarray) -> np.ndarray:
    """Rotate a tilted page roughly level, choosing the angle with the sharpest rows."""
    small = cv2.resize(page, None, fx=0.2, fy=0.2, interpolation=cv2.INTER_AREA)
    ink = to_ink(small).astype(np.float32)
    height, width = ink.shape
    best_angle, best_sharpness = 0.0, -1.0
    for angle in np.arange(-MOST_TILT, MOST_TILT + 0.01, 0.5):
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), float(angle), 1.0)
        rows = cv2.warpAffine(ink, matrix, (width, height)).sum(axis=1)
        sharpness = float(np.square(np.diff(rows)).sum())
        if sharpness > best_sharpness:
            best_angle, best_sharpness = float(angle), sharpness
    if abs(best_angle) < 1.0:
        return page  # The curves handle small tilts, without resampling the image.
    matrix = cv2.getRotationMatrix2D((page.shape[1] / 2, page.shape[0] / 2), best_angle, 1.0)
    return cv2.warpAffine(page, matrix, (page.shape[1], page.shape[0]), flags=cv2.INTER_LINEAR, borderValue=255)


def line_rows(ink: np.ndarray) -> list[float]:
    """Return the center of each dark row that's long enough to be a staff line."""
    rows = np.flatnonzero(ink.sum(axis=1) >= LINE_SHARE * ink.shape[1]).tolist()
    groups: list[list[int]] = []
    for row in rows:
        if groups and row - groups[-1][-1] <= 1:
            groups[-1].append(row)
        else:
            groups.append([row])
    return [float(np.mean(group)) for group in groups]


def group_into_staves(rows: list[float]) -> list[tuple[float, float]]:
    """Return (top, bottom) for each run of five evenly spaced rows."""
    staves = []
    index = 0
    while index + 4 < len(rows):
        gaps = np.diff(rows[index : index + 5])
        if gaps.min() >= 3 and gaps.max() <= gaps.min() * 1.4:
            staves.append((rows[index], rows[index + 4]))
            index += 5
        else:
            index += 1
    return staves


def slice_combs(ink: np.ndarray) -> list[tuple[float, float, float]]:
    """Return (x, top, bottom) for every staff found in each vertical slice."""
    width = ink.shape[1]
    slice_width = max(16, width // SLICES)
    found = []
    for left in range(0, width - slice_width + 1, slice_width):
        piece = ink[:, left : left + slice_width]
        found.extend((left + slice_width / 2, top, bottom) for top, bottom in group_into_staves(line_rows(piece)))
    return found


def chain_tracks(combs: list[tuple[float, float, float]]) -> list[list[tuple[float, float, float]]]:
    """Join combs from neighbouring slices into one track per staff."""
    tracks: list[list[tuple[float, float, float]]] = []
    for x, top, bottom in sorted(combs):
        spacing = (bottom - top) / 4
        best, best_gap = None, spacing * SAME_TRACK
        for track in tracks:
            recent = track[-6:]
            last_x, last_top, last_bottom = recent[-1]
            if x <= last_x or abs((last_bottom - last_top) / 4 - spacing) > spacing * 0.25:
                continue
            slope = np.polyfit([p[0] for p in recent], [p[1] for p in recent], 1)[0] if len(recent) >= 3 else 0.0
            gap = abs(last_top + slope * (x - last_x) - top)
            if gap < best_gap:
                best, best_gap = track, gap
        if best is None:
            tracks.append([(x, top, bottom)])
        else:
            best.append((x, top, bottom))
    return tracks


def keep_one_track_per_staff(tracks: list[list[tuple[float, float, float]]]) -> list[list[tuple[float, float, float]]]:
    """Drop false staves, which are never as long as the real ones.

    A ledger line or beam beside four staff lines also looks like a staff, one or two lines
    away from the real one. So tracks are taken longest first, and a track that overlaps a
    staff already taken is either the same staff, resumed after a gap, or a false one.
    """
    kept: list[list[tuple[float, float, float]]] = []
    for track in sorted(tracks, key=len, reverse=True):
        xs, tops, bottoms = map(np.array, zip(*track))
        spacing = float(np.median(bottoms - tops)) / 4
        for staff in kept:
            line = np.polyfit([point[0] for point in staff], [point[1] for point in staff], 1)
            distance = float(np.median(np.abs(np.polyval(line, xs) - tops)))
            if distance < OVERLAP * spacing:
                if distance < SAME_TRACK * spacing:
                    staff.extend(track)
                    staff.sort()
                break
        else:
            kept.append(list(track))
    return kept


def fit_curve(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Fit a gentle curve, then refit without any points that sit far from it."""
    degree = min(CURVE_DEGREE, len(xs) - 1)
    curve = np.polyfit(xs, ys, degree)
    errors = np.abs(np.polyval(curve, xs) - ys)
    keep = errors <= max(2.0, 2.5 * float(np.median(errors)))
    return np.polyfit(xs[keep], ys[keep], degree) if keep.sum() > degree else curve


def line_rows_at(staff: Staff, x: float) -> list[float]:
    top, bottom = staff.top_at(x), staff.bottom_at(x)
    return [top + (bottom - top) * line / 4 for line in range(5)]


def staff_end(ink: np.ndarray, staff: Staff, start: float, step: int) -> float:
    """Walk along the staff from a known point until its lines stop."""
    height, width = ink.shape
    missing = 0
    x = int(start)
    last = x
    longest_gap = max(4, int(staff.spacing / 2))
    while 0 <= x < width:
        hits = 0
        for row in line_rows_at(staff, x):
            row = int(round(row))
            hits += 0 <= row < height and ink[max(0, row - 2) : row + 3, x].any()
        if hits >= 4:
            last, missing = x, 0
        else:
            missing += 1
            if missing > longest_gap:
                break
        x += step
    return float(last)


def find_staves(page: np.ndarray) -> list[Staff]:
    """Return the staves on a grayscale page, from top to bottom."""
    ink = to_ink(page)
    # Keep only long horizontal strokes, which clears away noteheads, stems and text.
    length = max(9, page.shape[1] // SLICES // 2)
    lines = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, length), np.uint8))
    staves = []
    tracks = [track for track in chain_tracks(slice_combs(lines)) if len(track) >= 2]
    for track in keep_one_track_per_staff(tracks):
        xs, tops, bottoms = map(np.array, zip(*track))
        if len(track) < 3 or xs[-1] - xs[0] < SHORTEST_STAFF * page.shape[1]:
            continue
        rough = Staff(fit_curve(xs, tops), fit_curve(xs, bottoms), float(xs[0]), float(xs[-1]))
        left = staff_end(ink, rough, xs[0], -1)
        right = staff_end(ink, rough, xs[-1], 1)
        staves.append(Staff(rough.top, rough.bottom, left, right))
    return sorted(staves, key=lambda staff: staff.top_at((staff.left + staff.right) / 2))


def crop_staff(page: np.ndarray, staff: Staff) -> tuple[np.ndarray, float, float]:
    """Cut one staff into a level strip of fixed height.

    Returns the strip, plus the page x of its left edge and the horizontal scale, so that
    a position in the strip can be put back on the page.
    """
    spacing = staff.spacing
    left = max(0.0, staff.left - spacing)
    right = min(float(page.shape[1] - 1), staff.right + spacing)
    tall = (4 + 2 * SPACES_ABOVE) * spacing
    scale = STRIP_HEIGHT / tall
    width = int(min(MAX_STRIP_WIDTH, max(STRIP_HEIGHT, round((right - left) * scale))))

    xs = left + (np.arange(width, dtype=np.float32) + 0.5) * (right - left) / width
    tops = np.polyval(staff.top, xs).astype(np.float32)
    bottoms = np.polyval(staff.bottom, xs).astype(np.float32)
    margins = (bottoms - tops) / 4 * SPACES_ABOVE
    fractions = (np.arange(STRIP_HEIGHT, dtype=np.float32) + 0.5) / STRIP_HEIGHT
    map_y = (tops - margins)[None, :] + fractions[:, None] * (bottoms + margins - tops + margins)[None, :]
    map_x = np.repeat(xs[None, :], STRIP_HEIGHT, axis=0)

    # The strip is usually smaller than the page, so blur first to keep thin lines from aliasing.
    source = cv2.GaussianBlur(page, (0, 0), 0.45 / scale) if scale < 0.75 else page
    strip = cv2.remap(source, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    return strip, left, width / (right - left)

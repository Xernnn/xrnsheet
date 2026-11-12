"""Page and style settings: the paper, its margins, the size of the staff, and the room between staves and systems.

They're kept in the score as MusicXML keeps them, in <defaults>, so they go wherever the score goes
and other programs read them. MusicXML measures in tenths of a staff space, so a staff is 40 tenths
high, and <scaling> says how many millimetres that is.

Only settings made here, or brought in with a MusicXML file opened here, are used. A score read from
a photo may carry a reader's guess at a page, and that mustn't change how the score is laid out.
"""

import math
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass

from pipeline.rhythm import EditError
from pipeline.structure import read_setting, write_setting

MARKER = "xrnsheet-page-style"
TENTHS = 40  # A staff's height, four spaces, in MusicXML's tenths.
HEADER_ORDER = ["work", "movement-number", "movement-title", "identification", "defaults", "credit", "part-list"]
# MuseScore's page sizes, in millimetres across and down, portrait.
PAGE_SIZES = {
    "A4": (210, 297), "Letter": (215.9, 279.4), "Legal": (215.9, 355.6), "Tabloid": (279.4, 431.8), "A3": (297, 420),
    "A5": (148, 210), "B4": (250, 353), "B5": (176, 250), "Executive": (184.2, 266.7), "Concert": (228.6, 304.8),
}
# The limits of what the pages can be drawn with, in millimetres and staff spaces.
SMALLEST_PAGE, LARGEST_PAGE = 50.0, 1000.0
MOST_MARGIN = 50.0
SMALLEST_SPACE, LARGEST_SPACE = 0.9, 2.4
MOST_SPACING = 24.0
LEAST_MUSIC = 30.0  # What's left across or down a page between its margins.


@dataclass(frozen=True)
class Layout:
    """A score's page, in millimetres, and its spacing, in staff spaces. The usual one is MuseScore's A4."""

    width: float = 210.0
    height: float = 297.0
    top: float = 15.0
    bottom: float = 15.0
    left: float = 15.0
    right: float = 15.0
    space: float = 1.75  # A staff space, the room between two staff lines.
    staves: float = 6.0  # The least room between one instrument's staff and the next's.
    systems: float = 6.0  # The least room between one system and the next.

    def checked(self) -> "Layout":
        values = asdict(self)
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values.values()):
            raise EditError("Those page settings aren't numbers.")
        if not (SMALLEST_PAGE <= self.width <= LARGEST_PAGE and SMALLEST_PAGE <= self.height <= LARGEST_PAGE):
            raise EditError(f"A page can be {SMALLEST_PAGE:g} to {LARGEST_PAGE:g} mm across and down.")
        if not all(0 <= margin <= MOST_MARGIN for margin in (self.top, self.bottom, self.left, self.right)):
            raise EditError(f"A margin can be up to {MOST_MARGIN:g} mm.")
        if self.width - self.left - self.right < LEAST_MUSIC or self.height - self.top - self.bottom < LEAST_MUSIC:
            raise EditError("Those margins leave no room for the music.")
        if not SMALLEST_SPACE <= self.space <= LARGEST_SPACE:
            raise EditError(f"A staff space can be {SMALLEST_SPACE:g} to {LARGEST_SPACE:g} mm.")
        if not (0 <= self.staves <= MOST_SPACING and 0 <= self.systems <= MOST_SPACING):
            raise EditError(f"The room between staves or systems can be up to {MOST_SPACING:g} staff spaces.")
        return self


def number(element: ET.Element | None, path: str) -> float | None:
    text = element.findtext(path) if element is not None else None
    try:
        found = float(text) if text is not None else None
    except ValueError:
        return None
    return found if found is not None and math.isfinite(found) else None


def read_defaults(root: ET.Element) -> Layout | None:
    """Read the page a MusicXML file sets out, or None if it doesn't say enough to lay one out."""
    defaults = root.find("defaults")
    millimetres, tenths = number(defaults, "scaling/millimeters"), number(defaults, "scaling/tenths")
    width, height = number(defaults, "page-layout/page-width"), number(defaults, "page-layout/page-height")
    if not millimetres or not tenths or not width or not height:
        return None
    per_tenth = millimetres / tenths
    margins = next((found for found in defaults.findall("page-layout/page-margins") if found.get("type") in (None, "both", "odd")), None)
    usual = Layout()
    side = lambda name, fallback: (number(margins, f"{name}-margin") * per_tenth) if number(margins, f"{name}-margin") is not None else fallback
    spacing = lambda path, fallback: number(defaults, path) / 10 if number(defaults, path) is not None else fallback
    return Layout(
        width=round(width * per_tenth, 2), height=round(height * per_tenth, 2),
        top=round(side("top", usual.top), 2), bottom=round(side("bottom", usual.bottom), 2),
        left=round(side("left", usual.left), 2), right=round(side("right", usual.right), 2),
        space=round(millimetres / 4 * TENTHS / tenths, 3),
        staves=round(spacing("staff-layout/staff-distance", usual.staves), 2),
        systems=round(spacing("system-layout/system-distance", usual.systems), 2),
    )


def page_layout(root: ET.Element) -> Layout:
    """Return the layout a score is drawn with: the one set here, or MuseScore's usual A4."""
    if read_setting(root, MARKER) != "yes":
        return Layout()
    found = read_defaults(root)
    try:
        return found.checked() if found is not None else Layout()
    except EditError:
        return Layout()  # A file can say anything, and a page that can't be drawn is drawn the usual way.


def keep_imported(root: ET.Element) -> None:
    """Lay out a score from another program on the page its file sets out, as MuseScore does, if it can be drawn."""
    found = read_defaults(root)
    if found is None:
        return
    try:
        found.checked()
    except EditError:
        return
    write_setting(root, MARKER, "yes")


def text_element(parent: ET.Element, tag: str, value: float) -> ET.Element:
    element = ET.SubElement(parent, tag)
    element.text = f"{round(value, 4):g}"
    return element


def set_layout(root: ET.Element, layout: Layout) -> None:
    """Keep a layout in the score as MusicXML's <defaults>. Anything else <defaults> says, such as fonts, stays."""
    layout = layout.checked()
    defaults = root.find("defaults")
    if defaults is None:
        defaults = ET.Element("defaults")
        rank = HEADER_ORDER.index("defaults")
        children = list(root)
        position = next((index for index, child in enumerate(children) if child.tag not in HEADER_ORDER or HEADER_ORDER.index(child.tag) > rank), len(children))
        root.insert(position, defaults)
    kept = [child for child in defaults if child.tag not in ("scaling", "page-layout", "system-layout", "staff-layout")]
    tenth = layout.space / 10  # Millimetres in a tenth.
    scaling = ET.Element("scaling")
    text_element(scaling, "millimeters", layout.space * 4)
    text_element(scaling, "tenths", TENTHS)
    page = ET.Element("page-layout")
    text_element(page, "page-height", layout.height / tenth)
    text_element(page, "page-width", layout.width / tenth)
    margins = ET.SubElement(page, "page-margins", type="both")
    for side in ("left", "right", "top", "bottom"):
        text_element(margins, f"{side}-margin", getattr(layout, side) / tenth)
    system = ET.Element("system-layout")
    text_element(system, "system-distance", layout.systems * 10)
    staff = ET.Element("staff-layout")
    text_element(staff, "staff-distance", layout.staves * 10)
    # In the order the schema wants: scaling, whether it's a concert score, then the page, system and
    # staff layouts, then the rest, such as its fonts.
    concert_score = [child for child in kept if child.tag == "concert-score"]
    for child in list(defaults):
        defaults.remove(child)
    defaults.extend([scaling, *concert_score, page, system, staff, *[child for child in kept if child.tag != "concert-score"]])
    write_setting(root, MARKER, "yes")


def layout_of(root: ET.Element) -> dict:
    return asdict(page_layout(root))

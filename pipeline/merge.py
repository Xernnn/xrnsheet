"""Join the pages of one document into a single score.

Each page is recognized alone, so pages can disagree: one might have lost a staff. Every
part must have the same number of measures, so a part missing from a page gets rests.
"""

import copy
import xml.etree.ElementTree as ET


def forward_length(measure: ET.Element) -> int:
    """Return how far a measure's first voice moves, in that measure's divisions."""
    total = 0
    for element in measure:
        if element.tag == "backup":
            break
        if element.tag == "forward" or (element.tag == "note" and element.find("chord") is None and element.find("grace") is None):
            total += int(float(element.findtext("duration") or 0))
    return total


def blank_measure(like: ET.Element, divisions: str) -> ET.Element:
    """Make a measure of rest as long as another part's measure."""
    measure = ET.Element("measure")
    ET.SubElement(ET.SubElement(measure, "attributes"), "divisions").text = divisions
    note = ET.SubElement(measure, "note")
    ET.SubElement(note, "rest", measure="yes")
    ET.SubElement(note, "duration").text = str(forward_length(like) or 1)
    return measure


def divisions_by_measure(measures: list[ET.Element]) -> list[str]:
    values = []
    current = "1"
    for measure in measures:
        current = measure.findtext("attributes/divisions") or current
        values.append(current)
    return values


def merge_pages(pages: list[ET.Element]) -> tuple[ET.Element, list[int]]:
    """Return one score, plus the number of measures that come before each page."""
    widest = max(pages, key=lambda page: len(page.findall("part")))
    merged = copy.deepcopy(widest)
    for name in ("work", "movement-title"):
        for element in merged.findall(name):
            merged.remove(element)
    for index, element in enumerate(pages[0].findall("work") + pages[0].findall("movement-title")):
        merged.insert(index, copy.deepcopy(element))
    parts = merged.findall("part")
    for part in parts:
        for measure in part.findall("measure"):
            part.remove(measure)

    offsets = []
    total = 0
    for page_index, page in enumerate(pages):
        page_parts = [part.findall("measure") for part in page.findall("part")]
        longest = max(page_parts, key=len, default=[])
        divisions = divisions_by_measure(longest)
        offsets.append(total)
        for part_index, part in enumerate(parts):
            measures = page_parts[part_index] if part_index < len(page_parts) else []
            for index in range(len(longest)):
                measure = copy.deepcopy(measures[index]) if index < len(measures) else blank_measure(longest[index], divisions[index])
                measure.set("number", str(total + index + 1))
                measure.attrib.pop("implicit", None)
                if index == 0 and page_index > 0:
                    found = measure.find("print")
                    if found is None:
                        found = ET.Element("print")
                        measure.insert(0, found)
                    found.set("new-system", "yes")
                    found.set("new-page", "yes")
                part.append(measure)
        total += len(longest)
    return merged, offsets

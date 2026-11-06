import xml.etree.ElementTree as ET

from pipeline import edit
from pipeline.merge import merge_pages


def page(parts: list[int], title: str | None = None, divisions: int = 4) -> ET.Element:
    root = ET.Element("score-partwise")
    if title:
        ET.SubElement(ET.SubElement(root, "work"), "work-title").text = title
    part_list = ET.SubElement(root, "part-list")
    for index, measures in enumerate(parts, start=1):
        ET.SubElement(part_list, "score-part", id=f"P{index}")
        part = ET.SubElement(root, "part", id=f"P{index}")
        for number in range(measures):
            measure = ET.SubElement(part, "measure", number=str(number + 1))
            if number == 0:
                ET.SubElement(ET.SubElement(measure, "attributes"), "divisions").text = str(divisions)
            note = ET.SubElement(measure, "note")
            ET.SubElement(note, "rest")
            ET.SubElement(note, "duration").text = str(divisions * 4)
    return root


def test_one_page_stays_as_it_was():
    merged, offsets = merge_pages([page([3])])
    assert offsets == [0] and len(merged.findall("part/measure")) == 3


def test_measures_are_renumbered_across_pages():
    merged, offsets = merge_pages([page([3]), page([2]), page([4])])
    assert offsets == [0, 3, 5]
    assert [measure.get("number") for measure in merged.findall("part/measure")] == [str(number) for number in range(1, 10)]


def test_each_later_page_starts_a_new_page():
    merged, _ = merge_pages([page([2]), page([2])])
    measures = merged.findall("part/measure")
    assert measures[0].find("print") is None
    assert measures[2].find("print").get("new-page") == "yes" and measures[2].find("print").get("new-system") == "yes"
    assert list(measures[2])[0].tag == "print"  # MusicXML requires it first.


def test_the_first_page_title_is_kept():
    merged, _ = merge_pages([page([1], "Blue Moon"), page([1], "Wrong guess")])
    assert merged.findtext("work/work-title") == "Blue Moon"


def test_the_input_pages_are_not_changed():
    first, second = page([2]), page([2])
    merge_pages([first, second])
    assert [measure.get("number") for measure in second.findall("part/measure")] == ["1", "2"]


def test_a_part_missing_from_a_page_is_filled_with_rests():
    merged, offsets = merge_pages([page([2, 2]), page([3])])
    parts = merged.findall("part")
    assert len(parts) == 2 and [len(part.findall("measure")) for part in parts] == [5, 5] and offsets == [0, 2]
    filler = parts[1].findall("measure")[2]
    assert filler.find("note/rest").get("measure") == "yes"
    assert filler.findtext("note/duration") == "16" and filler.findtext("attributes/divisions") == "4"


def test_a_page_with_more_parts_than_the_first_still_keeps_every_part():
    merged, _ = merge_pages([page([2]), page([2, 2])])
    assert [len(part.findall("measure")) for part in merged.findall("part")] == [4, 4]
    assert len(merged.findall("part-list/score-part")) == 2


def test_note_ids_stay_unique_after_merging():
    first, second = page([1]), page([1])
    edit.ensure_note_ids(first)
    edit.ensure_note_ids(second)
    merged, _ = merge_pages([first, second])
    for note in merged.iter("note"):
        note.attrib.pop("id", None)
    edit.ensure_note_ids(merged)
    ids = [note.get("id") for note in merged.iter("note")]
    assert len(ids) == len(set(ids)) == 2

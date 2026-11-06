"""Small edits to a score: the fixes needed after recognition, not a full notation editor."""

import xml.etree.ElementTree as ET

from chords.vocab import parse_chord
from pipeline.musicxml import chord_from_harmony, harmony_element

STEPS = "CDEFGAB"
SHARP_ORDER = "FCGDAEB"
ACCIDENTAL_NAMES = {-1: "flat", 0: "natural", 1: "sharp"}
# Elements that must come after <accidental> inside a <note>, in schema order.
AFTER_ACCIDENTAL = {"time-modification", "stem", "notehead", "notehead-text", "staff", "beam", "notations", "lyric", "play"}


class EditError(ValueError):
    """The requested edit doesn't make sense, for example text that isn't a chord."""


def ensure_note_ids(root: ET.Element) -> None:
    """Give each note a stable id. The renderer keeps them, so clicks map back to notes."""
    used = {note.get("id") for note in root.iter("note") if note.get("id")}
    counter = 0
    for note in root.iter("note"):
        if note.get("id"):
            continue
        while f"note{counter}" in used:
            counter += 1
        note.set("id", f"note{counter}")
        used.add(f"note{counter}")


def find_note(root: ET.Element, note_id: str) -> ET.Element:
    for note in root.iter("note"):
        if note.get("id") == note_id:
            return note
    raise EditError(f"No note with id {note_id}")


def key_signature_at(root: ET.Element, target: ET.Element) -> int:
    """Return the number of sharps (or negative flats) in force at this note."""
    fifths = 0
    for part in root.findall("part"):
        for measure in part.findall("measure"):
            found = measure.findtext(".//attributes/key/fifths")
            fifths = int(found) if found else fifths
            if target in measure.iter("note"):
                return fifths
    return fifths


def alteration_in_key(step: str, fifths: int) -> int:
    if fifths > 0 and step in SHARP_ORDER[:fifths]:
        return 1
    if fifths < 0 and step in SHARP_ORDER[::-1][:-fifths]:
        return -1
    return 0


def write_alteration(note: ET.Element, alter: int, key_alter: int) -> None:
    pitch = note.find("pitch")
    existing = pitch.find("alter")
    if alter:
        if existing is None:
            existing = ET.Element("alter")
            pitch.insert(1, existing)
        existing.text = str(alter)
    elif existing is not None:
        pitch.remove(existing)

    for accidental in note.findall("accidental"):
        note.remove(accidental)
    if alter != key_alter:
        element = ET.Element("accidental")
        element.text = ACCIDENTAL_NAMES[alter]
        children = list(note)
        position = next((i for i, child in enumerate(children) if child.tag in AFTER_ACCIDENTAL), len(children))
        note.insert(position, element)


def move_note(root: ET.Element, note_id: str, steps: int) -> None:
    """Move a note up or down the staff, taking the key signature's sharps and flats."""
    note = find_note(root, note_id)
    pitch = note.find("pitch")
    if pitch is None:
        raise EditError("Rests have no pitch to move")
    absolute = STEPS.index(pitch.findtext("step")) + 7 * int(pitch.findtext("octave")) + steps
    step = STEPS[absolute % 7]
    pitch.find("step").text = step
    pitch.find("octave").text = str(absolute // 7)
    key_alter = alteration_in_key(step, key_signature_at(root, note))
    write_alteration(note, key_alter, key_alter)


def set_accidental(root: ET.Element, note_id: str, alter: int) -> None:
    note = find_note(root, note_id)
    pitch = note.find("pitch")
    if pitch is None:
        raise EditError("Rests have no accidental")
    key_alter = alteration_in_key(pitch.findtext("step"), key_signature_at(root, note))
    write_alteration(note, alter, key_alter)


def delete_note(root: ET.Element, note_id: str) -> None:
    """Remove a note. Its time has to stay filled, so a lone note becomes a rest."""
    note = find_note(root, note_id)
    if note.find("pitch") is None:
        raise EditError("This is already a rest")
    for measure in root.iter("measure"):
        children = list(measure)
        if note not in children:
            continue
        index = children.index(note)
        follows = index + 1 < len(children) and children[index + 1].tag == "note" and children[index + 1].find("chord") is not None
        if note.find("chord") is not None:
            measure.remove(note)
        elif follows:
            # The next stacked note inherits the duration, so it just stops being a chord member.
            children[index + 1].remove(children[index + 1].find("chord"))
            measure.remove(note)
        else:
            pitch_index = list(note).index(note.find("pitch"))
            note.remove(note.find("pitch"))
            note.insert(pitch_index, ET.Element("rest"))
            for tag in ("accidental", "stem", "beam", "tie", "notations"):
                for child in note.findall(tag):
                    note.remove(child)
        return


def clear_marks(root: ET.Element, note_id: str) -> None:
    """Remove fermatas, ornaments and articulations, keeping slurs and ties.

    Chord text just above a staff is sometimes misread as one of these, about once a page.
    """
    notations = find_note(root, note_id).find("notations")
    removed = 0
    if notations is not None:
        for tag in ("fermata", "ornaments", "articulations"):
            for child in notations.findall(tag):
                notations.remove(child)
                removed += 1
    if not removed:
        raise EditError("This note has no marks to clear")


def list_chords(root: ET.Element) -> list[dict]:
    """List chords in document order, which matches their order on the rendered page."""
    chords = []
    part = root.find("part")
    divisions = 1.0
    for measure_index, measure in enumerate(part.findall("measure")):
        position = 0.0
        for child in measure:
            length = float(child.findtext("duration") or 0)
            if child.tag == "attributes":
                divisions = float(child.findtext("divisions") or divisions)
            elif child.tag == "harmony":
                chord = chord_from_harmony(child)
                chords.append({"index": len(chords), "text": str(chord) if chord else "?", "measure": measure_index, "onset": position})
            elif child.tag == "backup":
                position -= length / divisions
            elif child.tag == "forward":
                position += length / divisions
            elif child.tag == "note" and child.find("chord") is None and child.find("grace") is None:
                position += length / divisions
    return chords


def require_chord(text: str):
    chord = parse_chord(text)
    if chord is None:
        raise EditError(f'"{text}" is not a chord symbol this app understands')
    return chord


def harmony_at(root: ET.Element, index: int) -> tuple[ET.Element, ET.Element]:
    harmonies = [(measure, harmony) for measure in root.find("part").findall("measure") for harmony in measure.findall("harmony")]
    if not 0 <= index < len(harmonies):
        raise EditError(f"No chord number {index}")
    return harmonies[index]


def set_chord(root: ET.Element, index: int, text: str) -> None:
    """Replace a chord, or delete it when the text is empty."""
    measure, harmony = harmony_at(root, index)
    position = list(measure).index(harmony)
    new_element = harmony_element(require_chord(text)) if text.strip() else None
    measure.remove(harmony)
    if new_element is not None:
        measure.insert(position, new_element)


def add_chord(root: ET.Element, note_id: str, text: str) -> None:
    """Add a chord at a note, which matters when recognition missed one completely."""
    chord = require_chord(text)
    note = find_note(root, note_id)
    for measure in root.iter("measure"):
        children = list(measure)
        if note not in children:
            continue
        index = children.index(note)
        while children[index].find("chord") is not None:
            index -= 1
        measure.insert(index, harmony_element(chord))
        return

"""The mixer: how loud each instrument plays, where it sits between the speakers, and which are muted or soloed.

MuseScore keeps its mixer with the score but apart from the music, so moving a fader isn't an edit
to undo. Here it's kept beside the score's versions for the same reason. Volume is in decibels from
-60 to +12, with 0 the instrument's usual level, and pan goes from -100, hard left, to 100, hard
right. The chord symbols and the metronome have a channel each, and the master channel sets the
whole.

MusicXML has a volume and a pan for each instrument, so they go into an exported file and come back
from one. Its volume is a share of a MIDI volume of 127, and MuseScore writes its usual level as
the MIDI volume 100, which is what 0 dB stands for here too. Its pan is an angle: -90 degrees is
hard left. Mute and solo are only for listening, so no file keeps them.
"""

import copy
import math
import xml.etree.ElementTree as ET

QUIETEST_DB, LOUDEST_DB = -60.0, 12.0
USUAL_MIDI_VOLUME = 100
MOST_MIDI = 127
PAN_DEGREES = 0.9  # A pan of 100 is 90 degrees to the right.
CHANNELS = ("chords", "metronome", "master")
MIDI_INSTRUMENT_ORDER = ["midi-channel", "midi-name", "midi-bank", "midi-program", "midi-unpitched", "volume", "pan", "elevation"]


def channel(values: dict | None = None) -> dict:
    """Return a channel's settings, each kept within its range, with the usual ones for anything not given."""
    values = values or {}
    volume = float(values.get("volume", 0.0))
    pan = float(values.get("pan", 0.0))
    return {
        "volume": min(max(volume, QUIETEST_DB), LOUDEST_DB) if math.isfinite(volume) else 0.0,
        "pan": min(max(pan, -100.0), 100.0) if math.isfinite(pan) else 0.0,
        "mute": bool(values.get("mute", False)),
        "solo": bool(values.get("solo", False)),
    }


def settled(mixer: dict | None, part_ids: list[str]) -> dict:
    """Return a whole mixer for these instruments. Settings for instruments no longer there are dropped."""
    mixer = mixer or {}
    parts = mixer.get("parts") or {}
    return {"parts": {part_id: channel(parts.get(part_id)) for part_id in part_ids}} | {name: channel(mixer.get(name)) for name in CHANNELS}


def midi_volume(db: float) -> int:
    """MIDI's volume controller for a level in decibels. General MIDI makes 40 times the log of the volume its decibels."""
    return min(MOST_MIDI, max(0, round(USUAL_MIDI_VOLUME * 10 ** (db / 40)))) if db > QUIETEST_DB else 0


def midi_pan(pan: float) -> int:
    return min(MOST_MIDI, max(1, round(64 + pan * 63 / 100)))


def musicxml_volume(db: float) -> float:
    return round(midi_volume(db) * 100 / MOST_MIDI, 4)


def decibels(musicxml: float) -> float:
    """The level a MusicXML volume stands for, which is a share of the loudest MIDI volume."""
    level = musicxml * MOST_MIDI / 100 / USUAL_MIDI_VOLUME
    return min(max(40 * math.log10(level), QUIETEST_DB), LOUDEST_DB) if level > 0 else QUIETEST_DB


def number(text: str | None) -> float | None:
    try:
        found = float(text) if text is not None else None
    except ValueError:
        return None
    return found if found is not None and math.isfinite(found) else None


def read_from_score(root: ET.Element) -> dict | None:
    """Read the volume and pan a MusicXML file gives its instruments, if it gives any."""
    parts = {}
    for entry in root.findall("part-list/score-part"):
        volume = number(entry.findtext("midi-instrument/volume"))
        pan = number(entry.findtext("midi-instrument/pan"))
        if volume is not None or pan is not None:
            parts[entry.get("id")] = channel({"volume": decibels(volume) if volume is not None else 0.0, "pan": pan / PAN_DEGREES if pan is not None else 0.0})
    return {"parts": parts} if parts else None


def with_mixer(root: ET.Element, mixer: dict) -> ET.Element:
    """Return a copy of the score with each instrument's volume and pan written in, for exporting."""
    written = copy.deepcopy(root)
    for entry in written.findall("part-list/score-part"):
        settings = mixer["parts"].get(entry.get("id"))
        if settings is None:
            continue
        midi = entry.find("midi-instrument")
        if midi is None:
            midi = ET.SubElement(entry, "midi-instrument", id=f"{entry.get('id')}-I1")
        for tag, value in (("volume", musicxml_volume(settings["volume"])), ("pan", round(settings["pan"] * PAN_DEGREES, 2))):
            for found in midi.findall(tag):
                midi.remove(found)
            element = ET.Element(tag)
            element.text = f"{value:g}"
            rank = MIDI_INSTRUMENT_ORDER.index(tag)
            position = next((index for index, child in enumerate(midi) if child.tag in MIDI_INSTRUMENT_ORDER and MIDI_INSTRUMENT_ORDER.index(child.tag) > rank), len(midi))
            midi.insert(position, element)
    return written

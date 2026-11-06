"""Small MusicXML files for tests, written by hand so each test shows exactly what it uses."""

import xml.etree.ElementTree as ET

HEADER = '<?xml version="1.0"?><score-partwise version="4.0"><part-list><score-part id="P1"><part-name>x</part-name></score-part></part-list><part id="P1">'
FOOTER = "</part></score-partwise>"
ATTRIBUTES = "<attributes><divisions>2</divisions><key><fifths>{fifths}</fifths></key><time><beats>4</beats><beat-type>4</beat-type></time></attributes>"


def note(step: str = "C", octave: int = 4, duration: int = 2, extra: str = "", note_type: str = "quarter") -> str:
    return f'<note>{extra}<pitch><step>{step}</step><octave>{octave}</octave></pitch><duration>{duration}</duration><type>{note_type}</type></note>'


def rest(duration: int = 2) -> str:
    return f"<note><rest/><duration>{duration}</duration></note>"


def harmony(root: str = "C", text: str = "7", kind: str = "dominant") -> str:
    return f'<harmony><root><root-step>{root}</root-step></root><kind text="{text}">{kind}</kind></harmony>'


def score(*measures: str, fifths: int = 0) -> str:
    body = ""
    for number, content in enumerate(measures, start=1):
        attributes = ATTRIBUTES.format(fifths=fifths) if number == 1 else ""
        body += f'<measure number="{number}">{attributes}{content}</measure>'
    return HEADER + body + FOOTER


def parsed(*measures: str, fifths: int = 0) -> ET.Element:
    return ET.fromstring(score(*measures, fifths=fifths))

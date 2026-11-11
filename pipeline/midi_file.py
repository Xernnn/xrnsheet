"""Write a score's playback as a standard MIDI file, so it opens in any music program.

A MIDI file is a list of events, each saying how long after the one before it happens. Every part
gets its own track, and one more track holds the tempo.
"""

TICKS_PER_QUARTER = 480
CHORD_VELOCITY = 50
MOST_CHANNELS = 16
DRUM_CHANNEL = 9  # General MIDI keeps this channel for drums, so it's skipped.
HIGHEST_NOTE = 127  # A note number has seven bits, because a set top bit starts the next event.
MOST_SOUNDS = 128  # General MIDI's sounds, counted from 1 in a score and from 0 in the file.
LONGEST_BEAT = 0xFFFFFF  # A tempo is the microseconds a quarter note lasts, in three bytes: 3.6 a minute at the slowest.


def variable_length(value: int) -> bytes:
    """Write a number in seven-bit pieces, the top bit of each saying that another one follows."""
    pieces = [value & 0x7F]
    value >>= 7
    while value:
        pieces.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(pieces))


def ticks(position: float) -> int:
    """Return a moment as a count from the start of the file, which is the earliest there is.

    A score from another program can put a note or a tempo before its own start. A wait can't be
    less than nothing, and writing one never ended.
    """
    return max(round(position * TICKS_PER_QUARTER), 0)


def track(events: list[tuple[int, int, bytes]]) -> bytes:
    """Build a track from (tick, order, bytes). At one tick, notes end before others begin."""
    body = bytearray()
    now = 0
    for tick, _, message in sorted(events, key=lambda item: item[:2]):
        body += variable_length(tick - now) + message
        now = tick
    body += variable_length(0) + b"\xff\x2f\x00"
    return b"MTrk" + len(body).to_bytes(4, "big") + bytes(body)


def note_events(notes: list[dict], channel: int) -> list[tuple[int, int, bytes]]:
    events = []
    for note in notes:
        if not 0 <= note["midi"] <= HIGHEST_NOTE:
            continue  # A MIDI file can't hold it, and written anyway it would spoil the file.
        velocity = max(min(round(note.get("volume", CHORD_VELOCITY / 127) * 127), 127), 1)
        start = ticks(note["start"])
        end = max(ticks(note["start"] + note["length"]), start + 1)
        events.append((start, 1, bytes([0x90 | channel, note["midi"], velocity])))
        events.append((end, 0, bytes([0x80 | channel, note["midi"], 0])))
    return events


def channel_of(part: int) -> int:
    channel = part % (MOST_CHANNELS - 1)
    return channel + 1 if channel >= DRUM_CHANNEL else channel


def midi_bytes(data: dict) -> bytes:
    """Turn what playback returns into the bytes of a MIDI file."""
    tempo_events = []
    for change in data["tempos"]:
        if not change["bpm"] > 0:
            continue  # No speed at all, which only a file from elsewhere can ask for.
        microseconds = min(max(round(60_000_000 / change["bpm"]), 1), LONGEST_BEAT)
        tempo_events.append((ticks(change["start"]), 0, b"\xff\x51\x03" + microseconds.to_bytes(3, "big")))
    tracks = [track(tempo_events)]
    programs = data.get("programs") or [1]
    for part, program in enumerate(programs):
        channel = channel_of(part)
        if not 1 <= program <= MOST_SOUNDS:
            program = 1  # The usual sound, as when a part doesn't say.
        events = [(0, -1, bytes([0xC0 | channel, program - 1]))]
        events += note_events([note for note in data["notes"] if note.get("part", 0) == part], channel)
        tracks.append(track(events))
    if data["chords"]:
        channel = channel_of(len(programs))
        chords = [{"start": chord["start"], "length": chord["length"], "midi": midi} for chord in data["chords"] for midi in chord["midi"]]
        tracks.append(track(note_events(chords, channel)))
    header = b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big") + len(tracks).to_bytes(2, "big") + TICKS_PER_QUARTER.to_bytes(2, "big")
    return header + b"".join(tracks)

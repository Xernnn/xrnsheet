# Instrument notation API

What the server offers for guitar, bass, violin and piano notation, for wiring into the page. Every
edit below goes through `apply_edit`, so it's saved as a new version (undo and redo work), it's made
at concert pitch when the score is shown that way, and it answers with the whole score as
`GET /api/scores/{id}` does, plus anything listed under "Returns". A refused edit answers 422 with
`{"detail": "<the reason, to show as it is>"}` and changes nothing.

## Guitars and basses: strings, capo and tablature

### What a score says about them

`GET /api/scores/{id}` has two more fields:

- `fretted`: one entry per part, in order. `null` for anything without frets, otherwise:

  ```json
  {"strings": ["E2", "A2", "D3", "G3", "B3", "E4"], "tuning": "standard", "capo": 0, "frets": 19,
   "tab": true, "tabStaff": 2, "tunings": ["DADGAD", "dropD", "halfStepDown", "openD", "openG", "standard"]}
  ```

  `strings` are the strings as they sound, from the lowest up. `tuning` is a name from `tunings`, or
  `"custom"`. `tabStaff` is the staff number of the tablature in its part, or `null` without one.
- `fretProblems`: notes no string can play, which the tablature leaves out:
  `[{"note": "note12", "part": 0, "reason": "C2 is below this instrument's lowest string."}]`.
  Show them like `barChecks`.

### The tablature staff

A guitar with tablature is one part with two staves, as MusicXML and MuseScore have it: the standard
staff, and under it a TAB staff (`<clef><sign>TAB</sign>`, `<staff-details>` with a `<staff-tuning>`
per string and `<capo>`). `partStaves` counts both, so a staff on the page still maps to its part.

The TAB staff holds a copy of every note, with `<technical><string>` and `<fret>`. The server makes
it again after every edit, so it always matches the standard staff. Its notes have the id of the note
they copy with `-tab` on the end (`note12-tab`), and Verovio keeps those ids on the drawn fret
numbers (`<g id="note12-tab" class="note">`). Every endpoint that takes a note id takes a `-tab` id
too and acts on the note it copies, so clicking a fret number can simply select that id. The
copy's rests aren't drawn and aren't clickable.

Notes on a TAB staff are never played: playback and MIDI only sound the standard staff.

New instruments for the chooser (`GET /api/instruments`), each with `"family": "Strings - Plucked"`:
`guitar-tab` ("Guitar + tablature"), `acoustic-guitar-tab`, `electric-guitar-tab` and
`bass-guitar-tab` (four strings, E1 A1 D2 G2). The plain `guitar`, `bass-guitar` and the rest are
unchanged and have no tablature until it's turned on.

### `POST /api/scores/{id}/strings`

Tune a fretted instrument, move its capo, or show or hide its tablature. Notes keep their sound;
their frets follow, and each note stays on its string while that string can still play it.

```json
{"part": 0, "tuning": "dropD", "strings": null, "capo": 2, "tab": true}
```

- `part`: which instrument, counted from 0 at the top.
- `tuning`: a name from that part's `fretted.tunings`. Guitars: `standard`, `dropD`, `DADGAD`,
  `openG`, `openD`, `halfStepDown`. Basses: `standard`, `dropD`, `halfStepDown`.
- `strings`: a custom tuning instead, each string's sound from the lowest up, as `["C2", "G2", "D3",
  "G3", "C4", "E4"]` (sharps `#`, flats `b`). 3 to 10 strings; the TAB staff takes as many lines.
- `capo`: 0 to 12. Fret numbers count from the capo, so an open string under a capo is `0`.
- `tab`: `true` adds a TAB staff under a one-staff fretted instrument, `false` takes it away. The
  tuning and capo stay with the instrument either way.

Anything left out stays as it is. Refused (422): a part that isn't fretted, an unknown tuning, a
string that isn't a note, a capo past 12, or a request that changes nothing.

### `GET /api/tunings`

The named tunings, each string from the lowest up:
`{"guitar": {"standard": ["E2", ...], "dropD": [...], ...}, "bass": {...}, "mostCapo": 12}`.

### Choosing the string for a note: `POST /api/scores/{id}/notes/{note_id}`

```json
{"action": "string", "string": 2}
```

Plays the note on that string (1 is the highest), and its fret follows. Notes tied to it move with
it. The choice holds until the note's pitch changes. Refused with the reason when the string can't
play the note ("String 1 can't play C4: the lowest it plays is E4."), when it would be past the last
fret, when another note sounding at that moment is on that string, or when the instrument has no
tablature yet.

Strings are otherwise chosen automatically for every new or changed note: a string that's free at
that moment, near where the hand already is, open strings while the hand is low on the neck, and
every note of a chord on its own string.

## Playback

`GET /api/scores/{id}/playback`:

- Each entry of `notes` has `"staff"` beside `"part"`: the staff it's on in its part, from 1. A
  piano's right hand is staff 1 and its left hand staff 2, so the page can play one hand alone by
  leaving out the other's notes.
- Each entry of `events` (by note id) has the staff as a sixth item:
  `[bar, place, length, midi, part, staff]`.

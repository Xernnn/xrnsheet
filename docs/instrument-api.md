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

## Techniques and markings: `POST /api/scores/{id}/marks`

The same endpoint as the other marks, with these actions. `notes` is the selection's note ids (a
`-tab` id stands for its note). Each one toggles: asking for what's already there takes it away,
and asking for something else of the same kind replaces it.

| Action | Body | What it does |
|---|---|---|
| `fingering` | `{"notes": [...], "text": "3"}` or `"text": "1 2 4"` | One finger on every chosen note, or one each in turn (spaces optional, `"124"` works). Piano 1–5, above the right hand's staff and below the left hand's; strings and guitar 0–4. Refused with the range otherwise. |
| `pluck` | `{"notes": [...], "text": "p"}` | A guitarist's right-hand finger: `p`, `i`, `m`, `a` (or `c`). |
| `stringNumber` | `{"notes": [...], "number": 2}` | The string that plays each note: circled on a guitar, a Roman numeral on a violin. On a guitar with tablature it moves the tablature to that string too, and `number: 0` marks the strings the tablature already has. Refused when the string can't play the note. |
| `bend` | `{"notes": [...], "name": "half"}` | `half`, `full` or `oneAndHalf`. |
| `harmonic` | `{"notes": [...], "name": "natural"}` | `natural` or `artificial`. A violin's artificial harmonic adds the diamond a fourth above, as a chord note. |
| `deadNote` | `{"notes": [...]}` | An x notehead, and an X on the tablature. |
| `join` | `{"notes": [first, last], "name": "hammerOn"}` | `hammerOn`, `pullOff`, `slide` or `glissando`, from each chosen note to the next (one note chosen: to the note after it). |
| `vibrato` | `{"notes": [first, last]}` | A vibrato's wavy line over a note or a stretch. |
| `line` | `{"notes": [first, last], "name": "palmMute"}` | `palmMute` ("P.M." below) or `letRing` ("let ring" above), with a dashed line over the stretch. |
| `sul` | `{"notes": [first, last], "text": "G"}` | "sul G" with a dashed line; the text is one of the instrument's strings, `pont.` or `tasto`. |
| `pizzicato` | `{"notes": [id], "on": true}` | "pizz." (`on: true`) or "arco" (`on: false`) at that note. |
| `position` | `{"notes": [id], "number": 3}` | "III pos." from that note; 0 is "½ pos.", up to XII. |
| `toggle` | `{"notes": [...], "name": "open"}` | As for staccato and the others; new names `open` (an open string's "o") and `stopped` ("+"). `upbow` and `downbow` were already there. |

### Selecting and deleting them

`marks` in `GET /api/scores/{id}` now lists these too, so a click can select them and Delete
(`{"action": "remove", "id": ...}`) takes them away:

- `{"kind": "textline", "drawn": "dir", "text": "P.M."}` for palm mute, let ring and sul lines
  (drawn once, from their words, so they match by order like other `dir` marks). "pizz.",
  "arco" and positions are `"kind": "text"`, as staff text is.
- `{"kind": "fingering" | "pluck" | "string" | "bend" | "hammer" | "pull", "drawn": "self"}` for
  marks on notes. `"self"` means the drawing already carries the id: the element has
  `data-mark="<id>"` (a `g.fing` for fingerings, plucks and string numbers, `g.xrn-bend`, and the
  hammer-on's `g.slur` and its `g.xrn-joined` letter). Don't match these up by order.

Removing a hammer-on or pull-off removes both its ends. "Clear marks" on a note removes all its
techniques too.

### How they're drawn

Verovio draws fingerings, slides, glissandos, vibratos, harmonic circles, and the words and dashed
lines. The server draws what it leaves out into the page SVG (pipeline/decorate.py): circles round a
guitar's string numbers, italic p-i-m-a, H and P over hammer-on and pull-off slurs on both staves,
bend arrows with "½" or "full" (on the tablature when there is one), X for dead notes and `<12>`
or `5<17>` for harmonics on the tablature, and "A.H." over a guitar's artificial harmonic.

## Playback

`GET /api/scores/{id}/playback`:

- Each entry of `notes` has `"staff"` beside `"part"`: the staff it's on in its part, from 1. A
  piano's right hand is staff 1 and its left hand staff 2, so the page can play one hand alone by
  leaving out the other's notes.
- Each entry of `events` (by note id) has the staff as a sixth item:
  `[bar, place, length, midi, part, staff]`.
- A note can carry `"program"`: the General MIDI sound (counted from 1) to play it with, where it
  differs from its part's. Pizzicato violin notes have `46`. MIDI export changes sound there.
- A bend is two entries with the same id: the note, then `"bend": 2` (semitones) at the bent pitch
  from a quarter of the way through.
- Techniques are heard: hammer-ons and pull-offs legato (the second note quieter), slides and
  glissandos as quick notes up to the next, palm mute short and quieter, let ring to the end of its
  line, dead notes as a click, harmonics at the pitch they ring (by fret on a guitar with tablature,
  an octave up for a guitar's artificial harmonic, two octaves above a violin's stopped note), and
  pizzicato short.

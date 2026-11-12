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
- `strings`: each part's strings as they sound, from the lowest up (a fretted part's as it's tuned,
  a violin's or viola's `["G3", "D4", "A4", "E5"]`), or `null` for anything without strings.
- `transpositions`: how many semitones each part's first bar sounds from how it's written (`-12`
  for a bass guitar), so the page can place a played note before it has fetched playback.
- `notes`: each note's details have a sixth item when the score says how it's played:
  `{"string": 2, "fret": 5}` from the tablature (the fret counted from the capo), and any of
  `"finger"`, `"pluck"`, `"mark"` (its string number), `"bend"` (`half`, `full`, `oneAndHalf`),
  `"harmonic"` (`natural`, `artificial`), `"dead"`, `"cross"` (the staff it's drawn on), and for a
  bowed instrument the `"position"` in force and the `"sul"` string over it. A fret number's
  `-tab` copy has no sixth item: look up the note it copies.

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

**Tablature on its own** is a part of one staff, a TAB staff that is the music itself:
`guitar-tab-only` ("Guitar, tablature only") and `bass-guitar-tab-only` in `GET /api/instruments`.
Its notes carry their own `<string>` and `<fret>`, it's played, and it's drawn with its rhythm:
stems, beams and rests, which have their notes' ids in the drawing (`<g id="note7" class="rest">`) as
on any other staff. `fretted` says `"tab": true, "tabStaff": 1`, and `fretProblems` lists its notes
no string can play. `{"tab": false}` on `/strings` turns it into a standard staff in the
instrument's own clef, each note keeping its string (below).

**A guitar without tablature** keeps a string chosen for a note on the note itself, as a
`<technical>` holding its `<string>` and `<fret>` (a `<string>` with no `<fret>` beside it is a
circled string number, a mark). It isn't drawn, but `notes` gives it as `{"string": 2, "fret": 5}`
as for a guitar with tablature. A new pitch forgets it, a new tuning or capo moves its fret while
the string can still play the note, tablature turned on plays the note there, and tablature taken
away leaves each note's string on the note.

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
fret, or when another note sounding at that moment is on that string. A guitar without tablature
keeps it on the note (above).

A note written or stacked (`"action": "write"` or `"stack"`) can carry `"string": 2`, as a fret
clicked on the fretboard does. On a guitar the note is played on that string in the same edit, so
one undo takes both back; on a bowed instrument it gets that string number. If the string is busy
the note is still written, on a string of its own, and the reply's `message` says why.

### A fret typed on the tablature: `POST /api/scores/{id}/notes/{note_id}`

```json
{"action": "fret", "fret": 7}
```

Makes the note the sound of that fret (counted from the capo) on the string it's played on now, or
on `"string": 3` if one is given, and plays it there: the standard staff follows, spelled as the key
spells the sound, and the notes tied to it change with it. `note_id` is the fret number's `-tab` id
or a note of tablature on its own. Refused with the reason for a rest, a fret past the
instrument's last, or a note no string plays yet. The page types two-digit frets itself, waiting a
moment after a first digit that could begin one.

Strings are otherwise chosen automatically for every new or changed note: a string that's free at
that moment, near where the hand already is, open strings while the hand is low on the neck, and
every note of a chord on its own string.

## Chord diagrams

### `POST /api/scores/{id}/diagrams`

`{"on": true}` shows a fretboard diagram over every chord symbol, `{"on": false}` stops. It's an
edit, so it can be undone, and the score keeps it. `GET /api/scores/{id}` says `"diagrams": true`.

The diagrams are for the score's first fretted part (one with six strings or more first), in its
tuning and with its capo: with the capo on the second fret a D chord shows the C shape, fingered
from the capo. Without a guitar in the score they're for a guitar in standard tuning. Shapes come
from the open and barre chords guitarists learn; other chords and other tunings are worked out (the
chord's bass on the lowest string played, its needed notes above, low on the neck).

They're drawn into the page SVG inside each chord symbol's `g.harm`, as `g.xrn-diagram`, so a
click on a diagram is a click on its chord. Each system gets room above it for them, chords close
together on a line get smaller diagrams, and the composer's line moves up out of their way.

A downloaded MusicXML file has each chord's diagram as its `<harmony><frame>`. A file opened with
`<frame>`s turns diagrams on; a frame that isn't the library's shape (someone's own fingering) is
kept and drawn as it is.

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
- `"kind": "slide" | "glissando" | "vibrato"`, also `"drawn": "self"`: a `g.gliss` on the staff and
  another between the fret numbers, and a vibrato's `g.trill`. Removing one removes both its ends.
- `"kind": "harmonic" | "upbow" | "downbow" | "open" | "stopped" | "staccato" | "staccatissimo" |
  "tenuto" | "accent" | "marcato"`, `"drawn": "self"`: the `g.artic` Verovio draws inside the note's
  `g.note` (or its chord's `g.chord`), a guitar's `g.xrn-harmonic` A.H., and for a violin's
  artificial harmonic the diamond's own `g.note`, which is that harmonic's and not a note to select.
  Removing a violin's artificial harmonic removes its diamond.

A dead note is its notehead, an "x", so it isn't a mark of its own: its note is selected, and the
`deadNote` action or the note's `clear` action takes it away. `clear` now takes a player's
techniques too, a slide's and a glissando's other ends and a violin harmonic's diamond with them,
and leaves the string and fret a guitar's note is played at.

Removing a hammer-on or pull-off removes both its ends. `{"action": "label", "id": ..., "text": "3"}`
retypes a clicked fingering, pluck or string number, and a bend as `½`, `full` or `1½`; an empty text
removes it. A guitar's string number retyped moves its tablature to that string too, or is refused
with the reason. "Clear marks" on a note removes all its techniques too.

### How they're drawn

Verovio draws fingerings, slides, glissandos, vibratos, harmonic circles, and the words and dashed
lines. The server draws what it leaves out into the page SVG (pipeline/decorate.py): circles round a
guitar's string numbers, italic p-i-m-a, slurs for hammer-ons and pull-offs on both staves with
their H and P on the tablature (on the staff when there's none),
bend arrows with "½" or "full" (on the tablature when there is one), X for dead notes and `<12>`
or `5<17>` for harmonics on the tablature, and "A.H." over a guitar's artificial harmonic.

### Copying and pasting them

`POST /api/scores/{id}/copy` gives each copied event `techniques` (one per note of the chord: its
`fingering`, `pluck`, `string`, `bend`, `harmonic`, `dead`), `strings` (on a guitar, each
note's string), `joins` (`[later event, "hammerOn" | "pullOff" | "slide" | "glissando" |
"vibrato"]`), `words` (`[{"kind": "pizzicato", "on": true}]`, `{"kind": "position",
"number": 3}` or `{"kind": "hand", "name": "RH"}`, at that note) and `lines` (`[later event,
"P.M." | "let ring" | "sul G" ...]`, a line starting at the note, to the copied note it ends on or
the copy's last). `POST /api/scores/{id}/paste` puts them back, leaving out what the instrument
pasted into can't have (palm mute and let ring are a fretted instrument's, sul and pizz. a bowed
one's, positions either's, hands a keyboard's; a plain staff takes any), and the strings once its
tablature has the notes, as one edit. Words or a line already there the same stay, rather than
being toggled away. A violin's artificial harmonic is copied as its stopped note, and the paste
adds its diamond.

### A capo

A guitar with a capo has "Capo 2" drawn over the start of its music, as a `g.xrn-capo`, which isn't
one of the score's marks and can't be selected.

## Piano hands

- **R.H. and L.H.:** `POST /api/scores/{id}/marks` with `{"action": "hand", "notes": [...], "name": "RH"}`
  (or `"LH"`) puts the words over each chosen note; the same again takes them away, the other
  hand replaces them. They're `"kind": "text"` marks.
- **A note on the other staff:** `POST /api/scores/{id}/notes/{note_id}` with `{"action": "crossDown"}`
  draws the note, with its chord, on the staff below its own, as MuseScore's Ctrl+Shift+↓ does, and
  `{"action": "crossUp"}` on the staff above (or back to its own). `POST /api/scores/{id}/group` takes
  the same two actions for a selection. Refused for a rest, past the top or bottom staff, onto a
  tablature staff, or on a one-staff instrument.

  The note stays in its own staff's voice, so its bar still adds up and every edit treats it as
  before. The drawing, a downloaded MusicXML file and playback put it on the other staff: the file
  gives it that `<staff>` with its own voice, as MuseScore writes it, and playback gives it that
  `staff`, so muting a hand mutes the notes that hand plays. Opening such a file keeps the note in
  its voice.
- **Fingers 1–5:** the `fingering` action above, which places a right hand's fingers above the staff
  and a left hand's below. For "typing digits after selecting notes", send the selection's ids in
  order with one digit each, as `{"notes": [a, b, c], "text": "135"}`.

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

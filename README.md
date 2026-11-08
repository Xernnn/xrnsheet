# xrnsheet

Turn a photo or PDF of sheet music into notation you can edit and reprint.

Upload a sheet, check the result beside the original, fix notes and chord symbols, then download a PDF or MusicXML. Chord symbols are read by a small model trained in this repository. Notes are read by [homr](https://github.com/liebharc/homr), or by a second small model here if the sheet has one melody per staff.

It works well on clean, computer-rendered sheets. It's weak on real handwriting, and it hasn't been tested on real phone photos or real printed pages yet. See [the project report](docs/PROJECT_REPORT.md) for the full results, and [the knowledge guide](docs/KNOWLEDGE_GUIDE.md) for the ideas behind them.

## Setup

Requires Python 3.11+ and `rsvg-convert`. Install [PyTorch](https://pytorch.org/get-started/locally/) for your machine first if you want GPU training.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m chords.fonts          # download the fonts
```

The trained models are in `models/`. To make your own, see [Train the chord reader](#train-the-chord-reader) and [Train the note reader](#train-the-note-reader).

The benchmark also uses Audiveris and Tesseract. Audiveris has no Arch package, so it's unpacked into `tools/` from the official release:

```sh
mkdir -p tools/audiveris && cd tools/audiveris
curl -LO https://github.com/Audiveris/audiveris/releases/download/5.11.0/Audiveris-5.11.0-ubuntu24.04-x86_64.deb
bsdtar -xf Audiveris-*.deb && bsdtar -xf data.tar.*
rm Audiveris-*.deb data.tar.* control.tar.* debian-binary
```

Tesseract needs `eng.traineddata` in `/usr/share/tessdata`.

## Use the app

```sh
.venv/bin/python -m app.server
```

Open <http://127.0.0.1:8000>. A page takes 15 to 30 seconds on CPU. PDFs can have up to 40 pages. If a page can't be read, it's left out and the app says which one. Your sheets are listed on the start page, so you can come back to them.

To read notes with the model in this repository and not homr, which only works for one melody per staff:

```sh
.venv/bin/python -m app.server --notes own
```

To write music with no photo behind it, choose "Or start a blank score" on the start page and pick a key, time, instrument and number of bars. You can also open a `.musicxml`, `.xml` or `.mxl` file from another program the same way as a photo, and it goes straight to the editor.

The keys follow MuseScore. Press `N` to start entering notes, pick a length, and type the letters or click the staff. A new bar appears when you write past the last one.

| Key | Action |
|---|---|
| `N` | Start or stop entering notes |
| `A` to `G` | Write that note and move on |
| Shift+`A` to `G` | Add that note to the chord just written |
| `1` to `7` | Length, from a 64th to a whole note. `5` is a quarter note |
| `.` | Dotted |
| `0` | Rest |
| `T` | Tie to the next note |
| Ctrl+`3` | Make a triplet from the chosen length, or take one away. Ctrl+`5`, `6` and `7` make other tuplets |
| `#` `-` `=` | Sharp, flat, natural. `b` also means flat when you aren't entering notes |
| ↑ ↓ | Move a note. With Ctrl, move an octave |
| ← → | Previous or next note |
| Shift+click, Shift+← → | Select a stretch of notes |
| Ctrl+A | Select the whole staff |
| Ctrl+C, Ctrl+X, Ctrl+V | Copy, cut, paste, also between scores |
| Ctrl+L | Type lyrics. Space moves on, `-` joins syllables, ↓ ↑ change verse, Enter finishes. On the last note both stay in the word |
| `S` | Slur a stretch, or this note to the next |
| `<` `>` | Crescendo, diminuendo over a stretch |
| Shift+`S` `V` `N` `O` | Staccato, accent, tenuto, marcato |
| `/` | Add a grace note before this note |
| `R` | Repeat this note or stretch |
| `J` | Swap for the same note under another name, as C♯ for D♭ |
| `X` | Flip the stem |
| Ctrl+Alt+`1` to `4` | Write in that voice, or move the selection to it |
| Enter | Start a new line at this bar |
| Space | Play or stop, from the selected note |
| Ctrl+Z, Ctrl+Y | Undo, redo |
| Delete, Ctrl+Delete | Turn into a rest, or remove the beat and pull later notes in |
| Esc | Stop entering notes, or close the chord editor |
| Click the staff | While entering notes, place a note there. Shift+click adds it to a chord |
| Drag a selected note | Move it up or down |
| Click a dynamic, tempo, text, slur or hairpin | Change or remove it |

A longer note overwrites what comes after it, and a shorter one leaves a rest, so every bar stays full. Bars that don't add up to the time signature are listed above the score, which is usually how a misread rhythm shows itself. Click one to go there, then fix it with "Remove beat" or "Insert beat". The toolbar also adds and deletes bars, and changes the key or time from the selected bar. A new time signature doesn't move notes between bars: the ones that no longer fit are listed for you to fix.

A note too long for its bar carries into the next one, tied. A selected stretch moves, deletes or copies as one undo step, within one voice.

Play sounds the notes and the chord symbols, starting from the selected note. It follows repeats, first and second endings, D.C. and D.S. with Fine and Coda, tempo changes, dynamics and hairpins, swing, and articulations and ornaments. The menu beside Play slows it down or speeds it up.

The row of menus above the score adds marks, dynamics, tempos, text, rehearsal letters, jumps and clefs. "Transpose…" moves the selection, or the whole score if nothing is selected, by an interval, for a B♭, E♭ or F instrument, or to a key, and the chord symbols move with it. "Instruments…" adds a part, and a transposing instrument gets its own key. "Layout…" sets how many bars go on a line, and "Time…" also makes the first bar a pickup. A part can be printed alone, and the MIDI link saves a file for other music programs. A MIDI keyboard works while entering notes, and keys pressed together make a chord.

A staff can hold four voices. Choose one with V1 to V4 or Ctrl+Alt and a number, and the arrows stay in that voice. If you write a rest in an extra voice, it stays there so you can carry on, and Delete removes it.

To write a triplet, pick the length it should fill, press Ctrl+`3`, and type its three notes. A quarter gives eighth-note triplets. A triplet can't be cut in half, so selecting or copying any note of one takes the whole triplet.

The "Repeat…" menu starts or ends a repeat at the selected bar, and marks it as a first or second ending. Select several bars to put one ending over all of them. The title and composer you type above the score are printed. A title recognition guessed isn't printed until you save it.

Click a chord to change or remove it. Doubtful chords are coloured and listed under "Please check". "Clear marks" removes a false fermata or ornament from a note. "Mark as checked" tells the app you've finished with a sheet, which makes it usable as training data. Bars you added or deleted are allowed for, so a chord that only moved isn't treated as a misread one.

To recognize a sheet without the app:

```sh
.venv/bin/python -m pipeline.recognize photo.jpg --out results/photo
```

## Train the chord reader

```sh
.venv/bin/python -m chords.testset                                                  # build the test crops
.venv/bin/python -m chords.baselines                                                # score Tesseract and RapidOCR
.venv/bin/python -m chords.train --name chord_reader --hand-layout 0.35 --negatives 0.2
.venv/bin/python -m chords.evaluate --name chord_reader                             # score the model
```

Training takes about 34 minutes on a 4 GB GPU. Add `--no-damage` to repeat the ablation. Training data is generated as it runs, and 5 fonts are held out for testing.

## Train the note reader

```sh
.venv/bin/python -m notes.render      # generate the strips, about an hour of CPU time
.venv/bin/python -m notes.train       # about 60 minutes on a 4 GB GPU
.venv/bin/python -m notes.evaluate    # score it on the two held-out music fonts
```

The best checkpoint is saved as training runs, so a stopped run still leaves a usable model.

## Retrain on your own corrections

After you've pressed "Mark as checked" on some sheets:

```sh
.venv/bin/python -m chords.corrections       # gather checked sheets into data/corrections/
.venv/bin/python bench/real_report.py        # how the app did on the held-out sheets
.venv/bin/python -m chords.retrain           # train a candidate and report on it
.venv/bin/python -m chords.retrain --promote # also replace the live model if it passes
```

About one sheet in five is held out, chosen by a hash. A candidate replaces the live model only if there are at least 30 held-out chords, it gains more chords than it loses with p below 0.05, and it stays at 0.99 or above on the synthetic test. So far, a few dozen real corrections haven't been shown to help. See [the project report](docs/PROJECT_REPORT.md).

## Run the benchmark

```sh
.venv/bin/python bench/make_samples.py     # build test images and correct answers
.venv/bin/python bench/run_engines.py      # run each engine and print the scores
.venv/bin/python bench/make_figures.py     # redraw the report's charts
```

Add `--only chart --engine xrnsheet` to run part of it, or `--cached` to re-score existing outputs without running any engine. `bench/repair_report.py` shows what the bar repairs change, and `bench/real_page.py` scores the real handwritten page, which its opening comment explains how to fetch. Finished runs are cached in `results/`, so delete `results/xrnsheet` after changing the pipeline or the model. The `own_notes` engine runs the same pipeline with no homr, and isn't part of the default run.

To compare another app, export its result for each image as `data/external/soundslice/<image name>.musicxml`, then add `--engine soundslice`.

## Test your own sheets

1. Put photos or scans in `data/my_sheets/`.
2. Run `.venv/bin/python bench/run_engines.py`. The MusicXML appears in `results/`, ready to open in MuseScore.
3. To score a sheet, correct its MusicXML in MuseScore and save it as `data/truth/<same name as the image>.musicxml`.

To get a real photo score without correcting anything by hand, print a benchmark image such as `data/images/chart_00__clean.png`, photograph it, and save the photo as `data/my_sheets/chart_00__myphoto.jpg`. The name before `__` links it to its correct answer.

Scores on real photos count for far more than the simulated ones.

## What the scores mean

- **pitch**: F1 score for correct pitches, ignoring rhythm.
- **no-oct**: the same, ignoring octave. A big gap between this and **pitch** usually means one misread clef moved every note.
- **note**: F1 score for correct pitch and duration together.
- **chords**: share of chord symbols read with the right root and quality.
- **placed**: share also attached to the right measure and beat.

## Latest results

From 2026-11-06, on synthetic pages. Notes are `note` F1 scores, and chords show recall / placement. In this table, xrnsheet's notes come from homr.

| Sheets | Notes: xrnsheet | Notes: Audiveris | Chords: xrnsheet | Chords: Audiveris |
|---|---|---|---|---|
| Spelled jazz charts, clean (10) | 1.00 | 0.99 | 0.93 / 0.88 | 0.25 / 0.19 |
| Spelled jazz charts, simulated photo (10) | 1.00 | 0.92 | 0.93 / 0.87 | 0.16 / 0.08 |
| Lead sheets, clean (3) | 1.00 | 1.00 | 1.00 / 0.96 | 0.63 / 0.63 |
| Lead sheets, handwritten-style font (3) | 1.00 | 0.89 | 1.00 / 1.00 | 0.53 / 0.53 |
| Lead sheets, simulated photo (3) | 1.00 | 0.87 | 0.98 / 0.98 | 0.43 / 0.30 |
| Classical, clean (5) | 0.97 | 0.95 | n/a | n/a |
| Classical, handwritten-style font (5) | 0.93 | 0.51 | n/a | n/a |
| Classical, simulated photo (5) | 0.82 | 0.27 | n/a | n/a |

These aren't real-world accuracy. homr was trained on images much like these, so its scores are likely inflated. On one real handwritten page, the pipeline found 20 of 53 chords.

The note reader in this repository (`--notes own`), on the same synthetic pages:

| Sheets | Notes: own reader | Notes: homr | Chords placed: own reader | Chords placed: homr |
|---|---|---|---|---|
| Spelled jazz charts, clean (10) | 1.00 | 1.00 | 0.93 | 0.88 |
| Spelled jazz charts, simulated photo (10) | 1.00 | 1.00 | 0.88 | 0.87 |
| Lead sheets, clean (3) | 1.00 | 1.00 | 1.00 | 0.96 |
| Lead sheets, handwritten-style font (3) | 0.99 | 1.00 | 1.00 | 1.00 |
| Lead sheets, simulated photo (3) | 1.00 | 1.00 | 1.00 | 0.98 |
| Classical, all conditions (15) | 0.12 | 0.91 | n/a | n/a |

It reads one melody per staff, which is why classical music scores so low, and why homr stays the default. It takes about 4 seconds a page. It reads hand-style time signatures badly, and the notes score doesn't measure that. On real handwriting it found only a small share of the notes.

## Licences

Check these before shipping a commercial product.

| Component | Licence | Note |
|---|---|---|
| homr, Audiveris | AGPL-3.0 | Running them as a closed online service may require sharing source code. `--notes own` runs with no homr, for melody sheets only |
| Verovio | LGPL-3.0 | |
| music21 | BSD-3-Clause | The benchmark pieces come from its corpus, and two tests open a chorale from it. Corpus files aren't copied into this repository |
| RapidOCR | Apache-2.0 | |
| Fonts in `data/fonts/` | SIL Open Font License | Used to generate training images |
| JAZZMUS dataset | CC BY-NC 4.0 | Not used here. It's non-commercial, and access must be requested |

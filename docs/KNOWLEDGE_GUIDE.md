# What I had to learn to build xrnsheet

This is the knowledge behind the project, written so I can explain every decision without looking at the code. Each idea is tied to the place it mattered. The results themselves are in [PROJECT_REPORT.md](PROJECT_REPORT.md).

## 1. The problem: optical music recognition

Optical music recognition (OMR) turns an image of notation into data a computer can edit. It's harder than reading text for three reasons.

- **Meaning depends on position in two dimensions.** A dot on a staff means a different pitch depending on its height, the clef, the key signature and any accidental earlier in the bar. Text mostly reads along one line.
- **Symbols overlap by design.** Stems, beams, slurs and staff lines all run through each other.
- **The output has strict rules.** Every bar's durations must add up, so one misread rest damages the whole bar.

There are two broad approaches. A pipeline finds staves, then symbols, then rebuilds the music. An end-to-end model reads a whole staff at once. homr is a hybrid: it uses segmentation to find staves and a transformer to read each one. Audiveris is a classic rule-based pipeline, with Tesseract reading text.

I treated notes and chord symbols as separate problems. Notes are a huge problem that existing engines handle well on print. Chord symbols are a small, text-like problem that fits on a 4 GB GPU.

## 2. The data format: MusicXML

MusicXML is the shared language of notation software. Everything in my system meets there: homr writes it, my code edits it, Verovio draws it, and MuseScore opens it.

```xml
<attributes><divisions>2</divisions></attributes>      <!-- 2 units per quarter note -->
<harmony>
  <root><root-step>B</root-step><root-alter>-1</root-alter></root>
  <kind text="m7">minor-seventh</kind>                 <!-- meaning, plus how it was printed -->
</harmony>
<note>
  <pitch><step>D</step><octave>4</octave></pitch>
  <duration>2</duration>                               <!-- 2 units = 1 quarter note -->
</note>
```

Three facts caused real bugs.

- **A chord is a separate element that sits before a note.** Its beat is wherever the running position is at that point in the bar. To read one, I have to walk through the bar and add up durations, including `<backup>` and `<forward>`.
- **`kind` is the meaning and `text` is the spelling.** `C-7`, `Cm7` and `Cmin7` mean the same chord. My `Chord` class holds the meaning, so a different spelling isn't scored as a mistake.
- **`<degree>` adds alterations like b9.** Verovio prints those on top of the text, which gave me `A7b9(b9)`. I keep them in the saved file and strip them only when drawing.

## 3. Measure before you build

The most valuable decision was building the benchmark before any model.

**Baseline.** A baseline is the score a simple or existing method gets. Without one, 90% means nothing. Mine changed the plan twice:

- Audiveris suggested chords were an unsolved gap.
- RapidOCR, a free text reader, then scored 0.908, while Tesseract scored 0.452. The real gap was mostly Tesseract, not chords. My model now had a much higher bar to clear.

**Ground truth by construction.** Labelling real pages is slow, so I generate pages from known scores. The answer is exact, because the image was made from it. The cost is that these images are too easy, which section 8 is all about.

**Scoring notes with alignment.** If one note is missed, comparing lists position by position marks everything after it as wrong. An alignment (`SequenceMatcher`) finds the longest matching blocks, so one missed note costs one note. Then:

```
precision = matched / predicted      recall = matched / truth
F1 = 2 * matched / (predicted + truth)
```

**Scoring slices, not one number.** An average hides failure. `pitch` compared with `no-oct` separates "one wrong clef" from "many wrong notes". Slicing by font, damage and symbol type showed where each reader broke.

**Rule I learned:** a wrong scorer gives believable numbers. Several of my bugs produced tables that looked fine. Audiveris splitting one sheet into two files made it look worse than it was, and lyrics like "Am" counted as chords. I now check any surprising score against the raw output.

## 4. Training data: synthetic images and leakage

No large commercially usable set of labelled chord images exists. JAZZMUS is real but gated and non-commercial. So I generate images: pick a chord, draw it in a font, add damage.

**Generalization and leakage.** The test set must answer "how will this do on things it hasn't seen?". If test data leaks into training, the score answers an easier question. Three decisions came from this:

- I split by **font**, not by image. A random image split would put every font in both sets, and the model would be tested on handwriting it had already learned.
- I checked for **hidden twins**. Tinos and Liberation Serif are the same design under different names, as are Arimo and Liberation Sans. A test on those would be a disguised copy of training.
- I **froze** the test set on disk. Generating it fresh would let results drift between runs.

**Three splits, three jobs.**

| Split | Used for | Rule |
|---|---|---|
| Train | Updating weights | Anything goes |
| Validation | Choosing the best checkpoint and settings | Can guide decisions |
| Test | One final score | Must never guide decisions |

Each time a result changes what you do, that data stops being a clean test. This happened with my real page, so I now call it a development page.

**Negatives.** A detector boxes everything, including words and noteheads, so the reader must be able to say "this isn't a chord". I label those images with empty text. Later, real false chords showed I had the wrong negatives (section 9).

**Rebalancing.** `common_weight` of 0.5 flattens the chord mix, so rare chords like `m(maj7)` appear often enough to learn. The test set keeps the natural mix, so scores aren't distorted.

## 5. The model: CRNN with CTC

A CRNN is a CNN followed by an RNN. It's the classic design for reading a line of text, and small enough for my GPU at 2.8 million parameters.

Here's the shape of the data for a 200-pixel-wide crop:

```
image                     1 × 48 × 200     grayscale, fixed height, any width
CNN with pooling        256 × 1 × 50       height shrinks to 1, width to a quarter
as a sequence            50 × 256          50 slices, read left to right
bidirectional LSTM       50 × 384          each slice now knows its neighbours
linear + log-softmax     50 × 38           one probability per character per slice
```

- **Why the CNN pools height more than width.** Height is squeezed from 48 to 1, because I want a horizontal sequence. Width only shrinks by 4, so narrow characters keep their own slices. That's why the later pools are `(2, 1)`.
- **Why batch normalization.** It keeps each layer's inputs at a steady scale, so training is faster and more stable.
- **Why an LSTM.** A CNN only sees a small window. The LSTM carries context along the line, so a smudged mark can be read from what surrounds it. Only a few characters can follow `ma`, and a chord doesn't start twice.
- **Why bidirectional.** Some characters are only settled by what comes after. An `m` could mean minor, or it could be the start of `maj`, so the model has to see to the right as well.

**CTC: training without character positions.** The model produces 50 predictions, but the label `C11` has 3 characters, and nobody marked where each one sits. CTC (connectionist temporal classification) solves this by adding a special blank symbol and adding up the probability of every alignment that collapses to the right text.

```
slices:   C  C  -  1  1  -  1  -
collapse repeats, then drop blanks:   C 1 1
```

The blank is what lets `11` exist at all, because without it the repeated 1s would merge. The rule is that the output can't be longer than the slices, so width mustn't shrink too much.

**Decoding.** I use greedy decoding: pick the likeliest character per slice, merge repeats, drop blanks. Beam search can do better, but greedy is simpler and the chord vocabulary is small.

**Confidence.** I define it as the probability of the least sure character kept. One doubtful letter makes a chord doubtful, which is what a person reviewing it wants to know. That's the "Please check" list in the app.

## 6. Training

| Part | What it does | Why here |
|---|---|---|
| AdamW | Adapts the step for each weight, with weight decay | A reliable default |
| OneCycleLR | Raises then lowers the learning rate | Fast start, stable finish |
| Mixed precision | Runs some math in 16-bit | Fits more on a 4 GB GPU |
| Gradient clipping | Limits the size of an update | RNNs can have sudden huge gradients |
| Fresh images every step | Never repeats a sample | Stops memorization of images |

Reading my training log taught me three things.

- **Loss near zero with validation stuck near 88% means a gap.** Training images became easy, while some unseen fonts stayed hard. That gap was the first warning of section 8.
- **Validation accuracy is noisy.** It jumped between .60 and .83 in the middle of training, because the learning rate was high.
- **Checkpoint selection adds optimism.** I save the best validation score, so that number is slightly lucky. The test score is the honest one.

A practical bug: the DataLoader starts several worker processes. Each needs its own seed, or they all generate the same images.

## 7. Measuring results honestly

**Confidence intervals.** 32/53 is not the same claim as 604/1000. For a proportion near 0 or 1, the textbook interval breaks down, so I used the **Wilson interval**. At 0.604 with n=53 it's 0.469 to 0.724, which is a wide range worth stating.

**Comparing two models on the same data.** Two separate intervals ignore that both models saw the same images. **McNemar's test** uses only the items where they disagree:

```
only my model right: 13     only RapidOCR right: 7
```

If the models were equal, these would split 50/50. An exact binomial test gives p = 0.26, so this page can't separate them. The lesson: 60% against 49% looks like a win, and it isn't proven.

**Character error rate (CER)** is edit distance divided by length. It shows near misses that accuracy treats as total failures.

**Selective prediction.** Measure accuracy at each confidence cutoff. On the test set, the first model kept 71% of readings above 0.8, at 99.9% accuracy. That's the logic behind asking a person to check only some chords.

**Ablation.** Remove one thing and retrain, to prove what it contributes. I removed camera damage:

| Model | Clean images | Damaged images |
|---|---|---|
| Trained with damage | 99.8% | 99.3% |
| Trained without damage | 97.4% | 1.6% |

Augmentation wasn't a small boost. It was the entire ability to read a damaged image, and it even helped on clean ones.

## 8. Distribution shift: the most important lesson

Distribution shift means the data you meet in use differs from the data you tested on. My chord reader scored 99.6% on held-out fonts and 60% on one real handwritten page.

Held-out fonts were a real test, but of a narrower question than I wanted. They showed the model could read *fonts it hadn't seen*. They couldn't show it could read *handwriting*, because every font draws each letter the same way, on a straight baseline, with even spacing.

Real handwriting differs in ways no font has:

- `7` raised well above the root
- wide gaps after the root
- sharps cut through the letter
- crossed sevens that look like `+`
- slurs running through the chord

**The warning signs were already there.** Validation hand fonts scored 76% and 93%, while Kalam, the test hand font, scored 98.5%. An unusually easy test font pushed the headline up. Averages over a few fonts depend heavily on which fonts you happened to pick.

**What to do about it:**

1. Get real data as early as possible, even a single page.
2. Look at errors and name their causes. Mine were mostly about layout, not letter shapes.
3. Make the training data include those causes. This is domain randomization: if the variation is wide enough, reality looks like one more variation.
4. Keep a separate real test. A page that shaped your fixes can't also prove they worked.

## 9. Detection versus recognition

Recognition reads a cropped image. Detection finds where the crops are. A page score multiplies both, so a strong reader can't rescue bad boxes.

My crop score was 99.6%, and my score on synthetic pages was 67.6%. That gap should have stopped me immediately. I blamed an early checkpoint, which was wrong. The same number came back with the final model, and a one-page trace found the real cause.

**Text detectors fail on chords in two opposite ways.**

- **Under-segmentation in type.** Close chords look like words on a line, so `Abmin7b5 F#7` arrives as one box. The reader read it correctly, and then my parser rejected it because the whole string isn't one chord. Correct answers were being thrown away.
- **Over-segmentation in handwriting.** The gap after a root looks like the end of a word, so `E7` arrives as `E` and `7`. The bare `E` then passes as a wrong chord.

**Splitting with dynamic programming.** When a box fails to read, I cut it at white gaps into pieces, read every run of neighbouring pieces, and choose the best way to explain the box.

```
best[end] = the best of:
    best[end - 1]                              skip this piece
    best[start] + span(start, end)             for every span that reads as a chord
ranked by: pieces covered, then fewest spans
```

"Fewest spans" matters: `C` and `maj7` could be cut apart, but one chord that explains both beats two guesses. Dynamic programming is what makes this cheap, because the best answer up to a piece can be reused rather than recomputed.

**Judging rows rather than boxes.** One box reading `Am` could be a chord or a lyric. A whole row of mostly chords is chords. This replaced a distance rule I had written for lyrics, which was silently deleting real chords on pages with tight staves. It had worked on synthetic pages because they have generous spacing, so the rule had picked up the same bias as my data.

**Model the negatives you actually see.** Before writing new negatives, I made a sheet of the real false chords. Almost all were pieces of staff with handwritten stems running through them, read as `F` or `E`, which are bars on a stem. So that's what I added.

**Judge a fix on every score, not on the example that suggested it.** Tracing lost chords gave me three convincing ideas, and only one survived.

| Idea | Why it looked right | What the numbers said |
|---|---|---|
| Shrink the page before the detector | Photos lost more chords, and photos are larger | No size beat the original |
| Join overlapping failed boxes | One chord really was cut between two boxes | Lost 23 of 268 chords on clean pages |
| Split a second time at a smaller gap | Two chords on a photo sat just under the gap limit | Gained 2 on the benchmark, added 4 false chords on the real page |
| Allow 10 pieces in a box, not 6 | A box of 7 pieces lost all 5 of its chords | Gained 3 chords and 2 false ones on clean pages, and changed nothing on the real page |

The third idea is the one to remember. It raised every synthetic score and looked finished until I ran the real page, because a rule that cuts more boxes also cuts handwriting that was never a chord. A rule has to be tested on the data most likely to hurt it.

**Heuristics or learning?** Each of these rules could in principle be learned by a chord-specific detector. Rules are the right start with no labelled data, because they're fast to test and easy to explain. If I get real labelled pages, training a detector is the obvious next step.

## 10. Looking at the data

Looking at actual images caught problems no metric would have shown:

| I looked at | I found |
|---|---|
| Rendered test pages | Wrong flat symbols, missing clef |
| My first augmented samples | Distortion so strong that a person couldn't read the chords |
| Boxes drawn on the real page | Three separate causes of one bad score |
| A sheet of false chords | Staff fragments, not stray text |
| The exported PDF | homr inventing fermatas above chords |

If a label is unreadable to a person, it's noise to the model. I now look at generated samples before every training run.

## 11. Reading notes myself

homr works, but it's AGPL and it won't tell me where its barlines are. So I built a small note reader for the simplest useful case: one melody on one staff. That covers lead sheets and most jazz charts. It doesn't cover piano or choir music.

**Finding staves without a neural network.** Each line of music has to be cut out first, and this is classical computer vision:

1. Threshold the page, so ink is white and paper is black, even under uneven light.
2. Use a long horizontal opening, which keeps lines and removes almost everything else.
3. Cut the page into 48 vertical slices. In each one, look for five evenly spaced rows. I call that a comb.
4. Chain combs from slice to slice into tracks, and fit a smooth curve through each one.
5. Use `cv2.remap` to bend the staff flat.

My first version counted ink along straight rows. It lost staves on warped photos, because a curved line doesn't sit in one row. The tracing version then invented staves on 64 of 120 pages, because a ledger line or beam beside four real lines also looks like a comb, shifted by one line. Keeping one track per staff fixed that. It now gets all 300 generated pages right, and all 48 rotated photo pages up to 8 degrees.

**One change to the CRNN: keep the height.** The chord reader pools height down to 1, because a chord's meaning runs along the line. In a note, height is the pitch, so pooling it away would destroy the answer.

```
strip                      1 × 96 × W
CNN with pooling         192 × 6 × W/4       keep 6 rows of height
flatten each slice      1152 × W/4
bidirectional LSTM       640 × W/4
linear + log-softmax     269 × W/4           one of 269 tokens per slice
```

**Tokens describe what's printed, and rules work out the pitch.** The model doesn't output "F sharp 5". It outputs `note:9:8`, meaning a note in staff position 9 that looks like an eighth. Rules then apply the clef, the key and any accidental earlier in the bar, the way a musician does. That gives two benefits. The vocabulary stays at 269 tokens, and one misread key signature can be fixed in one place, without touching the notes.

**CTC gives me positions for free.** I said CTC doesn't need to know where characters are. It doesn't, but after training, the slice where each token fires is close to where the symbol sits. Greedy decoding keeps that slice, so my reader knows the x position of every barline and note. Chord placement can then use real positions rather than guessing from note counts.

**Round-trip test.** Before an hour of training, I checked the labels. A generated page gives tokens, and the code that builds the score turns those tokens back into MusicXML. If the result differs from the original, the labels or the builder are wrong, and no amount of training can fix that. All 150 random pages came back exactly.

**Set limits from the unit you actually care about.** My pen distortion was copied from the chord generator, where it was sized for letters. One staff step on a strip is only 4 pixels, so that much warp would have moved a note to a different pitch and silently made its label wrong. I cut it to a level that can't change a pitch, then checked by eye.

**One test font explained more than the average did.** Petaluma, the hand-style test font, got 52% of strips exactly right, against 97% for Leipzig. An alignment found the biggest single cause: the reader got the time signature right on only 38 of 250 strips. Ignoring that token lifts Petaluma to 74%, which is still far below Leipzig's 99%, so it's only part of the story. My first reading was that notes were fine, and that was too generous. The notes I could line up were 98.7% right, but that leaves out every strip that came out the wrong length, and a quarter of Petaluma's strips did.

**The same lesson about test sets, again.** I could work on that token, but Petaluma is my held-out font. Changing the model because of it would turn it into a development font, and I'd have no clean test left.

**An earlier negative result.** With homr I tried finding barlines from the image with rules. It helped clean pages, but on photos it found wrong barlines whose count happened to match, so placement fell from 185 to 171 of 207. That's why I wanted barlines from a model that has read the line.

## 12. Learning from real corrections

The app's correction loop is also a way to collect data: every chord a person fixes is a real, labelled example. That sounds free. Getting something usable out of it took more thought than the model did.

**Human in the loop.** A person checks the model's output, and that check becomes training data. The danger is that the model's mistakes can become labels. If someone opens a sheet, fixes two chords and leaves, the rest wasn't necessarily checked. So only sheets marked "checked" are used. "Checked" means a person looked, which is what makes a label worth trusting.

**Label quality, and how a tool can invent bad labels.** I compare the first recognized version with the final one and classify each chord as confirmed, corrected, moved or removed. A simulated user quickly exposed a bug in that logic: 28 chords looked "removed", which would have taught the model that real chords aren't chords. They had only been put in the wrong place, and the user deleted them and added them elsewhere. Those are now counted as moved, by looking within one measure either side. When the text differs and I can't tell which case it is, I skip the chord. A missing example costs less than a wrong one.

**Group splits and near-duplicates.** If the same piece is scanned twice and one copy lands in training and one in the test, the test is a disguised copy. It's the Tinos and Liberation Serif problem from section 4, with sheets in place of fonts. So sheets with mostly the same chords are grouped, and a whole group goes to one side. One group in five is held out, decided by a hash, so a sheet doesn't switch sides as more data arrives.

**Fine-tuning.** Rather than train from nothing, I start from the existing model and train gently on the new data, at a low learning rate. Each batch adds 20 real chords to 60 synthetic ones. The synthetic part is there so that training on a few dozen chords doesn't wear away everything else the model can read.

**K-fold cross-validation.** With 53 real chords, a single split is mostly luck, and the test side is tiny. K-fold takes turns: split into 5 parts, train on 4, test on the fifth, and repeat. Every chord is tested exactly once, by a model that never saw it.

| Real chords used to train | Right, of 53 | Gained | Lost | McNemar p | Second writer, of 8 |
|---|---|---|---|---|---|
| None, the current model | 41 | | | | 4 |
| 10, 500 steps | 41 | 6 | 6 | 1.00 | 4.8 |
| 20, 500 steps | 40 | 5 | 6 | 1.00 | 5.0 |
| 42, 500 steps | 41 | 7 | 7 | 1.00 | 4.8 |
| 42, 1,500 steps | 43 | 7 | 5 | 0.77 | 4.8 |

Fine-tuning changed which chords were right, but not how many. The best case, 43 against 41, is well within chance. So a few dozen corrected chords haven't been shown to help. Retraining may need far more data, or may not be where the gain is. It's better to learn that before spending weeks on corrections in the belief that they'll retrain the model. One caveat: that page already shaped the current model, so it's a harsh test for further gains.

**Promotion gates.** A new model mustn't replace the old one because the numbers look nice on the day. So I fixed the rules before any real data existed. `chords.retrain` promotes a model only if there are at least 30 held-out chords, the synthetic test stays at 0.99 or above, it gains more chords than it loses, and McNemar gives p below 0.05. Fixing the rule first is what stops me bending it later, and the gate can only be applied to data the new model has never trained on.

**A stand-in isn't evidence.** I tested the whole loop with a simulated user who corrected synthetic sheets. That proved the plumbing works, from upload through correction, export, retraining and gate. Its accuracy numbers mean nothing, and both models scored 88/88. A pipeline test and a model test can look identical in a log, so the difference has to be written down.

## 13. Building it as software

**Tests that prove something.** Passing tests only show the code matches my expectations. **Mutation testing** checks the tests themselves: break the code on purpose and see if a test fails. When I put old bugs back, two got through, including a test font moving into training. A test now pins those fonts by name.

**Keep risky parts in one place.** homr is imported in a single file, so swapping the engine or dealing with its licence stays contained. `chord_boxes.py` takes the reader as a function, so its tests use a fake reader and run in milliseconds.

**Caching experiments.** homr takes about 20 seconds per page, but the chord logic takes under a second. Saving homr's output let me compare five versions of the rule on 13 pages in a few minutes. Fast experiments get run, and slow ones get skipped.

**Running out of resources.** The laptop has little free memory, so each engine run has a memory cap through `systemd-run`. The GPU has 4 GB, so a training batch of 80 needs validation at 48. For the note reader I measured instead of guessing: a guessed pixel limit would have cut batches to about 5, and the measured one allows 32 at 1.82 GB.

**Save your work as you go.** The note reader's training was stopped by the system for lack of memory, after about 23,000 of 24,000 steps. Almost nothing was lost, because the best checkpoint so far is always on disk. Long jobs on a shared laptop should always assume they can die.

**Python details that cost me time.**

- On Python 3.14, worker processes re-import the main script. A script without an `if __name__ == "__main__"` guard crashes with a confusing `ConnectionResetError`.
- A pipe hides failures. `python script.py | grep ...` reports grep's exit code, so a crash looked like success until I set `pipefail`.
- `best = 1.0` as a starting value meant a score of exactly 1.0 never saved. It's `float("inf")` now.

**Keep one page's failure from losing the others.** Each page of a PDF is read in its own process, with a time limit. A blank page or a crash leaves that page out and tells the user, rather than failing the whole score.

**Web app basics.**

- FastAPI serves the page and a JSON API.
- Recognition runs in a separate process, so a crash or memory spike can't kill the server.
- Each edit saves a new numbered MusicXML file. Undo just goes back a file, so there's no complex undo logic to get wrong.
- Verovio draws SVG with stable note IDs, so a click on a note maps to an element in the file.
- The first version of the server failed in worker threads, because Verovio's resource path is per thread. A test now renders from another thread.

**Designing for errors.** A recognizer will always make mistakes, so the product is really the correction loop. Showing the original beside the result, marking doubtful chords and making one-click fixes matter as much as accuracy. When I found about one false fermata per page, a filter would also have deleted real fermatas. A "Clear marks" button was the better fix.

## 14. Building a score editor

**Why rhythm is harder than pitch.** Changing a pitch touches one note. Changing a length touches a whole bar, because MusicXML stores a bar as a list played in order. A note's position is the running total of everything before it, with `backup` and `forward` jumps between voices. Editing that list directly means repairing every jump after every change.

**Change the form, edit, change it back.** So an edited bar is read into a simpler form, a timeline: each voice is a list of events with a start and a length. The edit happens there, and the bar is written out again whole. Bars that weren't edited are never rewritten. Reading a real file and rewriting every bar changed nothing in 106 files and 2,121 bars, which proved this was safe before anything was built on it.

**Exact fractions.** A triplet eighth is a third of a quarter note, and a float can't store a third exactly. Small errors would make two notes that start together look different. Python's `Fraction` keeps 1/3 as 1/3, so three of them add up to exactly 1. MusicXML has the same problem from the other side, because it stores lengths as whole numbers of `divisions` per quarter note. A file with 2 divisions can't hold a 16th, so the editor rescales a score before writing into it.

**Rules that keep a bar valid.** A longer note overwrites what follows, a note that's partly covered becomes rest, and a shorter note leaves a rest. So a bar stays full whatever you type. These are MuseScore's rules. I used its behaviour and keyboard conventions, but not its code, which is GPL.

**Property-based tests and an oracle.** An example test only tries what I thought of. A property-based test states something that must always be true, called an **invariant**, and throws random inputs at the code. After every random edit I check four: IDs are unique, children are in the order the format requires, no tie has one end missing, and bars add up. I also wrote a second, much simpler model of a bar as 16 slots, and the real code has to agree with it at every step. A separate way of getting the right answer is called an **oracle**. Seeds are fixed, so a failure can be replayed. These tests found bugs my examples had missed, including a note left half-tied after being moved.

**Stable identity.** Chord boxes and correction labels pointed at bar numbers. Add a bar and every later number shifts, so a chord would point at the wrong box and the correction loop would write wrong training labels. Each bar now has a permanent ID that remembers its original number. It's the same reason a database row has an ID and not just a position.

**Race conditions.** A race condition is a bug that depends on the order two things happen to finish in. Fast typing sends edits faster than the server answers, which goes wrong in two ways. Two requests can read version 5 and both save version 6, losing an edit. Or the browser can send a note using a cursor from before the last note came back. So the server holds a lock around each read, edit and save, and the browser queues its edits. Without the lock, a test that sends 16 edits at once fails. The browser test types 8 notes with no delay between them.

**A check that needs no answers.** Notes in a bar have to add up to the time signature, which is true without any truth file. On correct files this raised 0 false alarms in 337 bars. On homr's output it found real errors that my note score can't see, like a piece read in 4/4 instead of 3/4, and a note placed half a beat late on a page that scored 1.00. Real use has no labels, so a check from the rules of the domain is often the only validation you get.

**Written length and sounding length.** A triplet is three notes in the time of two, so an eighth in a triplet lasts a third of a beat while still being written as an eighth. A note therefore needs two lengths, and code that mixes them up writes notes that look right and play wrong. MusicXML stores the group in `<time-modification>` on every note, and draws the bracket from `<tuplet>` marks on the first and last. A dotted note inside a triplet can land exactly on a beat, so "does this position look like a beat" can't find where a triplet ends. The unit, `<normal-type>`, can.

**One queue for everything that matters.** The first race fix put edits in a queue, but a length key still took effect at once. Type `c d 6 e` quickly and the half-note choice reached the `d` still waiting to be sent, so the wrong note got the wrong length. The general rule is that anything changing what the next edit means has to wait in the same queue: length, cursor, selection, stopping. The opposite mistake also exists. Reading a form field when a queued save finally runs can pick up a blank that a late reply just drew there. So read what the user typed at the moment they typed it, and read the score's state in turn.

**Testing what can't be seen.** A browser test can't hear. An `OfflineAudioContext` renders the same sound into an array instead of the speakers, faster than real time. The test then measures how strong each pitch is in each half beat, using a single-frequency form of the Fourier transform, and checks the loudest one. That proved pitches, timing, silence in rests and repeats. It can also tell a tied note from two notes of the same pitch, because the sound dips between two separate notes and doesn't across a tie.

**Test where the edges are, at different speeds, in more than one browser.** Typing `won-der` on the last note had a bug in every browser. The hyphen moves to the next note, there isn't one, so the box closed. In Chromium the rest of the word was typed before the reply came back and vanished. In Firefox the reply came first, so the letters reached the editor as commands. The same bug looked different because of timing, and only the second form made a test fail.

**Intervals have two parts.** A semitone count alone can't tell D♯ from E♭. An interval is a number of letter steps plus a number of semitones: a minor third is 2 letters and 3 semitones, so C always lands on E♭. Keys sit on a line of fifths, where each step adds a sharp, and moving a score moves it along that line. If that lands somewhere unreadable, such as 8 flats, moving 12 places gives a key that sounds the same with fewer accidentals.

**Transposing instruments.** A B♭ trumpet playing a written C sounds a B♭, so its part is written a tone higher, two sharps further on. A score stores what's written, plus how far that is from what sounds, and playback adds the difference back.

**Attach things to what they mean.** A staccato belongs to a note and goes with it. A dynamic belongs to a moment. Choosing wrongly means a dynamic vanishes when someone rewrites the note under it. Two-ended marks, such as slurs and hairpins, also need their ends paired up, by number, after anything that could remove one.

**MIDI files.** A MIDI file stores events, like "key down" and "key up", with the wait before each one in ticks. A wait can be any size, so it's written in pieces of 7 bits, with the top bit meaning "more follows". That's why the numbers in the file aren't plain bytes.

**A failed test has two possible culprits.** When a random test fails, either the code or the rule is wrong. Three of my rules were too strict. The way to decide is to state what a person would actually need, and to check whether the problem was there before the edit.

**Mutation testing needs a control.** Putting a bug in and seeing a test fail only counts if that test passes without the bug. Run the unchanged code first, freeze the copy you test, and count only real test failures. A bug that nothing catches means one of two things: a missing test, or code that does nothing. The second is worth deleting.

**Tests can pass for the wrong reason.** Of 51 bugs I put back, 10 got through, and 9 of those were tests that looked right. The usual cause is testing where a behaviour doesn't show: the middle of a bar when the bug is at the end, or a queue with nothing waiting in it. Overflow is the trap in numeric code. A 16-bit sample can't exceed 32,767, so checking that it doesn't proves nothing, and a too-loud value silently wraps round to a wrong one. Check the value you expect, not a limit the type already guarantees.

**Test on data you didn't make.** My benchmark pieces went through a pipeline I control. Scores from the music21 corpus came from other software, and the first thing they showed was notes with no `<voice>` tag.

**Keep it simple until a number says otherwise.** The server redraws the whole score on every edit, which sounds wasteful. I measured it before building anything cleverer, and Verovio redraws 192 bars in 185 ms.

## 15. Repairing output with rules

**What a score flattens, it can't see.** My note score turns a page into one list of notes, so a voice that starts a bar late, or a page in the wrong time signature, can score 1.00. I added two measures that keep that information: `time_right`, the share of bars with the right signature, and `bars_full`, the share of bars whose notes add up.

**Check a new metric against a dumb answer.** 15 of my 18 test pieces are in 4/4, so a program that always says 4/4 gets 0.83 overall. Reporting by signature removes that shelter. homr was right on 4/4, and right on only half of the 3/4 sheets.

**Use a constraint the output must obey.** Bars in a piece are nearly all the same length, so the notes themselves say what the signature must be. Two details matter:

- **Not every bar gets a vote.** A whole-bar rest stretches to whatever signature was read, so it says nothing about the real length. The first and last bars can be pickups, so they don't vote either.
- **Judge each part alone.** homr read Schubert's voice in 4/4 and the piano in 3/4. My first guard said "the score must have one signature", which blocked the very fix it was for.

**All or nothing.** homr sometimes leaves out the instruction to step back to the start of a bar, so a second voice begins late with all the right notes. My first fix moved whatever it could, and the note score on Schumann fell from 0.914 to 0.887, because a half-fixed bar is worse than an untouched one. Now it only touches a bar when every voice then fits.

**Say how thin the evidence is.** The signature repair changed 3 of 88 sheets and got all 3 right, but they are one piece in three versions. The other 85 only show that it raises no false alarms. A repair also can't choose between signatures of the same length: `2/2` and `4/4` both add up.

## 16. Licences

Licences decide what a startup can ship, so I tracked them from the start.

| Licence | Applies to | What it means |
|---|---|---|
| AGPL-3.0 | homr, Audiveris | A network service may have to publish its source code |
| Apache-2.0 | RapidOCR | Permissive |
| CC BY-NC-4.0 | JAZZMUS | No commercial use, so fine for learning only |
| OFL | The fonts | Free to use, including for training data |
| CC0 | OpenScore Lieder | Effectively public domain |

Training on a non-commercial dataset can restrict the resulting model, so what looks like a data decision is really a business decision. Images taken from papers also stay out of the repository.

This is also the second reason for my own note reader. On melody sheets it can run with no AGPL code at all. It isn't a replacement yet, because it can't read several voices on one staff.

## 17. Questions I should be able to answer

1. Why did you split by font instead of by image?
2. Walk through what happens to a 200-pixel crop inside the model.
3. What does the CTC blank do? What goes wrong without it?
4. Your test score was 99.6%. Why isn't that your accuracy?
5. What was your baseline, and how did it change your plan?
6. Why McNemar instead of comparing two confidence intervals?
7. What's an ablation? What did yours show?
8. Why did the page score differ from the crop score, and how did you find out?
9. Why is the real page no longer a valid test?
10. Why did you use rules rather than train a detector?
11. On rendered print your model barely beats a free OCR. Was it worth building?
12. How would you make this a commercial product, given homr's licence?
13. Why does the note reader keep 6 rows of height when the chord reader keeps 1?
14. Why not have the model output pitches directly?
15. CTC doesn't need positions, so how does your reader know where notes are?
16. What's a round-trip test, and why run it before training?
17. Petaluma scored much worse than Leipzig. What was actually wrong, and why didn't you fix it?
18. Why cross-validation on the real page instead of one train and test split?
19. Fine-tuning gained 7 chords and lost 5. What can you conclude?
20. Why were the promotion rules written before any real data existed?
21. How can a correction tool create wrong labels?
22. What did the simulated user prove, and what didn't it prove?
23. Why does the editor rewrite a whole bar when one note changes?
24. Why use fractions rather than floats for positions?
25. What can a property-based test find that an example test can't? What's an oracle?
26. Why do bars need a permanent ID when they already have a number?
27. How can fast typing lose a note, and how do you know your fix works?
28. What did the bar check find that the note score couldn't?
29. Putting old bugs back got past your tests three times. What did you do about each one?
30. Why does a triplet note need two lengths? Why can't you find a triplet's end from positions alone?
31. Typing `c d 6 e` gave the D the wrong length. Why, and what's the general rule that fixes it?
32. How did you test sound without listening to it?
33. A sheet scored 1.00 for notes while a third of its bars were wrong. How can both be true?
34. What would always answering 4/4 score, and what does that tell you about the time signature number?
35. Why does the voice repair refuse to fix part of a bar?
36. A rule raised every synthetic score and you removed it. Why?
37. Why can't the repair tell `2/2` from `4/4`?
38. Why does a whole-bar rest get no vote on the length of a bar?
39. How did a browser difference expose a bug that was in every browser?
40. Why can't you transpose by adding semitones to each note?
41. Should a dynamic belong to a note or to a moment? What goes wrong if you choose badly?
42. A random test failed, and the editor was right. How did you tell?
43. One bug you put back went unnoticed. What are the two possible reasons, and which was it?
44. Why does a mutation test need a control run?
45. Why test on scores from another source when you already have a benchmark?
46. Your check that audio never clips could not fail. Why not, and what did you test instead?

My short answer to question 11: on print it wasn't needed, and the honest conclusion is to use the free reader there. I still learned the most from building it, and the only place it may have an edge is handwriting, which is where I have the least evidence.

## Glossary

| Term | Meaning |
|---|---|
| Ablation | Removing one part to measure what it contributes |
| Augmentation | Altering training data to add variety |
| Baseline | A reference score from a simple or existing method |
| CER | Edit distance divided by the length of the true text |
| CNN | A network that learns local visual patterns |
| CRNN | A CNN followed by a recurrent network |
| CTC | A loss for sequences with unknown alignment |
| Data leakage | Test information reaching training |
| Development set | Data used to guide decisions |
| Distribution shift | Real data differing from test data |
| Domain randomization | Varying synthetic data widely so reality fits inside it |
| Dynamic programming | Solving a problem by reusing answers to smaller ones |
| F1 | The harmonic mean of precision and recall |
| Fine-tuning | Continuing to train an existing model on new data, gently |
| Greedy decoding | Taking the likeliest output at each step |
| Group split | Keeping related items together on one side of a split |
| Human in the loop | Using a person's checks of model output as data |
| Invariant | Something that must stay true after every step |
| K-fold cross-validation | Taking turns holding out each part of the data |
| Label noise | Wrong labels in the training data |
| LSTM | A recurrent layer that carries memory along a sequence |
| McNemar's test | A paired test using only disagreements |
| Morphological opening | Erasing, then regrowing, shapes to keep only those of a chosen form |
| MusicXML | The shared file format for notation |
| Interval | A distance between notes, as letter steps plus semitones |
| Line of fifths | Keys in order, where each step adds a sharp or removes a flat |
| Mutation testing | Breaking code on purpose to check that tests notice |
| Negative sample | An example of what the target is not |
| OMR | Optical music recognition |
| Oracle | A separate way of getting the right answer, used to check code |
| Over- and under-segmentation | Splitting one object apart, or merging several into one |
| Pickup | Notes before the first full bar, which make that bar short |
| Promotion gate | Rules a new model must pass to replace the old one |
| Property-based test | A test that states a rule and tries it on random inputs |
| Race condition | A bug that depends on which of two things finishes first |
| Regression | A change that breaks something that used to work |
| Round-trip test | Converting data there and back to check that nothing changed |
| Selective prediction | Answering only when confident |
| Stable identifier | A name that stays the same when things around it move |
| Time signature | How many beats a bar holds, and what kind of note gets one |
| Tuplet | Notes played in a time they wouldn't normally fill, such as three in the time of two |
| Volta | A first or second ending, played only on that pass through a repeat |
| Wilson interval | A confidence interval that behaves well near 0 and 1 |

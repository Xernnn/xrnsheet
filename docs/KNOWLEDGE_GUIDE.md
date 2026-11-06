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

**Selective prediction.** Measure accuracy at each confidence cutoff. On the test set, keeping readings above 0.8 kept 71% at 99.9% accuracy. That's the logic behind asking a person to check only some chords.

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

## 11. Building it as software

**Tests that prove something.** Passing tests only show the code matches my expectations. **Mutation testing** checks the tests themselves: break the code on purpose and see if a test fails. When I put old bugs back, two got through, including a test font moving into training. A test now pins those fonts by name.

**Keep risky parts in one place.** homr is imported in a single file, so swapping the engine or dealing with its licence stays contained. `chord_boxes.py` takes the reader as a function, so its tests use a fake reader and run in milliseconds.

**Caching experiments.** homr takes about 20 seconds per page, but the chord logic takes under a second. Saving homr's output let me compare five versions of the rule on 13 pages in a few minutes. Fast experiments get run, and slow ones get skipped.

**Running out of resources.** The laptop has little free memory, so each engine run has a memory cap through `systemd-run`. The GPU has 4 GB, so a training batch of 80 needs validation at 48.

**Web app basics.**

- FastAPI serves the page and a JSON API.
- Recognition runs in a separate process, so a crash or memory spike can't kill the server.
- Each edit saves a new numbered MusicXML file. Undo just goes back a file, so there's no complex undo logic to get wrong.
- Verovio draws SVG with stable note IDs, so a click on a note maps to an element in the file.
- The first version of the server failed in worker threads, because Verovio's resource path is per thread. A test now renders from another thread.

**Designing for errors.** A recognizer will always make mistakes, so the product is really the correction loop. Showing the original beside the result, marking doubtful chords and making one-click fixes matter as much as accuracy. When I found about one false fermata per page, a filter would also have deleted real fermatas. A "Clear marks" button was the better fix.

## 12. Licences

Licences decide what a startup can ship, so I tracked them from the start.

| Licence | Applies to | What it means |
|---|---|---|
| AGPL-3.0 | homr, Audiveris | A network service may have to publish its source code |
| Apache-2.0 | RapidOCR | Permissive |
| CC BY-NC-4.0 | JAZZMUS | No commercial use, so fine for learning only |
| OFL | The fonts | Free to use, including for training data |
| CC0 | OpenScore Lieder | Effectively public domain |

Training on a non-commercial dataset can restrict the resulting model, so what looks like a data decision is really a business decision. Images taken from papers also stay out of the repository.

## 13. Questions I should be able to answer

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
| Greedy decoding | Taking the likeliest output at each step |
| LSTM | A recurrent layer that carries memory along a sequence |
| McNemar's test | A paired test using only disagreements |
| Mutation testing | Breaking code on purpose to check that tests notice |
| MusicXML | The shared file format for notation |
| Negative sample | An example of what the target is not |
| OMR | Optical music recognition |
| Over- and under-segmentation | Splitting one object apart, or merging several into one |
| Selective prediction | Answering only when confident |
| Wilson interval | A confidence interval that behaves well near 0 and 1 |

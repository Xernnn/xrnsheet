# Bass guitar samples

The sound of a bass guitar part (any of General MIDI's basses) in playback, Practice, the fretboard
below and exported audio.

They're Karoryfer Samples' Growlybass, a Squier Jazz Bass in standard EADG tuning with roundwound
strings, recorded direct with both pickups, as 44.1 kHz 24-bit mono. These are the forte layer's
first round robin of its sustained notes (`sustain`, `_f_rr1`), ten notes from the low E1 to D♯4:
E1, F♯1, A1, C2, D♯2, F♯2, A2, D♯3, A3 and D♯4. A note between two of them is the nearer one played
a little higher or lower. The library's files are named an octave above the notes they sound (its
`e2` is the open E1 string), and these are named by the note they sound.

- **Taken from:** <https://github.com/sfzinstruments/karoryfer.growlybass>, folder `sustain`, at commit
  `4f483268fc66b5a6d5781d421c0d11b8d08d3fc6`, on 15 November 2026.
- **Made into these files losslessly:** each is the first 2.5 s of its WAV, from just before the note
  begins, encoded as FLAC by `flac 1.5.0 -8 --no-padding --skip --until`. Nothing was resampled,
  remixed, requantised or normalised: each file decodes to exactly the samples of that stretch of
  the original, which was checked sample for sample. A note held longer than that is damped as its
  recording runs out.
- **Licence:** [Creative Commons Zero 1.0](https://creativecommons.org/publicdomain/zero/1.0/) (CC0),
  as the repository's `LICENSE` says. Karoryfer's early releases were described as CC BY 3.0, so the
  credit is given here and in the main README all the same, which would meet that licence too:
  "Growlybass by Karoryfer Samples".

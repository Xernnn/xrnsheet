# Cello samples

The sound of a cello part in playback, Practice, the fingerboard below and exported audio.

They're the Karoryfer x bigcat cello, a solo cello played by Kamila Borowiak, by Karoryfer Samples
and bigcat instruments, close-miked and without vibrato, as 44.1 kHz 24-bit mono:

- **Bowed** (this folder): the mezzo-forte layer of its sustained notes, down bow (`sus`, `_mf_d`),
  twelve notes from C2 to C6: every minor third from C2 to F♯3 (C, D♯, F♯ and A), then C4, F♯4,
  C5, F♯5 and C6.
- **Plucked** (`pizzicato/`): the first round robin of its plucked notes (`pizzcato`, `_1`), every
  tritone from C2 to C5: C2, F♯2, C3, F♯3, C4, F♯4 and C5.

The library's files are named an octave below the notes they sound (its `C1` is the cello's low
C2), and these are named by the note they sound. A note between two of them is the nearer one
played a little higher or lower.

- **Taken from:** <https://github.com/sfzinstruments/karoryfer-bigcat.cello>, folder `Samples`, at
  commit `6fd75fbfc1dbb3109bf26220ba1adea46188a18b`, on 15 November 2026. Karoryfer's own page for
  it, <https://shop.karoryfer.com/pages/free-karoryfer-x-bigcat-cello>, downloads it from there.
- **Made into these files losslessly:** each is the first 1.5 s (bowed) or 1.0 s (plucked) of its WAV,
  from just before the note begins, encoded as FLAC by `flac 1.5.0 -8 --no-padding --skip --until`.
  Nothing was resampled, remixed, requantised or normalised: each file decodes to exactly the
  samples of that stretch of the original, which was checked sample for sample.
- **Licence:** [Creative Commons Zero 1.0](https://creativecommons.org/publicdomain/zero/1.0/) (CC0),
  as the repository's `LICENSE` says, and its README says it's royalty-free for any use. Karoryfer's
  early releases were described as CC BY 3.0, so the credit is given here and in the main README all
  the same, which would meet that licence too: "Karoryfer x bigcat cello by Karoryfer Samples and
  bigcat instruments, played by Kamila Borowiak".

# Double bass samples

The sound of a double bass part in playback, Practice, the fingerboard below and exported audio.

- **Bowed** (this folder): the solo double bass of the Versilian Studios Chamber Orchestra 2
  Community Edition (VSCO 2 CE), recorded by Sam Gossner and Simon Dalzell, with sample cutting by
  Elan Hickler (Soundemote), as 44.1 kHz 16-bit stereo. These are the louder layer of its sustained
  notes with vibrato (`SusVib`, `_v3_rr1`), nine notes from F♯1 to B3: F♯1, A♯1, D2, F♯2, A2, C♯3,
  E3, G♯3 and B3. Its lowest notes, E1 and F1, are the F♯1 played lower.
  - **Taken from:** <https://github.com/sgossner/VSCO-2-CE>, folder `Strings/Solo Contrabass`, at
    commit `440300901dfe9275fd84e0b7763af1f8443ae62e`, on 15 November 2026.
  - **Licence:** [Creative Commons Zero 1.0](https://creativecommons.org/publicdomain/zero/1.0/)
    (CC0), as the repository's `LICENSE` says.
- **Plucked** (`pizzicato/`): Karoryfer Samples' Meatbass, a 1958 Otto Rubner double bass played by
  Drogomir Smolken and recorded by Ludwik Zamenhof, as 44.1 kHz 24-bit mono, as VSCO's quiet
  pizzicato varied too much from note to note. These are its third velocity layer's first round
  robin (`pizz`, `_vl3_rr1`), seven notes: D♯1, F♯1, A1, D♯2, A2, F♯3 and C4.
  - **Taken from:** <https://github.com/sfzinstruments/karoryfer.meatbass>, folder `Samples/pizz`,
    at commit `ac9e859564bda286ab5ec672d00ff1aa2fef2895`, on 15 November 2026. Karoryfer's own page
    for it, <https://shop.karoryfer.com/pages/free-meatbass>, downloads it from there.
  - **Licence:** [Creative Commons Zero 1.0](https://creativecommons.org/publicdomain/zero/1.0/)
    (CC0), as the repository's `LICENSE` says, and its README says it's royalty-free for any use.
    Karoryfer's early releases were described as CC BY 3.0, so the credit is given here and in the
    main README all the same, which would meet that licence too: "Meatbass by Karoryfer Samples".

VSCO's files are named an octave below the notes they sound (its `F#0` is F♯1), Meatbass's by the
note itself, and these are named by the note they sound. A note between two of them is the nearer
one played a little higher or lower.

**Made into these files losslessly:** each is the first 1.5 s (bowed) or 1.0 s (plucked) of its WAV,
from just before the note begins, encoded as FLAC by `flac 1.5.0 -8 --no-padding --skip --until`.
Nothing was resampled, remixed, requantised or normalised: each file decodes to exactly the samples
of that stretch of the original, which was checked sample for sample. CC0 asks for no credit, but
it's given here and in the main README.

# Violin samples

The sound of a violin part in playback, Practice, the fingerboard below and exported audio.

They're the solo violin of the Versilian Studios Chamber Orchestra 2 Community Edition (VSCO 2 CE),
recorded by Sam Gossner and Simon Dalzell, with sample cutting by Elan Hickler (Soundemote), as
44.1 kHz 16-bit stereo:

- **Bowed** (this folder): the forte layer of its sustained notes with vibrato ("Arco Vib", `_f`),
  ten notes from G3 to A6: G3, C4, E4, G4, C5, E5, G5, C6, E6 and A6. A held note goes round the
  steady part of its recording for as long as the bow is drawn.
- **Plucked** (`pizzicato/`): the forte layer's first round robin of its pizzicato ("Pizz", `_f_RR1`),
  eight notes: G3, C4, E4, G4, C5, E5, A5 and A6.

A note between two of them is the nearer one played a little higher or lower.

- **Taken from:** <https://github.com/sgossner/VSCO-2-CE>, folder `Strings/Solo Violin`, at commit
  `440300901dfe9275fd84e0b7763af1f8443ae62e`, on 15 November 2026. More about the library:
  <http://vis.versilstudios.net/vsco-community.html>.
- **Made into these files losslessly:** each is the first 1.5 s (bowed) or 1.0 s (plucked) of its WAV,
  from just before the note begins, encoded as FLAC by `flac 1.5.0 -8 --no-padding --skip --until`.
  Nothing was resampled, remixed, requantised or normalised: each file decodes to exactly the
  samples of that stretch of the original, which was checked sample for sample. The file names give
  the note it sounds (`Cs4` is C♯4).
- **Licence:** [Creative Commons Zero 1.0](https://creativecommons.org/publicdomain/zero/1.0/) (CC0),
  as the repository's `LICENSE` says: dedicated to the public domain, so no credit is required. It's
  given here and in the main README all the same.

# Viola samples

The sound of a viola part in playback, Practice, the fingerboard below and exported audio.

They're the viola section of the Versilian Studios Chamber Orchestra 2 Community Edition (VSCO 2 CE),
recorded by Sam Gossner and Simon Dalzell, with sample cutting by Elan Hickler (Soundemote), as
44.1 kHz 24-bit stereo. The library has no solo viola, so a viola part sounds like a few violas
playing together.

- **Bowed** (this folder): the louder layer of its sustained notes with vibrato (`susvib`, `_v2_1`),
  eight notes from C3 to B5: C3, E3, G3, B3, F4, A4, E5 and B5. They're further apart above F4 than
  the other instruments' notes, as a stereo 24-bit recording is twice the size of the violin's.
- **Plucked** (`pizzicato/`): the louder layer's first round robin of its pizzicato (`pizz`, `_v2_rr1`),
  six notes: C3, E3, G3, D4, A4 and G5.

The library's files are named an octave below the notes they sound (its `C2` is the viola's low
C3), and these are named by the note they sound. A note between two of them is the nearer one
played a little higher or lower.

- **Taken from:** <https://github.com/sgossner/VSCO-2-CE>, folder `Strings/Viola Section`, at commit
  `440300901dfe9275fd84e0b7763af1f8443ae62e`, on 15 November 2026. More about the library:
  <http://vis.versilstudios.net/vsco-community.html>.
- **Made into these files losslessly:** each is the first 1.5 s (bowed) or 1.0 s (plucked) of its WAV,
  from just before the note begins, encoded as FLAC by `flac 1.5.0 -8 --no-padding --skip --until`.
  Nothing was resampled, remixed, requantised or normalised: each file decodes to exactly the
  samples of that stretch of the original, which was checked sample for sample.
- **Licence:** [Creative Commons Zero 1.0](https://creativecommons.org/publicdomain/zero/1.0/) (CC0),
  as the repository's `LICENSE` says: dedicated to the public domain, so no credit is required. It's
  given here and in the main README all the same.

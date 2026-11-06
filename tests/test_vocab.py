import pytest

from chords.vocab import ALPHABET, QUALITIES, Chord, parse_chord


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("Cmaj7", Chord("C", "maj7")),
        ("CM7", Chord("C", "maj7")),
        ("CΔ7", Chord("C", "maj7")),
        ("C-7", Chord("C", "m7")),
        ("Bbø", Chord("Bb", "m7b5")),
        ("F#m7b5", Chord("F#", "m7b5")),
        ("Db7(b9)", Chord("Db", "7b9")),
        ("C6/9", Chord("C", "6/9")),
        ("G7/B", Chord("G", "7", "B")),
        ("F/C", Chord("F", "", "C")),
        ("B♭7", Chord("Bb", "7")),
    ],
)
def test_spellings_of_a_chord_mean_the_same_thing(printed, expected):
    assert parse_chord(printed) == expected


@pytest.mark.parametrize("text", ["Hello", "N.C.", "5", "", "Be", "Piano", "H7"])
def test_other_text_is_not_a_chord(text):
    assert parse_chord(text) is None


def test_every_listed_spelling_parses_to_its_own_meaning():
    for meaning, (_, spellings) in QUALITIES.items():
        for spelling in spellings:
            assert parse_chord("C" + spelling) == Chord("C", meaning)


def test_alphabet_covers_every_spelling():
    for _, spellings in QUALITIES.values():
        assert all(character in ALPHABET for spelling in spellings for character in spelling)

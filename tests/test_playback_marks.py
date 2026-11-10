"""What the marks on a page do to what's heard."""

import pytest

from pipeline import edit, marks, structure
from pipeline.playback import play_order, playback


def blank(**settings):
    root = structure.blank_score(**{"measures": 4} | settings)
    edit.ensure_note_ids(root)
    return root


def melody(root, *steps, length="quarter"):
    cursor = next(root.iter("note")).get("id")
    written = []
    for step in steps:
        done = edit.write_note(root, cursor, length, 0, step)
        written.append(done["written"])
        cursor = done["cursor"]
    return written


def tune():
    root = blank()
    return root, melody(root, *("CDEF" * 4))


def heard(root):
    return playback(root)["notes"]


def volumes(root):
    return [item["volume"] for item in heard(root)]


def order(root):
    return play_order(root.find("part").findall("measure"))


def test_music_with_no_marks_plays_as_it_did():
    root, _ = tune()
    notes = heard(root)
    assert [item["start"] for item in notes] == list(range(16))
    assert {item["length"] for item in notes} == {1.0}
    assert len({item["volume"] for item in notes}) == 1


def test_a_dynamic_sets_the_loudness_from_there_on():
    root, ids = tune()
    marks.set_dynamic(root, ids[0], "pp")
    marks.set_dynamic(root, ids[4], "ff")
    found = volumes(root)
    assert found[0] == found[3] < found[4] == found[15]


def test_dynamics_get_louder_in_order():
    levels = []
    for name in ("ppp", "pp", "p", "mp", "mf", "f", "ff", "fff"):
        root, ids = tune()
        marks.set_dynamic(root, ids[0], name)
        levels.append(volumes(root)[0])
    assert levels == sorted(set(levels))


def test_a_crescendo_grows_note_by_note_and_lands_on_the_next_dynamic():
    root, ids = tune()
    marks.set_dynamic(root, ids[0], "p")
    marks.toggle_hairpin(root, ids[0], ids[7], "crescendo")
    marks.set_dynamic(root, ids[8], "f")
    found = volumes(root)
    assert found[:8] == sorted(found[:8]) and found[0] < found[7]
    assert found[8] == pytest.approx(96 / 127)


def test_a_diminuendo_with_nothing_after_it_still_falls():
    root, ids = tune()
    marks.set_dynamic(root, ids[0], "f")
    marks.toggle_hairpin(root, ids[0], ids[3], "diminuendo")
    found = volumes(root)
    assert found[3] < found[0]
    assert found[4] < found[0]
    assert found[4] == found[15]


def test_a_hairpin_on_one_note_changes_what_follows():
    root, ids = tune()
    marks.toggle_hairpin(root, ids[0], ids[0], "crescendo")
    found = volumes(root)
    assert found[1] > found[0]


def test_sforzando_is_one_loud_note():
    root, ids = tune()
    marks.set_dynamic(root, ids[1], "sfz")
    found = volumes(root)
    assert found[1] > found[0]
    assert found[2] == found[0]


def test_forte_piano_stays_soft_after_its_note():
    root, ids = tune()
    marks.set_dynamic(root, ids[1], "fp")
    found = volumes(root)
    assert found[1] > found[0] > found[2]


def test_each_part_keeps_its_own_dynamics():
    root, ids = tune()
    marks.set_dynamic(root, ids[0], "pp")
    structure.add_part(root, "piano")
    edit.ensure_note_ids(root)
    second = root.findall("part")[1].find("measure").find("note").get("id")
    edit.write_note(root, second, "quarter", 0, "C")
    found = heard(root)
    first_beat = sorted((item for item in found if item["start"] == 0), key=lambda item: item["volume"])
    assert first_beat[0]["volume"] < first_beat[1]["volume"]


@pytest.mark.parametrize(("name", "share"), [("staccato", 0.5), ("staccatissimo", 0.25)])
def test_short_marks_shorten_the_sound_and_not_the_time(name, share):
    root, ids = tune()
    marks.toggle_mark(root, [ids[1]], name)
    notes = heard(root)
    assert notes[1]["length"] == share
    assert notes[2]["start"] == 2


def test_accents_are_louder():
    root, ids = tune()
    marks.toggle_mark(root, [ids[1]], "accent")
    marks.toggle_mark(root, [ids[2]], "marcato")
    found = volumes(root)
    assert found[0] < found[1] < found[2]


def test_every_note_of_a_chord_takes_its_marks():
    root, ids = tune()
    edit.add_pitch(root, ids[0], "E")
    marks.toggle_mark(root, [ids[0]], "staccato")
    first_beat = [item for item in heard(root) if item["start"] == 0]
    assert [item["length"] for item in first_beat] == [0.5, 0.5]


def test_a_tied_note_isnt_cut_short_by_a_mark():
    root = blank()
    ids = melody(root, "C", "C", "D", "E")
    edit.toggle_tie(root, ids[0])
    marks.toggle_mark(root, [ids[0]], "staccato")
    assert heard(root)[0]["length"] == 2


def test_a_fermata_slows_only_its_note():
    root, ids = tune()
    marks.toggle_mark(root, [ids[3]], "fermata")
    data = playback(root)
    assert data["tempos"] == [{"start": 0.0, "bpm": 100}, {"start": 3.0, "bpm": 50.0}, {"start": 4.0, "bpm": 100}]


def test_fermatas_in_two_staves_dont_slow_twice():
    root = blank(clef="piano")
    first = root.find("part").find("measure").findall("note")
    upper = edit.write_note(root, first[0].get("id"), "whole", 0, "C")["written"]
    lower = edit.write_note(root, first[1].get("id"), "whole", 0, "C")["written"]
    marks.toggle_mark(root, [upper], "fermata")
    marks.toggle_mark(root, [lower], "fermata")
    assert [item["bpm"] for item in playback(root)["tempos"]] == [50.0, 100]


def test_a_tempo_change_starts_at_its_note():
    root, ids = tune()
    marks.set_tempo(root, ids[0], "60")
    marks.set_tempo(root, ids[8], "120")
    data = playback(root)
    assert data["tempo"] == 60
    assert data["tempos"] == [{"start": 0.0, "bpm": 60.0}, {"start": 8.0, "bpm": 120.0}]


def test_a_tempo_change_comes_round_again_with_a_repeat():
    root, ids = tune()
    marks.set_tempo(root, ids[0], "60")
    marks.set_tempo(root, ids[4], "120")
    structure.toggle_repeat(root, 1, "backward")
    assert [item["start"] for item in playback(root)["tempos"]] == [0.0, 4.0, 8.0, 12.0]


def test_a_grace_note_is_heard_first_and_takes_from_its_note():
    root, ids = tune()
    grace = marks.add_grace(root, ids[1])
    edit.ensure_note_ids(root)
    edit.move_note(root, grace, 1)
    notes = heard(root)
    assert notes[1]["id"] == grace
    assert notes[1]["start"] == 1
    assert notes[2]["id"] == ids[1]
    assert notes[2]["start"] == pytest.approx(1 + 0.125)
    assert notes[2]["start"] + notes[2]["length"] == pytest.approx(2)


def test_a_long_grace_note_takes_half():
    root, ids = tune()
    grace = marks.add_grace(root, ids[1], slashed=False)
    edit.ensure_note_ids(root)
    notes = heard(root)
    assert next(item for item in notes if item["id"] == grace)["length"] == 0.5


def test_swing_makes_eighths_long_then_short():
    root = blank()
    ids = melody(root, *"CDEFGABC", length="eighth")
    marks.set_swing(root, ids[0], True)
    notes = heard(root)
    assert notes[1]["start"] == pytest.approx(2 / 3)
    assert notes[2]["start"] == 1
    assert notes[0]["length"] == pytest.approx(2 / 3)
    assert notes[1]["length"] == pytest.approx(1 / 3)


def test_quarters_dont_swing():
    root, ids = tune()
    marks.set_swing(root, ids[0], True)
    assert [item["start"] for item in heard(root)] == list(range(16))


def test_swing_can_stop():
    root = blank()
    ids = melody(root, *("CDEFGABC" * 2), length="eighth")
    marks.set_swing(root, ids[0], True)
    marks.set_swing(root, ids[8], False)
    notes = heard(root)
    assert notes[1]["start"] == pytest.approx(2 / 3)
    assert notes[9]["start"] == 4.5


def test_a_trill_alternates_with_the_note_above_in_the_key():
    root = blank(fifths=-1)  # F major, so a trill on A goes up to B flat.
    ids = melody(root, *"ABCD", length="quarter")
    marks.toggle_mark(root, [ids[0]], "trill")
    trill = [item["midi"] for item in heard(root) if item["id"] == ids[0]]
    base = trill[0]
    assert set(trill) == {base, base + 1}
    assert len(trill) == 8


def test_a_mordent_goes_down_and_back():
    root, ids = tune()
    marks.toggle_mark(root, [ids[0]], "mordent")
    pitches = [item["midi"] for item in heard(root) if item["id"] == ids[0]]
    assert pitches[0] == pitches[2] == pitches[3]
    assert pitches[1] == pitches[0] - 1


def test_a_turn_circles_the_note():
    root, ids = tune()
    marks.toggle_mark(root, [ids[1]], "turn")
    pitches = [item["midi"] for item in heard(root) if item["id"] == ids[1]]
    assert pitches == [pitches[1] + 2, pitches[1], pitches[1] - 2, pitches[1], pitches[1]]


def test_an_ornament_keeps_its_note_length():
    root, ids = tune()
    marks.toggle_mark(root, [ids[0]], "turn")
    pieces = [item for item in heard(root) if item["id"] == ids[0]]
    assert sum(item["length"] for item in pieces) == pytest.approx(1)


def jumps_score(*steps):
    root = blank(measures=6)
    ids = melody(root, *("CDEF" * 6))
    for name, bar in steps:
        marks.set_jump(root, ids[bar * 4], name)
    return root, ids


def test_da_capo_goes_back_to_the_start():
    root, _ = jumps_score(("dc", 2))
    assert order(root) == [0, 1, 2, 0, 1, 2, 3, 4, 5]


def test_da_capo_al_fine_stops_at_fine():
    root, _ = jumps_score(("fine", 1), ("dcfine", 3))
    assert order(root) == [0, 1, 2, 3, 0, 1]


def test_fine_means_nothing_the_first_time_through():
    root, _ = jumps_score(("fine", 1))
    assert order(root) == [0, 1, 2, 3, 4, 5]


def test_dal_segno_goes_back_to_the_sign():
    root, _ = jumps_score(("segno", 1), ("ds", 3))
    assert order(root) == [0, 1, 2, 3, 1, 2, 3, 4, 5]


def test_dal_segno_al_coda_jumps_to_the_coda():
    root, _ = jumps_score(("segno", 1), ("tocoda", 2), ("dscoda", 3), ("coda", 5))
    assert order(root) == [0, 1, 2, 3, 4 - 3, 2, 5]


def test_a_jump_is_only_taken_once():
    root, _ = jumps_score(("dc", 1), ("dc", 4))
    played = order(root)
    assert played.count(5) == 1
    assert len(played) < 20


def test_a_dal_segno_with_no_sign_plays_straight_on():
    root, _ = jumps_score(("ds", 2))
    assert order(root) == [0, 1, 2, 3, 4, 5]


def test_repeats_arent_taken_after_a_jump():
    root, _ = jumps_score(("dc", 3))
    structure.toggle_repeat(root, 1, "backward")
    assert order(root) == [0, 1, 0, 1, 2, 3, 0, 1, 2, 3, 4, 5]


def test_only_the_last_ending_is_played_after_a_jump():
    root, _ = jumps_score(("dc", 3))
    structure.set_ending(root, 1, 1, 1)
    structure.set_ending(root, 2, 2, 2)
    structure.toggle_repeat(root, 1, "backward")
    assert order(root) == [0, 1, 0, 2, 3, 0, 2, 3, 4, 5]


def test_notes_follow_the_jumps():
    root, ids = jumps_score(("dc", 0))
    assert [item["id"] for item in heard(root)][:8] == ids[:4] * 2


def test_a_transposing_instrument_sounds_where_it_should():
    root = blank()
    structure.add_part(root, "trumpet")
    edit.ensure_note_ids(root)
    second = root.findall("part")[1].find("measure").find("note").get("id")
    written = edit.write_note(root, second, "quarter", 0, "D")["written"]
    note = edit.find_note(root, written)
    octave = int(note.findtext("pitch/octave"))
    assert heard(root)[0]["midi"] == (octave + 1) * 12 + 2 - 2


def tied_across_voices(first_voice, second_voice):
    """Two quarter notes on one pitch, tied, with each in the voice asked for."""
    root = structure.blank_score(measures=1)
    edit.ensure_note_ids(root)
    done = edit.write_note(root, next(root.iter("note")).get("id"), "quarter", 0, "C")
    following = edit.write_note(root, done["cursor"], "quarter", 0, "C")
    edit.toggle_tie(root, done["written"])
    if first_voice != 1:
        edit.move_to_voice(root, [done["written"]], first_voice)
    if second_voice != 1:
        edit.move_to_voice(root, [following["written"]], second_voice)
    return root


@pytest.mark.parametrize(("first_voice", "second_voice"), [(1, 2), (2, 1)])
def test_a_tie_across_voices_sounds_once(first_voice, second_voice):
    root = tied_across_voices(first_voice, second_voice)
    assert sum(tie.get("type") == "start" for tie in root.iter("tie")) == 1
    sounds = [note for note in playback(root)["notes"]]
    assert [(note["start"], note["length"]) for note in sounds] == [(0.0, 2.0)]


def test_two_voices_tied_on_one_pitch_ring_apart():
    root = structure.blank_score(measures=2)
    edit.ensure_note_ids(root)
    upper = edit.write_note(root, next(root.iter("note")).get("id"), "whole", 0, "C")
    later_upper = edit.write_note(root, upper["cursor"], "whole", 0, "C")["written"]
    lower = edit.write_note(root, upper["written"], "whole", 0, "C", voice=2)["written"]
    edit.write_note(root, later_upper, "half", 0, "C", voice=2)
    edit.toggle_tie(root, upper["written"])
    edit.toggle_tie(root, lower)
    assert sorted((note["start"], note["length"]) for note in playback(root)["notes"]) == [(0.0, 6.0), (0.0, 8.0)]

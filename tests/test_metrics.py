from chords.metrics import character_error_rate, is_correct, label_kind, summarize, wilson_interval


def test_a_different_spelling_of_the_same_chord_is_correct():
    assert is_correct("CM7", "CΔ7")


def test_wrong_chords_and_non_chords_are_wrong():
    assert not is_correct("C7", "Cm7")
    assert not is_correct("", "C7")
    assert not is_correct("Hello", "C7")


def test_character_error_rate_counts_one_substitution():
    assert character_error_rate(["Cm7"], ["Cm9"]) == 1 / 3


def test_wilson_interval_stays_in_range_and_widens_with_less_data():
    low_small, high_small = wilson_interval(9, 10)
    low_big, high_big = wilson_interval(900, 1000)
    assert 0 <= low_small < low_big < high_big < high_small <= 1


def test_wilson_interval_at_perfect_score_still_admits_doubt():
    low, high = wilson_interval(10, 10)
    assert low < 1 and round(high, 9) == 1


def test_label_kind():
    assert label_kind("CΔ7") == "special symbol"
    assert label_kind("Bb7") == "flat or sharp"
    assert label_kind("Dm7") == "plain"


def test_summary_slices():
    rows = [
        {"font": "a", "style": "jazz", "damage": False, "label": "C7", "predicted": "C7"},
        {"font": "a", "style": "jazz", "damage": True, "label": "C7", "predicted": "F7"},
    ]
    summary = summarize(rows)
    assert summary["all"]["accuracy"] == 0.5
    assert summary["condition: clean"]["accuracy"] == 1.0
    assert summary["condition: damaged"]["accuracy"] == 0.0

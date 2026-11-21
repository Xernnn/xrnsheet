"""The benchmark's answers in data/truth, and what they say about who owns them."""

import re
from pathlib import Path

import pytest

from bench.make_samples import without_empty_rights

TRUTH = sorted((Path(__file__).parent.parent / "data" / "truth").glob("*.musicxml"))


@pytest.mark.parametrize("path", TRUTH, ids=lambda path: path.stem)
def test_no_answer_claims_a_copyright_for_no_one(path):
    """Schubert's Der Lindenbaum came from music21's corpus with Sibelius's empty "Copyright ©", a claim
    naming no holder on music in the public domain. music21's corpus licence names none either."""
    for rights in re.findall(r"<rights>(.*?)</rights>", path.read_text(encoding="utf-8"), re.S):
        assert re.sub(r"copyright|©|\(c\)|\s", "", rights, flags=re.I), f"{path.name}: {rights!r}"


def test_making_the_samples_again_leaves_an_empty_claim_out_and_keeps_a_real_one():
    written = '  <identification>\n    <creator type="composer">Franz Schubert</creator>\n    <rights>Copyright ©</rights>\n    <encoding>'
    assert without_empty_rights(written) == '  <identification>\n    <creator type="composer">Franz Schubert</creator>\n    <encoding>'
    for empty in ["<rights>Copyright © </rights>", "<rights></rights>", "<rights>(c)</rights>"]:
        assert "rights" not in without_empty_rights(f"<identification>\n    {empty}\n</identification>")
    for real in ["<rights>Public Domain</rights>", "<rights>Copyright © 1911 Irving Berlin</rights>"]:
        assert without_empty_rights(f"<identification>\n    {real}\n</identification>") == f"<identification>\n    {real}\n</identification>"

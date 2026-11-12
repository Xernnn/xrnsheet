"""The mixer: kept beside the score, read back from MusicXML, and written into MusicXML and MIDI."""

import threading
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app import server
from pipeline import mixer


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "JOB_DIR", tmp_path)
    monkeypatch.setattr(server, "stopping", threading.Event())
    return TestClient(server.app, base_url="http://127.0.0.1:8000")


def blank(client, **settings):
    return client.post("/api/scores/blank", json={"measures": 2, **settings}).json()["id"]


def set_mixer(client, score_id, **settings):
    response = client.post(f"/api/scores/{score_id}/mixer", json=settings)
    assert response.status_code == 200, response.text
    return response.json()["mixer"]


def test_a_channel_is_kept_within_its_range():
    assert mixer.channel({"volume": 40, "pan": -300, "mute": 1}) == {"volume": 12.0, "pan": -100.0, "mute": True, "solo": False}
    assert mixer.channel({"volume": float("nan")})["volume"] == 0.0
    assert mixer.settled({"parts": {"P9": {"volume": -3}}}, ["P1"]) == {"parts": {"P1": mixer.channel()}, "chords": mixer.channel(), "metronome": mixer.channel(), "master": mixer.channel()}


def test_the_usual_level_is_musescores_usual_midi_volume():
    assert (mixer.midi_volume(0), mixer.midi_volume(-60), mixer.midi_volume(12), mixer.midi_pan(0), mixer.midi_pan(-100), mixer.midi_pan(100)) == (100, 0, 127, 64, 1, 127)
    assert mixer.musicxml_volume(0) == 78.7402, "what MuseScore writes for an instrument at its usual level"
    assert round(mixer.decibels(78.7402), 3) == 0


def test_a_score_starts_with_every_channel_at_its_usual_level(client):
    data = client.get(f"/api/scores/{blank(client, more=['flute'])}").json()
    assert data["partIds"] == ["P1", "P2"]
    assert data["mixer"]["parts"]["P2"] == {"volume": 0.0, "pan": 0.0, "mute": False, "solo": False}


def test_the_mixer_is_kept_but_is_not_an_edit(client):
    score_id = blank(client)
    settled = set_mixer(client, score_id, parts={"P1": {"volume": -6, "pan": 30, "solo": True}}, metronome={"volume": -10})
    assert settled["parts"]["P1"] == {"volume": -6.0, "pan": 30.0, "mute": False, "solo": True}
    data = client.get(f"/api/scores/{score_id}").json()
    assert data["mixer"] == settled and data["canUndo"] is False
    assert client.post(f"/api/scores/{score_id}/mixer", json={"master": {"volume": "loud"}}).status_code == 422


def test_musicxml_gets_the_volume_and_pan_and_brings_them_back(client):
    score_id = blank(client, more=["flute"])
    plain = ET.fromstring(client.get(f"/api/scores/{score_id}/score.musicxml").text)
    assert plain.find(".//midi-instrument/volume") is None, "a score nobody has mixed is exported as it was"
    set_mixer(client, score_id, parts={"P1": {"volume": -12, "pan": -50}, "P2": {"pan": 100}})
    text = client.get(f"/api/scores/{score_id}/score.musicxml").text
    exported = ET.fromstring(text)
    midi = exported.find("part-list/score-part/midi-instrument")
    assert [child.tag for child in midi] == ["midi-program", "volume", "pan"]
    assert (midi.findtext("volume"), midi.findtext("pan")) == ("39.3701", "-45")
    reopened = client.post("/api/scores", files={"file": ("mixed.musicxml", text.encode())}).json()["id"]
    levels = client.get(f"/api/scores/{reopened}").json()["mixer"]["parts"]
    assert round(levels["P1"]["volume"], 1) == -12.0 and levels["P1"]["pan"] == -50 and levels["P2"]["pan"] == 100


def test_midi_gets_the_volume_and_pan_of_each_instrument(client):
    score_id = blank(client)
    set_mixer(client, score_id, parts={"P1": {"volume": -12, "pan": 100}})
    data = client.get(f"/api/scores/{score_id}/score.mid").content
    assert bytes([0xB0, 7, 50]) in data and bytes([0xB0, 10, 127]) in data


def test_a_removed_instruments_levels_do_not_pass_to_the_next_one_added(client):
    score_id = blank(client, more=["flute"])
    set_mixer(client, score_id, parts={"P2": {"volume": -30, "mute": True}})
    client.post(f"/api/scores/{score_id}/parts", json={"action": "remove", "index": 1})
    client.post(f"/api/scores/{score_id}/parts", json={"action": "add", "names": ["oboe"]})
    data = client.get(f"/api/scores/{score_id}").json()
    assert data["partIds"] == ["P1", "P2"] and data["mixer"]["parts"]["P2"] == mixer.channel()

// Playback: rewind, play, loop, the metronome, the position as a measure and beat and as a time, and
// the speed, in the top bar. Audio export is made by the same code, and Practice adds to it.
const AUDIO_RATE = 44100;
const AUDIO_TAIL_SECONDS = 0.5;
const START_DELAY = 0.1; // Seconds between pressing Play and the first note, so nothing is cut short.
// General MIDI groups its sounds in eights, so the group says what kind of instrument this is.
// They're rough stand-ins to tell parts apart, and the levels even out how loud each wave sounds.
const FAMILY_SOUNDS = {
  5: { wave: "sawtooth", level: 0.7 }, // Strings.
  6: { wave: "sawtooth", level: 0.7 }, // Ensembles and voices.
  7: { wave: "sawtooth", level: 0.7 }, // Brass.
  8: { wave: "square", level: 0.5 }, // Reeds.
  9: { wave: "sine", level: 1.2 }, // Flutes.
};
const USUAL_SOUND = { wave: "triangle", level: 1 };
const TICK = { strong: 1760, weak: 1320, seconds: 0.04, level: 0.35 }; // The metronome: higher on the first beat of a bar.
const SLOWEST_SPEED = 10, FASTEST_SPEED = 300; // Percent of the written tempo, as MuseScore allows.
const EARLY = 1e-9; // Places are sums of fractions, so a beat may land a hair before where it's due.
// A pan in the middle gives each speaker this much less of a sound, so it's given back, and a score
// nobody has mixed sounds just as it did before there was a mixer.
const PAN_LAW = Math.SQRT2;
// Where playback starts and what it does. The place is in quarter notes along the music as it's
// played, with its repeats, so it means the same here as the times the server sends.
const playState = {
  place: 0,
  looping: false,
  loop: null, // The selection the loop goes round: { first, last } note ids, or null ends for the whole score.
  metronome: readSetting("metronome", "off") === "on",
  chords: readSetting("chordSymbols", "on") === "on",
  speed: 1,
  data: null, // The latest playback, for the position and tempo shown while nothing plays.
  wanted: 0,
  chosen: null, // A note or rest selected since, which playback starts from instead, as in MuseScore.
  starting: null, // Playback on its way, while its notes are fetched: Stop before then stops it starting.
};
let player = null;

function tone(audio, midi, at, length, volume, wave, destination = audio.destination) {
  const oscillator = audio.createOscillator();
  const gain = audio.createGain();
  const end = at + Math.max(length, 0.05);
  oscillator.type = wave;
  oscillator.frequency.value = 440 * 2 ** ((midi - 69) / 12);
  gain.gain.setValueAtTime(0, at);
  gain.gain.linearRampToValueAtTime(volume, at + 0.012);
  gain.gain.linearRampToValueAtTime(volume * 0.7, Math.max(at + 0.02, end - 0.04));
  gain.gain.linearRampToValueAtTime(0, end);
  oscillator.connect(gain).connect(destination);
  oscillator.start(at);
  oscillator.stop(end + 0.02);
}

// A metronome's tick: a short, bright click that dies away at once.
function tick(audio, at, strong, destination = audio.destination) {
  const oscillator = audio.createOscillator();
  const gain = audio.createGain();
  oscillator.type = "sine";
  oscillator.frequency.value = strong ? TICK.strong : TICK.weak;
  gain.gain.setValueAtTime(0, at);
  gain.gain.linearRampToValueAtTime(TICK.level, at + 0.002);
  gain.gain.exponentialRampToValueAtTime(0.001, at + TICK.seconds);
  oscillator.connect(gain).connect(destination);
  oscillator.start(at);
  oscillator.stop(at + TICK.seconds + 0.01);
}

// Turns places in quarter notes into seconds, which takes adding up every tempo before them, and
// back again with `place`.
function clockFor(data, speed) {
  const changes = data.tempos?.length ? data.tempos : [{ start: 0, bpm: data.tempo }];
  let seconds = 0;
  const marks = changes.map((change, index) => {
    if (index) seconds += (change.start - changes[index - 1].start) * 60 / (changes[index - 1].bpm * speed);
    return { ...change, seconds };
  });
  const clock = place => {
    const mark = marks.findLast(item => item.start <= place) ?? marks[0];
    return mark.seconds + (place - mark.start) * 60 / (mark.bpm * speed);
  };
  clock.place = time => {
    const mark = marks.findLast(item => item.seconds <= time) ?? marks[0];
    return mark.start + (time - mark.seconds) * mark.bpm * speed / 60;
  };
  clock.bpm = place => (marks.findLast(item => item.start <= place) ?? marks[0]).bpm;
  return clock;
}

function soundOf(data, note) {
  const program = data.programs?.[note.part] ?? 1;
  return FAMILY_SOUNDS[Math.floor((program - 1) / 8)] ?? USUAL_SOUND;
}

// The beat a bar is counted in: its time signature's lower number, or a dotted beat in 6/8, 9/8 and 12/8.
function beatOf(bar) {
  const compound = bar.beatType === 8 && bar.beats % 3 === 0 && bar.beats > 3;
  return (compound ? 3 : 1) * 4 / bar.beatType;
}

// A pickup's beats are the last ones of its bar, so they're counted from where the bar would begin.
function barBegins(bar) {
  return bar.index === 0 && bar.length < bar.full ? bar.start - (bar.full - bar.length) : bar.start;
}

// The metronome's beats from one place to another, each saying whether it starts a bar.
function beatsBetween(data, from, until) {
  const found = [];
  for (const bar of data.bars ?? []) {
    const unit = beatOf(bar), begins = barBegins(bar);
    for (let count = Math.ceil((bar.start - begins) / unit - EARLY); begins + count * unit < bar.start + bar.length - EARLY; count++) {
      const place = begins + count * unit;
      if (place >= from - EARLY && place < until - EARLY) found.push({ place, strong: count === 0 });
    }
  }
  return found;
}

// A channel's level as a gain. Muted, or left out while others are soloed, it's silent.
function gainOf(channel, soloing, soloable = true) {
  if (!channel || channel.mute || (soloing && soloable && !channel.solo) || channel.volume <= QUIETEST_DB) return channel ? 0 : 1;
  return 10 ** (channel.volume / 20);
}

// The mixer as playback reads it: each part's channel by its place in the score, not its id.
function mixerForPlayback(settings = mixerOf(score)) {
  return { parts: (score?.partIds ?? []).map(id => settings.parts[id]), chords: settings.chords, metronome: settings.metronome, master: settings.master };
}

// Each part, the chord symbols and the metronome go through a level and a pan of their own into the
// master channel, as in MuseScore's mixer. Their values can change while the music plays.
function mixerNodes(audio, mixer = {}) {
  const master = audio.createGain();
  master.connect(audio.destination);
  const strip = () => {
    const gain = audio.createGain(), panner = audio.createStereoPanner();
    gain.connect(panner).connect(master);
    return { gain, panner };
  };
  const nodes = { master, parts: [], chords: strip(), metronome: strip(), part: index => (nodes.parts[index] ??= strip()) };
  (mixer.parts ?? []).forEach((_, index) => nodes.part(index));
  setMixer(nodes, mixer);
  return nodes;
}

function setMixer(nodes, mixer, when = null) {
  const soloing = (mixer.parts ?? []).some(channel => channel?.solo) || Boolean(mixer.chords?.solo);
  const set = (parameter, value) => (when === null ? (parameter.value = value) : parameter.setTargetAtTime(value, when, 0.02));
  set(nodes.master.gain, gainOf(mixer.master, false));
  const level = (strip, channel, soloable) => {
    set(strip.gain.gain, gainOf(channel, soloing, soloable) * PAN_LAW);
    set(strip.panner.pan, (channel?.pan ?? 0) / 100);
  };
  nodes.parts.forEach((strip, index) => level(strip, mixer.parts?.[index], true));
  level(nodes.chords, mixer.chords, true);
  level(nodes.metronome, mixer.metronome, false);
}

// The mixer moved: what's playing follows it at once.
function applyMixer(settings) {
  if (player) setMixer(player.nodes, mixerForPlayback(settings), player.audio.currentTime);
}

// Works with speakers or with an offline context, which is how the tests check what would be heard.
// Nothing is played from `until` on, which is where a loop goes back to its start.
function schedule(audio, data, from, zero, speed = 1, until = Infinity, options = {}) {
  const clock = clockFor(data, speed);
  const at = place => zero + clock(place) - clock(from);
  const nodes = options.nodes ?? mixerNodes(audio, options.mixer);
  const notes = data.notes.filter(note => note.start >= from && note.start < until && (!options.only || options.only(note)));
  for (const note of notes) {
    const { wave, level } = soundOf(data, note);
    const ends = Math.min(note.start + note.length, until);
    tone(audio, note.midi, at(note.start), at(ends) - at(note.start), 0.4 * note.volume ** 2 * level, wave, nodes.part(note.part ?? 0).gain);
  }
  const chords = options.chords === false ? [] : data.chords.filter(chord => chord.start + chord.length > from && chord.start < until);
  for (const chord of chords) {
    const begins = Math.max(chord.start, from);
    const ends = Math.min(chord.start + chord.length, until);
    for (const midi of chord.midi) tone(audio, midi, at(begins), at(ends) - at(begins), 0.045, "sine", nodes.chords.gain);
  }
  const beats = options.metronome ? beatsBetween(data, from, Math.min(until, data.total)) : [];
  for (const beat of beats) tick(audio, at(beat.place), beat.strong, nodes.metronome.gain);
  return { notes, chords, beats, at, nodes };
}

// A plain 16-bit WAV, which any program can open: one channel, or the left and right of a stereo one.
function wavFile(samples, rate) {
  const channels = ArrayBuffer.isView(samples) ? [samples] : samples;
  const frames = channels[0].length, size = frames * channels.length * 2;
  const peak = Math.max(1, ...channels.map(channel => channel.reduce((top, sample) => Math.max(top, Math.abs(sample)), 0)));
  const file = new DataView(new ArrayBuffer(44 + size));
  const write = (at, word) => [...word].forEach((letter, index) => file.setUint8(at + index, letter.charCodeAt(0)));
  write(0, "RIFF");
  file.setUint32(4, 36 + size, true);
  write(8, "WAVEfmt ");
  file.setUint32(16, 16, true);
  file.setUint16(20, 1, true); // Plain samples, not compressed.
  file.setUint16(22, channels.length, true);
  file.setUint32(24, rate, true);
  file.setUint32(28, rate * 2 * channels.length, true);
  file.setUint16(32, 2 * channels.length, true);
  file.setUint16(34, 16, true);
  write(36, "data");
  file.setUint32(40, size, true);
  for (let frame = 0; frame < frames; frame++) {
    channels.forEach((channel, number) => file.setInt16(44 + (frame * channels.length + number) * 2, channel[frame] / peak * 0x7fff, true));
  }
  return new Blob([file], { type: "audio/wav" });
}

async function fetchPlayback() {
  const response = await fetch(`/api/scores/${scoreId}/playback`);
  if (!response.ok) return null;
  playState.data = await response.json();
  return playState.data;
}

// Made by the same code as Play, so the file sounds just as the score does, only faster than real
// time. As in MuseScore, it's mixed as the mixer is set, in stereo, and the metronome is in it when
// it's switched on.
function saveAudio() {
  return enqueue(async () => {
    const data = await fetchPlayback();
    const seconds = clockFor(data, 1)(data.total) + AUDIO_TAIL_SECONDS;
    const audio = new OfflineAudioContext(2, Math.ceil(seconds * AUDIO_RATE), AUDIO_RATE);
    schedule(audio, data, 0, 0, 1, Infinity, { metronome: playState.metronome, chords: playState.chords, mixer: mixerForPlayback() });
    const rendered = await audio.startRendering();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(wavFile([rendered.getChannelData(0), rendered.getChannelData(1)], AUDIO_RATE));
    link.download = "score.wav";
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  });
}

// Where a note or rest is first played, in quarter notes, and how long it lasts. After a given
// place, it's the first time it's played from there.
function placeOf(data, id, after = -Infinity) {
  const found = data.events?.[id];
  if (!found) return null;
  const [index, onset, length] = found;
  const bar = data.bars.find(item => item.index === index && item.start + onset >= after - EARLY) ?? data.bars.find(item => item.index === index);
  return bar ? { start: bar.start + onset, end: bar.start + onset + length } : null;
}

// The stretch a loop goes round: the selection it was set on, or the whole score.
function loopWindow(data) {
  const whole = { start: 0, end: data.total };
  const { first, last } = playState.loop ?? {};
  if (!first || !last) return whole;
  const opening = placeOf(data, first), closing = placeOf(data, last);
  if (!opening || !closing) return whole;
  const start = Math.min(opening.start, closing.start);
  const end = Math.max(placeOf(data, first, start).end, placeOf(data, last, start).end);
  return end > start ? { start, end } : whole;
}

function clearPlaying() {
  document.querySelectorAll("#pages .playing").forEach(element => element.classList.remove("playing"));
}

function showPlaying(on) {
  const button = $("play");
  button.setAttribute("aria-label", on ? "Stop" : "Play");
  button.setAttribute("aria-pressed", String(on));
  button.innerHTML = icon(on ? "stop" : "play");
}

// Where playback has got to, as a place in the music.
function playingPlace() {
  const now = player.audio.currentTime;
  const cycle = player.cycles.findLast(item => item.zero <= now) ?? player.cycles[0];
  const seconds = cycle.clock(cycle.from) + Math.max(0, now - cycle.zero);
  return Math.min(cycle.clock.place(seconds), cycle.until);
}

// One time through: to the end of the score, or round the loop, which then goes round again.
// A note starts: it's shown on the page, which follows it, and on the instrument if it's that part's.
function showSounding(data, note, at, until) {
  clearPlaying();
  const element = document.getElementById(note.id);
  element?.classList.add("playing");
  followNote(element);
  const timers = player?.timers ?? practice.follower?.timers;
  if (timers && instrumentShown() && (note.part ?? 0) === activePart()) timers.push(soundKey(note.midi, at(Math.min(note.start + note.length, until)) - at(note.start), note.id));
}

// Each time round a loop can go a little faster (see nextSpeed), shown as that time round begins.
function playCycle(from, until, zero) {
  const { audio, data } = player;
  const speed = player.cycles.length ? nextSpeed(player.speed) : player.speed;
  player.speed = speed;
  const { notes, chords, at } = schedule(audio, data, from, zero, speed, until, { metronome: playState.metronome, chords: playState.chords, nodes: player.nodes, only: hearing() });
  player.cycles.push({ from, until, zero, clock: clockFor(data, speed) });
  if (player.cycles.length > 1) player.timers.push(setTimeout(() => showSpeed(speed), Math.max(0, (zero - audio.currentTime) * 1000)));
  for (const note of notes) {
    player.timers.push(setTimeout(() => showSounding(data, note, at, until), (at(note.start) - audio.currentTime) * 1000));
  }
  if (player.cycles.length === 1) {
    $("play").dataset.notes = notes.length;
    $("play").dataset.chords = chords.length;
  }
  const ends = at(until);
  if (player.loop) {
    const window = player.loop;
    player.timers.push(setTimeout(() => { if (player) playCycle(window.start, window.end, ends); }, Math.max(0, (zero - audio.currentTime) * 1000)));
  } else {
    player.timers.push(setTimeout(() => stopPlaying(true), (ends - audio.currentTime + 0.3) * 1000));
  }
}

function followPlaying() {
  if (!player) return;
  drawPosition(playingPlace());
  player.frame = requestAnimationFrame(followPlaying);
}

// The playback position follows whatever was done last: selecting a note or rest puts it there, and
// so does typing in a counter, rewinding or stopping. It's where Play starts, as in MuseScore.
function playFromNote(id) {
  playState.chosen = id;
  const found = id && playState.data && placeOf(playState.data, id);
  if (found && !player) drawPosition(found.start);
}

// Play from a place given, or from the playback position. Started again where it was playing, as a
// new speed or a part to hear does, it's not counted in again.
async function startPlaying(place = null, counted = true) {
  const starting = playState.starting = {};
  const data = await fetchPlayback();
  if (playState.starting !== starting) return; // Stopped, or started again, while its notes came.
  playState.starting = null;
  if (!data) { message("This sheet couldn't be played."); return; }
  // While entering notes it's where the next note goes, as it always was.
  const fromNote = editor.on ? plainId(editor.cursor) : playState.chosen;
  const chosen = place === null && fromNote ? placeOf(data, fromNote) : null;
  if (chosen) playState.place = chosen.start;
  playState.chosen = null;
  let from = place ?? (playState.place < data.total - EARLY ? playState.place : 0);
  const loop = playState.looping ? loopWindow(data) : null;
  if (loop && (from < loop.start - EARLY || from >= loop.end - EARLY)) from = loop.start;
  const audio = new (window.AudioContext || window.webkitAudioContext)();
  const nodes = mixerNodes(audio, mixerForPlayback());
  player = { audio, data, nodes, speed: playState.speed, clock: clockFor(data, playState.speed), timers: [], cycles: [], loop };
  // In Practice a bar or two can be counted in first.
  const lead = counted ? countIn(audio, data, from, audio.currentTime + START_DELAY, nodes) : 0;
  playCycle(from, loop ? loop.end : data.total, audio.currentTime + START_DELAY + lead);
  showPlaying(true);
  followPlaying();
}

// Stopping keeps the place, so Play carries on from there. Coming to the end goes back to the start.
function stopPlaying(finished = false) {
  if (practice.follower) { stopFollowing(finished); return; }
  if (!player) return;
  playState.place = finished ? 0 : playingPlace();
  player.timers.forEach(clearTimeout);
  cancelAnimationFrame(player.frame);
  player.audio.close();
  player = null;
  clearPlaying();
  quietKeys();
  showPlaying(false);
  showWait("");
  speedBack();
  drawPosition(playState.place);
}

// In Practice, with someone's notes to wait for, playback waits for you to play them.
function togglePlaying() {
  if (player || practice.follower) return stopPlaying();
  if (playState.starting) { playState.starting = null; return; }
  return practising() && practice.waitFor !== "off" ? startFollowing() : startPlaying();
}

// Go to a place: at once if playing, or ready for the next Play.
function playFrom(place) {
  playState.chosen = null;
  if (practice.follower) {
    stopPlaying();
    startFollowing(place, false);
    return;
  }
  if (!player) {
    playState.place = place;
    drawPosition(place);
    return;
  }
  stopPlaying();
  startPlaying(place, false);
}

function rewind() {
  const data = playState.data;
  playFrom(playState.looping && data ? loopWindow(data).start : 0);
}

function toggleLoop() {
  playState.looping = !playState.looping;
  if (playState.looping) {
    const first = range?.first ?? actionTarget(), last = range?.last ?? first;
    if (first || !playState.loop) playState.loop = { first: first ?? null, last: last ?? null };
  }
  drawPracticeState();
  drawLoopMarkers();
  if (player) playFrom(playingPlace());
}

// The settings popup can set either end of the loop at the selected note, as MuseScore's can.
function setLoopEnd(side) {
  const id = range ? (side === "first" ? range.first : range.last) : actionTarget();
  if (!id) { message("Select a note or rest for the loop to start or end at."); return; }
  playState.loop = { first: playState.loop?.first ?? id, last: playState.loop?.last ?? id, [side]: id };
  playState.looping = true;
  drawPracticeState();
  drawLoopMarkers();
}

function toggleMetronome() {
  playState.metronome = !playState.metronome;
  keepSetting("metronome", playState.metronome ? "on" : "off");
  drawPracticeState();
  if (player) playFrom(playingPlace());
}

function setSpeed(percent) {
  const value = Math.min(FASTEST_SPEED, Math.max(SLOWEST_SPEED, Math.round(Number(percent) || 100)));
  practice.startSpeed = null; // A speed chosen is where a stepped-up loop starts from now.
  playState.speed = value / 100;
  $("speed").value = value;
  $("speedValue").value = value;
  drawPracticeState();
  drawPosition(player ? playingPlace() : playState.place);
  if (player) playFrom(playingPlace());
}

function twoDigits(number) {
  return String(number).padStart(2, "0");
}

// The time as MuseScore shows it, hours, minutes, seconds and tenths, and back.
function timeText(seconds) {
  const tenths = Math.floor(seconds * 10 + EARLY);
  return `${twoDigits(Math.floor(tenths / 36000))}:${twoDigits(Math.floor(tenths / 600) % 60)}:${twoDigits(Math.floor(tenths / 10) % 60)}.${tenths % 10}`;
}

function secondsIn(text) {
  const parts = text.trim().split(":").map(Number);
  if (!parts.length || parts.some(part => !Number.isFinite(part) || part < 0)) return null;
  return parts.reduce((total, part) => total * 60 + part, 0);
}

// The measure and beat of a place, counting measures from 1 as the bar checks do.
function beatAt(data, place) {
  const bar = data.bars.findLast(item => item.start <= place + EARLY) ?? data.bars[0];
  if (!bar) return { measure: 1, beat: 1 };
  return { measure: bar.index + 1, beat: Math.floor((place - barBegins(bar)) / beatOf(bar) + EARLY) + 1 };
}

function placeAtBeat(data, measure, beat) {
  const bar = data.bars.find(item => item.index === measure - 1);
  if (!bar) return null;
  return Math.min(Math.max(barBegins(bar) + (beat - 1) * beatOf(bar), bar.start), bar.start + bar.length - EARLY);
}

function drawPosition(place) {
  const data = playState.data;
  if (!data) return;
  const clock = clockFor(data, playState.speed);
  if (document.activeElement !== $("timeCounter")) $("timeCounter").value = timeText(clock(place));
  const { measure, beat } = beatAt(data, place);
  if (document.activeElement !== $("beatCounter")) $("beatCounter").value = `${measure}.${beat}`;
  $("tempoValue").textContent = String(Math.round(clock.bpm(place) * playState.speed));
}

// The playback position and tempo are kept up to date as the score is edited, but not on every keystroke.
function refreshPosition() {
  if (!scoreId) return;
  clearTimeout(playState.wanted);
  playState.wanted = setTimeout(async () => {
    if (!scoreId || player || practice.follower) return;
    const data = await fetchPlayback();
    if (data && !player && !practice.follower) {
      playState.place = Math.min(playState.place, data.total);
      drawPosition(playState.place);
      refreshKinds();
      lightKeys();
      // A transposing instrument's sound is only known now, and the inspector's strings follow it.
      if (actionTarget()) drawInspector();
    }
  }, 400);
}

// Flags where the loop starts and ends, at the notes it was set on, as MuseScore draws them.
function drawLoopMarkers() {
  document.querySelectorAll(".loopMarker").forEach(marker => marker.remove());
  if (!playState.looping || !playState.loop?.first) return;
  const frame = $("pagesFrame").getBoundingClientRect();
  for (const [side, id] of [["in", playState.loop.first], ["out", playState.loop.last]]) {
    const element = document.getElementById(id);
    const staff = element?.closest("g.system")?.querySelector("g.staff");
    if (!element || !staff) continue;
    const box = headBox(element), lines = staffLines(element.closest("g.staff") ?? staff);
    const marker = document.createElement("div");
    marker.className = `loopMarker ${side}`;
    marker.style.left = `${(side === "in" ? box.left - 4 : box.right + 4) - frame.left}px`;
    marker.style.top = `${lines.top - frame.top - lines.step * 6}px`;
    marker.style.height = `${lines.bottom - lines.top + lines.step * 8}px`;
    $("pagesFrame").append(marker);
  }
}

// A popup that opens below the button it belongs to, and closes when anything else is pressed.
function popupFor(buttonId, popupId) {
  $(buttonId).addEventListener("click", event => {
    const popup = $(popupId);
    popup.hidden = !popup.hidden;
    const at = event.currentTarget.getBoundingClientRect();
    popup.style.top = `${at.bottom + 4}px`;
    popup.style.left = `${Math.max(4, Math.min(at.left, innerWidth - popup.offsetWidth - 4))}px`;
  });
  document.addEventListener("pointerdown", event => {
    if (!$(popupId).hidden && !event.target.closest(`#${popupId}, #${buttonId}`)) $(popupId).hidden = true;
  });
}

// Another score starts from its beginning, with no loop.
function forgetPlayback() {
  stopPlaying();
  Object.assign(playState, { place: 0, looping: false, loop: null, data: null, chosen: null });
  drawPracticeState();
  $("timeCounter").value = timeText(0);
  $("beatCounter").value = "1.1";
}

function setUpPlayback() {
  $("tempoButton").querySelector(".tempoNote").outerHTML = glyphIcon("ECA5", 3.6);
  $("play").addEventListener("click", togglePlaying);
  $("rewind").addEventListener("click", rewind);
  $("loop").addEventListener("click", toggleLoop);
  $("metronome").addEventListener("click", toggleMetronome);
  $("audio").addEventListener("click", saveAudio);
  popupFor("playSettings", "playPopup");
  popupFor("tempoButton", "speedPopup");
  $("playChords").checked = playState.chords;
  $("playChords").addEventListener("change", event => {
    playState.chords = event.target.checked;
    keepSetting("chordSymbols", playState.chords ? "on" : "off");
    if (player) playFrom(playingPlace());
  });
  $("loopLeft").addEventListener("click", () => { $("playPopup").hidden = true; setLoopEnd("first"); });
  $("loopRight").addEventListener("click", () => { $("playPopup").hidden = true; setLoopEnd("last"); });
  $("speed").addEventListener("input", event => setSpeed(event.target.value));
  $("speedValue").addEventListener("change", event => setSpeed(event.target.value));
  $("speedReset").addEventListener("click", () => setSpeed(100));
  // A counter takes a new place when it's typed in, then Enter or a click elsewhere.
  const typed = (id, read) => {
    $(id).addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === "Escape") { event.preventDefault(); event.target.blur(); }
    });
    $(id).addEventListener("change", event => {
      const data = playState.data;
      const place = data ? read(data, event.target.value) : null;
      if (place !== null && Number.isFinite(place)) playFrom(Math.min(Math.max(place, 0), data.total));
      else drawPosition(player ? playingPlace() : playState.place);
    });
  };
  typed("timeCounter", (data, text) => {
    const seconds = secondsIn(text);
    return seconds === null ? null : clockFor(data, playState.speed).place(seconds);
  });
  typed("beatCounter", (data, text) => {
    const [measure, beat = 1] = text.trim().split(/[.:,\s]+/).map(Number);
    return Number.isInteger(measure) && Number.isInteger(beat) && beat >= 1 ? placeAtBeat(data, measure, beat) : null;
  });
}

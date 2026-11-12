// Practice: learning a piece rather than writing it. A passage loops, between A and B or round the
// selection, at any speed, after a bar or two counted in. Playback can sound one part alone, or one
// hand of a piano, and it can wait for you: it stops at each of your notes until you've played them,
// on a MIDI keyboard or on the instrument below, while the rest plays along.
const practice = {
  countIn: Number(readSetting("countIn", "0")) || 0, // Bars of ticks before the music starts.
  hear: "all", // What playback sounds: everything, "part:N" or "staff:N:S".
  waitFor: "off", // Whose notes playback waits for, in the same terms.
  follower: null, // Playback while it waits for you.
  midi: "unknown",
};
const COUNT_TICK_SECONDS = 0.05;

function practising() {
  return currentMode() === "practice";
}

// The notes a choice covers: every note, a part's, or one staff of a part, counted from 1.
function notesOf(choice) {
  if (!choice || choice === "all" || choice === "off") return () => true;
  const [kind, part, staff] = choice.split(":");
  return note => (note.part ?? 0) === Number(part) && (kind !== "staff" || (note.staff ?? 1) === Number(staff));
}

// What playback sounds in Practice: everything, or what was chosen to hear, and never the notes it's
// waiting for you to play.
function hearing() {
  if (!practising()) return null;
  const heard = practice.hear === "all" ? null : notesOf(practice.hear);
  if (practice.waitFor === "off") return heard;
  const yours = notesOf(practice.waitFor);
  return note => (!heard || heard(note)) && !yours(note);
}

// Everything, each part, and a piano's two hands.
function practiceChoices() {
  const choices = [];
  (score?.parts ?? []).forEach((_, part) => {
    const name = partLabel(part);
    choices.push([`part:${part}`, (score.parts.length > 1 ? `${name} alone` : name)]);
    if (kindOfPart(part) === "piano" && (score.partStaves?.[part] ?? 1) >= 2) {
      choices.push([`staff:${part}:1`, `${name}, right hand`], [`staff:${part}:2`, `${name}, left hand`]);
    }
  });
  return choices;
}

function fillChoices(select, first, choices, value) {
  const wanted = [first, ...choices];
  const same = select.options.length === wanted.length && wanted.every(([key, label], index) => select.options[index].value === key && select.options[index].text === label);
  if (!same) select.replaceChildren(...wanted.map(([key, label]) => new Option(label, key)));
  select.value = wanted.some(([key]) => key === value) ? value : first[0];
  return select.value;
}

function drawPracticeChoices() {
  if (!score) return;
  const choices = practiceChoices();
  practice.hear = fillChoices($("practiceHear"), ["all", "Everything"], choices, practice.hear);
  practice.waitFor = fillChoices($("waitFor"), ["off", "Nobody: just play"], choices, practice.waitFor);
}

// Both loop buttons, the metronome's and the speed, wherever they're shown.
function drawPracticeState() {
  $("practiceLoop").setAttribute("aria-pressed", String(playState.looping));
  $("loop").setAttribute("aria-pressed", String(playState.looping));
  $("practiceMetronome").setAttribute("aria-pressed", String(playState.metronome));
  $("metronome").setAttribute("aria-pressed", String(playState.metronome));
  const percent = Math.round(playState.speed * 100);
  $("practiceSpeed").value = percent;
  $("practiceSpeedValue").textContent = `${percent}%`;
  $("countIn").value = String(practice.countIn);
}

function setCountIn(bars) {
  practice.countIn = Math.max(0, Math.min(2, Number(bars) || 0));
  keepSetting("countIn", String(practice.countIn));
  drawPracticeState();
}

// The bar a place is in, its beat, and the seconds one beat takes at the speed chosen.
function beatAtPlace(data, place) {
  const bar = data.bars.findLast(item => item.start <= place + EARLY) ?? data.bars[0];
  const unit = bar ? beatOf(bar) : 1;
  const beats = bar ? Math.max(1, Math.round(bar.full / unit)) : 4;
  const bpm = clockFor(data, playState.speed).bpm(place) * playState.speed;
  return { beats, seconds: unit * 60 / bpm };
}

// Count a bar or two in, ticking every beat and higher on the first, and say how long it takes.
function countIn(audio, data, from, start, nodes) {
  if (!practising() || !practice.countIn) return 0;
  const { beats, seconds } = beatAtPlace(data, from);
  const count = practice.countIn * beats;
  const timers = [];
  for (let number = 0; number < count; number++) {
    tick(audio, start + number * seconds, number % beats === 0, nodes.metronome.gain);
    timers.push(setTimeout(() => showWait(`Count in: ${(number % beats) + 1}`), (start + number * seconds - audio.currentTime) * 1000));
  }
  timers.push(setTimeout(() => showWait(""), (start + count * seconds - audio.currentTime) * 1000));
  player?.timers.push(...timers);
  practice.follower?.timers.push(...timers);
  return count * seconds + COUNT_TICK_SECONDS;
}

function showWait(text, waiting = false) {
  $("waitState").textContent = text;
  $("waitState").classList.toggle("waiting", waiting);
}

// Waiting for you: the notes you play, grouped by when they're played, from where playback starts.
async function startFollowing(place = null) {
  const data = await fetchPlayback();
  if (!data) { message("This sheet couldn't be played."); return; }
  const fromNote = playState.chosen;
  const chosen = place === null && fromNote ? placeOf(data, fromNote) : null;
  if (chosen) playState.place = chosen.start;
  playState.chosen = null;
  const loop = playState.looping ? loopWindow(data) : null;
  let from = place ?? (playState.place < data.total - EARLY ? playState.place : 0);
  if (loop && (from < loop.start - EARLY || from >= loop.end - EARLY)) from = loop.start;
  const until = loop ? loop.end : data.total;
  const yours = notesOf(practice.waitFor);
  const audio = new (window.AudioContext || window.webkitAudioContext)();
  const nodes = mixerNodes(audio, mixerForPlayback());
  practice.follower = { data, audio, nodes, loop, until, timers: [], steps: [], index: 0, pressed: new Set(), place: from, arrived: false, flowing: null, yours };
  practice.follower.steps = stepsBetween(data, yours, from, until);
  showPlaying(true);
  setUpMidi();
  const lead = countIn(audio, data, from, audio.currentTime + START_DELAY, nodes);
  practice.follower.timers.push(setTimeout(() => { if (practice.follower) waitAtStep(); }, lead * 1000));
  followFollower();
  drawDock();
}

// Your notes from one place to another, as steps: each the pitches that start together.
function stepsBetween(data, yours, from, until) {
  const steps = [];
  for (const note of data.notes) {
    if (!yours(note) || note.start < from - EARLY || note.start >= until - EARLY) continue;
    const pitch = keyOfSound(data, note.midi, note.part ?? 0);
    const last = steps.at(-1);
    if (last && Math.abs(last.start - note.start) < EARLY * 1000) { last.pitches.add(pitch); last.sounding.add(note.midi); last.ids.push(note.id); } else steps.push({ start: note.start, pitches: new Set([pitch]), sounding: new Set([note.midi]), ids: [note.id] });
  }
  return steps;
}

// Play what you don't from here to your next notes, then wait for them.
function waitAtStep() {
  const follower = practice.follower;
  const step = follower.steps[follower.index];
  keyboard.expected = new Set(step?.sounding ?? []);
  lightKeys();
  const ends = step ? step.start : follower.until;
  flow(follower.place, ends, () => {
    if (!practice.follower) return;
    if (step) { arrive(); return; }
    if (follower.loop) {
      follower.place = follower.loop.start;
      follower.steps = stepsBetween(follower.data, follower.yours, follower.loop.start, follower.loop.end);
      follower.index = 0;
      waitAtStep();
    } else {
      stopPlaying(true);
    }
  });
}

function flow(from, to, then) {
  const follower = practice.follower;
  if (to - from < EARLY) { follower.flowing = null; then(); return; }
  const zero = follower.audio.currentTime + 0.03;
  const { notes, at } = schedule(follower.audio, follower.data, from, zero, playState.speed, to, { metronome: playState.metronome, chords: playState.chords, nodes: follower.nodes, only: hearing() });
  follower.flowing = { from, to, zero, clock: clockFor(follower.data, playState.speed) };
  for (const note of notes) {
    follower.timers.push(setTimeout(() => showSounding(follower.data, note, at, to), (at(note.start) - follower.audio.currentTime) * 1000));
  }
  follower.timers.push(setTimeout(() => { if (practice.follower === follower) { follower.flowing = null; then(); } }, (at(to) - follower.audio.currentTime) * 1000));
}

function arrive() {
  const follower = practice.follower;
  const step = follower.steps[follower.index];
  follower.arrived = true;
  follower.place = step.start;
  drawPosition(step.start);
  if ([...step.pitches].every(pitch => follower.pressed.has(pitch))) { advance(); return; }
  document.querySelectorAll("#pages .awaited").forEach(element => element.classList.remove("awaited"));
  step.ids.forEach(id => document.getElementById(id)?.classList.add("awaited"));
  followNote(document.getElementById(step.ids[0]));
  showWait(`Play ${[...step.pitches].sort((a, b) => a - b).map(noteName).join(" ")}`, true);
}

function advance() {
  const follower = practice.follower;
  const step = follower.steps[follower.index];
  document.querySelectorAll("#pages .awaited").forEach(element => element.classList.remove("awaited"));
  follower.place = step.start;
  follower.index += 1;
  follower.pressed = new Set();
  follower.arrived = false;
  showWait("");
  waitAtStep();
}

// A note you played, as the staff shows it. It counts towards your next notes, and is kept if
// they're played a little early, before the music reaches them.
function practiseHeard(midi) {
  const follower = practice.follower;
  if (!follower) return false;
  const step = follower.steps[follower.index];
  if (!step) return true;
  if (step.pitches.has(midi)) {
    follower.pressed.add(midi);
    if (follower.arrived && [...step.pitches].every(pitch => follower.pressed.has(pitch))) advance();
  } else if (follower.arrived) {
    showWait(`That was ${noteName(midi)}. Play ${[...step.pitches].sort((a, b) => a - b).map(noteName).join(" ")}`, true);
  }
  return true;
}

function followerPlace() {
  const follower = practice.follower;
  if (!follower?.flowing) return follower?.place ?? 0;
  const { from, to, zero, clock } = follower.flowing;
  const seconds = clock(from) + Math.max(0, follower.audio.currentTime - zero);
  return Math.min(clock.place(seconds), to);
}

function followFollower() {
  const follower = practice.follower;
  if (!follower) return;
  drawPosition(followerPlace());
  follower.frame = requestAnimationFrame(followFollower);
}

function stopFollowing(finished = false) {
  const follower = practice.follower;
  if (!follower) return;
  practice.follower = null;
  playState.place = finished ? 0 : follower.place;
  follower.timers.forEach(clearTimeout);
  cancelAnimationFrame(follower.frame);
  follower.audio.close();
  document.querySelectorAll("#pages .awaited").forEach(element => element.classList.remove("awaited"));
  keyboard.expected = new Set();
  clearPlaying();
  quietKeys();
  showWait("");
  showPlaying(false);
  drawPosition(playState.place);
  drawDock();
}

// A MIDI keyboard's key: what you played while playback waits for you, or else a note to write.
function midiNoteOn(number) {
  if (practiseHeard(number)) { hearKey(soundingPitch(number, activePart())); return; }
  midiKey(number);
}

function drawMidiState() {
  if (practice.waitFor === "off") { showWait(""); return; }
  const words = { ready: "MIDI keyboard ready, or play the instrument below", none: "No MIDI keyboard found: play the instrument below", unknown: "Play on a MIDI keyboard or the instrument below" };
  if (!practice.follower) showWait(words[practice.midi] ?? words.unknown);
}

function setUpPractice() {
  $("practiceBar").innerHTML = `
    <div class="tool" role="group" aria-label="Loop">
      <span class="label">Loop</span>
      <button id="loopStart" title="Start the loop at the selected note (A)">A</button>
      <button id="loopEnd" title="End the loop at the selected note (B)">B</button>
      <button id="practiceLoop" class="toggle" aria-pressed="false" title="Loop between A and B, or round the selection">${icon("loop")}Loop</button>
    </div>
    <span class="divider"></span>
    <label class="tool"><span class="label">Speed</span><input id="practiceSpeed" type="range" min="25" max="150" step="5" value="100" aria-label="Practice speed"><span id="practiceSpeedValue">100%</span></label>
    <label class="tool"><span class="label">Count in</span><select id="countIn"><option value="0">Off</option><option value="1">1 bar</option><option value="2">2 bars</option></select></label>
    <button id="practiceMetronome" class="toggle" aria-pressed="false" title="Tick every beat">${icon("metronome")}Metronome</button>
    <span class="divider"></span>
    <label class="tool"><span class="label">Hear</span><select id="practiceHear" aria-label="What playback sounds"></select></label>
    <label class="tool"><span class="label">Wait for</span><select id="waitFor" aria-label="Whose notes playback waits for you to play"></select></label>
    <span id="waitState" role="status"></span>`;
  $("loopStart").addEventListener("click", () => setLoopEnd("first"));
  $("loopEnd").addEventListener("click", () => setLoopEnd("last"));
  $("practiceLoop").addEventListener("click", toggleLoop);
  $("practiceMetronome").addEventListener("click", toggleMetronome);
  $("practiceSpeed").addEventListener("input", event => setSpeed(event.target.value));
  $("countIn").addEventListener("change", event => { setCountIn(event.target.value); event.target.blur(); });
  $("practiceHear").addEventListener("change", event => {
    practice.hear = event.target.value;
    event.target.blur();
    if (player) playFrom(playingPlace());
  });
  $("waitFor").addEventListener("change", event => {
    practice.waitFor = event.target.value;
    event.target.blur();
    if (practice.waitFor !== "off") {
      setUpMidi();
      const part = Number(practice.waitFor.split(":")[1]);
      if (part !== activePart()) choosePart(part);
    }
    if (player || practice.follower) { stopPlaying(); }
    drawMidiState();
  });
  $("practiceBar").addEventListener("click", event => event.target.closest("button")?.blur());
  drawPracticeState();
}

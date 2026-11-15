// Practice: learning a piece rather than writing it. A passage loops, between A and B or round the
// selection, at any speed, after a bar or two counted in. Playback can sound one part alone, or one
// hand of a piano, and it can wait for you: it stops at each of your notes until you've played them,
// on a MIDI keyboard or on the instrument below, while the rest plays along.
const practice = {
  countIn: Number(readSetting("countIn", "0")) || 0, // Bars of ticks before the music starts.
  countWhole: readSetting("countWhole", "off") === "on", // Whole bars, then the beats up to the start.
  hear: "all", // What playback sounds: everything, "part:N" or "staff:N:S".
  waitFor: "off", // Whose notes playback waits for, in the same terms.
  follower: null, // Playback while it waits for you.
  midi: "unknown",
  stepUp: readSetting("speedSteps", "off") === "on", // Each time round the loop a little faster.
  step: Number(readSetting("speedStep", "10")) || 10, // By this many percent,
  target: Number(readSetting("speedTarget", "100")) || 100, // up to this speed.
  startSpeed: null, // The speed a stepped-up loop started at, to go back to.
  timing: null, // How your notes fell while playback waited for you: { early, late, onTime, short }.
};
const COUNT_TICK_SECONDS = 0.05;
const ON_TIME_SECONDS = 0.08; // A note played this near its moment, or an eighth of a beat, is on time.
const HELD_SHARE = 0.5; // A note let go before half its length is short.

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
  $("countIn").value = `${practice.countIn}${practice.countIn && practice.countWhole ? "w" : ""}`;
  $("speedSteps").setAttribute("aria-pressed", String(practice.stepUp));
  $("speedStep").value = String(practice.step);
  $("speedTarget").value = String(practice.target);
}

// "1" or "2" bars, or "1w" and "2w" for whole bars before the beats up to the start.
function setCountIn(choice) {
  practice.countIn = Math.max(0, Math.min(2, parseInt(choice, 10) || 0));
  practice.countWhole = String(choice).endsWith("w");
  keepSetting("countIn", String(practice.countIn));
  keepSetting("countWhole", practice.countWhole ? "on" : "off");
  drawPracticeState();
}

// The bar a place is in, its beat, how many beats into the bar the place is, and the seconds one
// beat takes at the speed chosen. A pickup's beats are the last of its bar.
function beatAtPlace(data, place) {
  const bar = data.bars.findLast(item => item.start <= place + EARLY) ?? data.bars[0];
  const unit = bar ? beatOf(bar) : 1;
  const beats = bar ? Math.max(1, Math.round(bar.full / unit)) : 4;
  const bpm = clockFor(data, playState.speed).bpm(place) * playState.speed;
  const into = bar ? Math.max(0, (place - barBegins(bar)) / unit) : 0;
  return { beats, seconds: unit * 60 / bpm, into: into < EARLY * 1000 ? 0 : into };
}

// How many beats the count-in lasts before the music, as players count it: the bars chosen, the last
// of them cut short so that the music comes in on its own beat, as "1, 2, 3" before a pickup on 4.
// With whole bars the count is whole bars, then the beats up to where the music starts.
function countBeats(beats, into, bars = practice.countIn, whole = practice.countWhole) {
  if (!bars) return 0;
  return into > 0 ? (whole ? bars : bars - 1) * beats + into : bars * beats;
}

// Count in, ticking every beat and higher on the first of each bar, and say how long it takes.
function countIn(audio, data, from, start, nodes) {
  if (!practising() || !practice.countIn) return 0;
  const { beats, seconds, into } = beatAtPlace(data, from);
  const lead = countBeats(beats, into);
  const ticks = Math.ceil(lead - EARLY * 1000);
  const timers = [];
  for (let number = 0; number < ticks; number++) {
    tick(audio, start + number * seconds, number % beats === 0, nodes.metronome.gain);
    timers.push(setTimeout(() => showWait(`Count in: ${(number % beats) + 1}`), (start + number * seconds - audio.currentTime) * 1000));
  }
  timers.push(setTimeout(() => showWait(""), (start + lead * seconds - audio.currentTime) * 1000));
  player?.timers.push(...timers);
  practice.follower?.timers.push(...timers);
  return lead * seconds + COUNT_TICK_SECONDS;
}

// A practice loop can go a little faster each time round, from the speed it started at up to the one
// chosen, and goes back to where it started when it stops.
function nextSpeed(now = playState.speed) {
  if (!practising() || !practice.stepUp) return now;
  practice.startSpeed ??= now;
  return Math.min(Math.max(practice.target, practice.startSpeed * 100), Math.round(now * 100) + practice.step) / 100;
}

function showSpeed(speed) {
  playState.speed = speed;
  $("speed").value = $("speedValue").value = Math.round(speed * 100);
  drawPracticeState();
}

function speedBack() {
  if (practice.startSpeed !== null) showSpeed(practice.startSpeed);
  practice.startSpeed = null;
}

function showWait(text, waiting = false) {
  $("waitState").textContent = text;
  $("waitState").classList.toggle("waiting", waiting);
}

// Waiting for you: the notes you play, grouped by when they're played, from where playback starts.
async function startFollowing(place = null, counted = true) {
  const starting = playState.starting = {};
  const data = await readySamples(await fetchPlayback());
  if (playState.starting !== starting) return; // Stopped, or started again, while its notes came.
  playState.starting = null;
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
  practice.follower = { data, audio, nodes, loop, until, timers: [], steps: [], index: 0, pressed: new Set(), pressedAt: new Map(), holding: new Map(), place: from, arrived: false, flowing: null, dueAt: null, yours };
  practice.follower.steps = stepsBetween(data, yours, from, until);
  practice.timing = { early: 0, late: 0, onTime: 0, short: 0 };
  document.querySelectorAll("#pages .early, #pages .late, #pages .onTime, #pages .short").forEach(element => element.classList.remove("early", "late", "onTime", "short"));
  drawTiming("");
  showPlaying(true);
  setUpMidi();
  const lead = counted ? countIn(audio, data, from, audio.currentTime + START_DELAY, nodes) : 0;
  const follower = practice.follower;
  follower.timers.push(setTimeout(() => { if (practice.follower === follower) waitAtStep(); }, lead * 1000));
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
    const entry = { id: note.id, midi: note.midi, part: note.part ?? 0 };
    if (last && Math.abs(last.start - note.start) < EARLY * 1000) { last.pitches.add(pitch); last.sounding.add(note.midi); last.ids.push(note.id); last.notes.push(entry); } else steps.push({ start: note.start, pitches: new Set([pitch]), sounding: new Set([note.midi]), ids: [note.id], notes: [entry] });
  }
  return steps;
}

// Play what you don't from here to your next notes, then wait for them.
function waitAtStep() {
  const follower = practice.follower;
  const step = follower.steps[follower.index];
  keyboard.expected = new Set(step?.sounding ?? []);
  keyboard.expectedNotes = step?.notes ?? [];
  lightKeys();
  const ends = step ? step.start : follower.until;
  flow(follower.place, ends, () => {
    if (!practice.follower) return;
    if (step) { arrive(); return; }
    if (follower.loop) {
      follower.place = follower.loop.start;
      follower.steps = stepsBetween(follower.data, follower.yours, follower.loop.start, follower.loop.end);
      follower.index = 0;
      showSpeed(nextSpeed());
      waitAtStep();
    } else {
      stopPlaying(true);
    }
  });
}

function flow(from, to, then) {
  const follower = practice.follower;
  if (to - from < EARLY) { follower.flowing = null; follower.dueAt = follower.audio.currentTime; then(); return; }
  const zero = follower.audio.currentTime + 0.03;
  follower.dueAt = zero + clockFor(follower.data, playState.speed)(to) - clockFor(follower.data, playState.speed)(from);
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
  if ([...step.pitches].every(pitch => follower.pressed.has(pitch))) { played(step); advance(); return; }
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
  follower.pressedAt = new Map();
  follower.arrived = false;
  showWait("");
  waitAtStep();
}

// How a step's notes fell: early or late against the moment the music reached them, from the last of
// them pressed, as a chord is played when it's all down. Each note is marked on the page, and the
// counts are kept beside what playback waits for.
function played(step) {
  const follower = practice.follower;
  const pressed = [...step.pitches].map(pitch => follower.pressedAt.get(pitch) ?? follower.audio.currentTime);
  const off = Math.max(...pressed) - (follower.dueAt ?? follower.audio.currentTime);
  const near = Math.max(ON_TIME_SECONDS, beatAtPlace(follower.data, step.start).seconds / 8);
  const kind = Math.abs(off) <= near ? "onTime" : off < 0 ? "early" : "late";
  practice.timing[kind] += step.ids.length;
  step.ids.forEach(id => document.getElementById(id)?.classList.add(kind));
  // Each note should be held for half its length at least, from when it was played.
  const clock = clockFor(follower.data, playState.speed);
  for (const note of step.notes) {
    const length = follower.data.notes.find(item => item.id === note.id && Math.abs(item.start - step.start) < EARLY * 1000)?.length ?? 0;
    const pitch = keyOfSound(follower.data, note.midi, follower.data.notes.find(item => item.id === note.id)?.part ?? 0);
    const from = follower.pressedAt.get(pitch) ?? follower.audio.currentTime;
    follower.holding.set(pitch, { id: note.id, until: from + (clock(step.start + length) - clock(step.start)) * HELD_SHARE });
  }
  const said = kind === "onTime" ? "On time" : `${kind === "early" ? "Early" : "Late"} by ${Math.abs(off).toFixed(2)} s`;
  drawTiming(said);
}

function drawTiming(last) {
  const timing = practice.timing;
  const counted = timing ? timing.onTime + timing.early + timing.late : 0;
  $("timingState").hidden = !counted;
  if (!counted) return;
  const parts = [`${timing.onTime} on time`, ...(timing.early ? [`${timing.early} early`] : []), ...(timing.late ? [`${timing.late} late`] : []), ...(timing.short ? [`${timing.short} let go early`] : [])];
  $("timingState").textContent = `${last ? `${last} · ` : ""}${parts.join(", ")}`;
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
    if (!follower.pressedAt.has(midi)) follower.pressedAt.set(midi, follower.audio.currentTime);
    if (follower.arrived && [...step.pitches].every(pitch => follower.pressed.has(pitch))) { played(step); advance(); }
  } else if (follower.arrived) {
    showWait(`That was ${noteName(midi)}. Play ${[...step.pitches].sort((a, b) => a - b).map(noteName).join(" ")}`, true);
  }
  return true;
}

// A key let go: a note you were to hold is short if it's let go before half its length.
function practiseReleased(midi) {
  const follower = practice.follower;
  const held = follower?.holding.get(midi);
  if (!held) return Boolean(follower);
  follower.holding.delete(midi);
  if (follower.audio.currentTime < held.until) {
    practice.timing.short += 1;
    document.getElementById(held.id)?.classList.add("short");
    drawTiming("Let go early");
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
  const place = followerPlace();
  drawPosition(place);
  drawPlayhead(place);
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
  hidePlayhead();
  document.querySelectorAll("#pages .awaited").forEach(element => element.classList.remove("awaited"));
  keyboard.expected = new Set();
  keyboard.expectedNotes = [];
  clearPlaying();
  quietKeys();
  showWait("");
  showPlaying(false);
  speedBack();
  drawPosition(playState.place);
  drawDock();
}

// A MIDI keyboard's key: what you played while playback waits for you, or else a note to write. It's
// heard at once, whatever it does, until it's let go: while entering notes too, and with nothing being
// written, so the keyboard can simply be played.
function midiNoteOn(number, velocity = 100) {
  holdKey(`midi:${number}`, soundingPitch(number, activePart()), activePart(), midiVolume(velocity));
  if (practiseHeard(number)) return;
  midiKey(number);
}

function midiNoteOff(number) {
  letGo(`midi:${number}`);
  practiseReleased(number);
}

// What playback sounds, from the bar or the command palette: what plays changes at once.
function setHear(choice) {
  practice.hear = choice;
  drawPracticeChoices();
  if (player) playFrom(playingPlace());
}

// Whose notes playback waits for: their instrument is chosen, and playing stops to start again so.
function setWaitFor(choice) {
  practice.waitFor = choice;
  drawPracticeChoices();
  if (practice.waitFor !== "off") {
    setUpMidi();
    const part = Number(practice.waitFor.split(":")[1]);
    if (part !== activePart()) choosePart(part);
  }
  if (player || practice.follower) stopPlaying();
  drawMidiState();
}

// The page drawn again while playback waits for you, as undo or concert pitch does: the notes waited
// for are marked again, and the keys asked for are the ones the staves now show.
function refreshFollower() {
  const follower = practice.follower;
  if (!follower) return;
  for (const step of follower.steps) step.pitches = new Set(step.notes.map(note => keyOfSound(follower.data, note.midi, note.part)));
  const step = follower.steps[follower.index];
  if (!follower.arrived || !step) return;
  step.ids.forEach(id => document.getElementById(id)?.classList.add("awaited"));
  showWait(`Play ${[...step.pitches].sort((a, b) => a - b).map(noteName).join(" ")}`, true);
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
    <label class="tool" title="Before a pickup or a start partway through a bar, the last bar counted is cut short, so the music comes in on its own beat"><span class="label">Count in</span><select id="countIn"><option value="0">Off</option><option value="1">1 bar</option><option value="2">2 bars</option><option value="1w" title="A whole bar, then the beats up to the start">1 bar + beats</option><option value="2w" title="Two whole bars, then the beats up to the start">2 bars + beats</option></select></label>
    <div class="tool" role="group" aria-label="Speed up each time round the loop">
      <button id="speedSteps" class="toggle" aria-pressed="false" title="Each time round the loop, play a little faster, up to the speed chosen. Stopped, it goes back to where it started">Step up</button>
      <select id="speedStep" aria-label="By how much each time round">${[5, 10, 20].map(step => `<option value="${step}">+${step}%</option>`).join("")}</select>
      <select id="speedTarget" aria-label="Up to">${[80, 90, 100, 110, 120, 150].map(speed => `<option value="${speed}">to ${speed}%</option>`).join("")}</select>
    </div>
    <button id="practiceMetronome" class="toggle" aria-pressed="false" title="Tick every beat">${icon("metronome")}Metronome</button>
    <span class="divider"></span>
    <label class="tool"><span class="label">Hear</span><select id="practiceHear" aria-label="What playback sounds"></select></label>
    <label class="tool"><span class="label">Wait for</span><select id="waitFor" aria-label="Whose notes playback waits for you to play"></select></label>
    <span id="waitState" role="status"></span>
    <span id="timingState" role="status" hidden title="How your notes fell against the music while it waited for you"></span>`;
  $("loopStart").addEventListener("click", () => setLoopEnd("first"));
  $("loopEnd").addEventListener("click", () => setLoopEnd("last"));
  $("practiceLoop").addEventListener("click", toggleLoop);
  $("practiceMetronome").addEventListener("click", toggleMetronome);
  $("practiceSpeed").addEventListener("input", event => setSpeed(event.target.value));
  $("countIn").addEventListener("change", event => { setCountIn(event.target.value); event.target.blur(); });
  $("speedSteps").addEventListener("click", () => { practice.stepUp = !practice.stepUp; keepSetting("speedSteps", practice.stepUp ? "on" : "off"); drawPracticeState(); });
  $("speedStep").addEventListener("change", event => { practice.step = Number(event.target.value); keepSetting("speedStep", event.target.value); event.target.blur(); });
  $("speedTarget").addEventListener("change", event => { practice.target = Number(event.target.value); keepSetting("speedTarget", event.target.value); event.target.blur(); });
  $("practiceHear").addEventListener("change", event => { event.target.blur(); setHear(event.target.value); });
  $("waitFor").addEventListener("change", event => { event.target.blur(); setWaitFor(event.target.value); });
  $("practiceBar").addEventListener("click", event => event.target.closest("button")?.blur());
  drawPracticeState();
}

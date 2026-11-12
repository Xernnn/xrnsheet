// The panels docked below the score, as MuseScore docks them, each one a tab when more than one is
// open: the mixer (F10), the piano keyboard (P) and the timeline (F12).
const DOCK_PANELS = { mixer: { label: "Mixer", keys: "F10" }, piano: { label: "Piano keyboard", keys: "P" }, timeline: { label: "Timeline", keys: "F12" } };
// The timeline's first rows, in MuseScore's order, each saying where something changes.
const TIMELINE_ROWS = [["tempo", "Tempo"], ["time", "Time signature"], ["rehearsal", "Rehearsal mark"], ["key", "Key signature"], ["barline", "Barlines"], ["jump", "Jumps and markers"]];
const LOWEST_KEY = 21, HIGHEST_KEY = 108; // A piano's 88 keys, A0 to C8.
const BLACK_KEYS = new Set([1, 3, 6, 8, 10]);
const NARROWEST_KEY = 10, WIDEST_KEY = 44; // A white key's width in pixels, which Ctrl and the wheel change.
const PREVIEW_SECONDS = 0.4;
const QUIETEST_DB = -60, LOUDEST_DB = 12;
const MIXER_SAVE_MS = 300;
const dock = { open: [], active: null };
let mixerSaving = 0;
let mixerWaiting = null; // Settings on their way to the server, which an edit's reply mustn't undo.

function dockOpen(name) {
  return dock.open.includes(name);
}

function drawDock() {
  const shown = dock.open.length > 0 && Boolean(score);
  $("dock").hidden = !shown;
  $("dockTabs").replaceChildren(...dock.open.map(name => {
    const tab = document.createElement("button");
    tab.setAttribute("role", "tab");
    tab.dataset.dock = name;
    tab.textContent = DOCK_PANELS[name].label;
    tab.setAttribute("aria-selected", String(name === dock.active));
    tab.addEventListener("click", () => { dock.active = name; drawDock(); });
    return tab;
  }));
  for (const name of Object.keys(DOCK_PANELS)) $(`${name}Panel`).hidden = !shown || name !== dock.active;
  if (shown && dock.active === "mixer") drawMixer();
  if (shown && dock.active === "piano") drawKeyboard();
  if (shown && dock.active === "timeline") drawTimeline();
  drawCursor();
}

// F10 and the View menu show a panel, or put it away if it's the one showing, as F9 does the palettes.
function toggleDock(name) {
  if (dockOpen(name) && dock.active === name) {
    dock.open = dock.open.filter(open => open !== name);
    dock.active = dock.open.at(-1) ?? null;
  } else {
    if (!dockOpen(name)) dock.open.push(name);
    dock.active = name;
  }
  drawDock();
}

// The mixer, as kept with the score. A score that hasn't been mixed has every channel at its usual level.
function mixerOf(data = score) {
  const usual = () => ({ volume: 0, pan: 0, mute: false, solo: false });
  const mixer = data?.mixer ?? {};
  const parts = {};
  for (const id of data?.partIds ?? []) parts[id] = { ...usual(), ...(mixer.parts?.[id] ?? {}) };
  return { parts, chords: { ...usual(), ...mixer.chords }, metronome: { ...usual(), ...mixer.metronome }, master: { ...usual(), ...mixer.master } };
}

function decibelText(volume) {
  return volume <= QUIETEST_DB ? "−∞ dB" : `${volume > 0 ? "+" : ""}${volume.toFixed(1)} dB`;
}

function panText(pan) {
  return pan === 0 ? "C" : `${pan < 0 ? "L" : "R"} ${Math.abs(pan)}`;
}

// One strip of the mixer: pan above, the fader, mute and solo, and the channel's name at the foot.
function mixerStrip(key, name, settings, { solo = true, pan = true } = {}) {
  const strip = document.createElement("div");
  strip.className = "strip";
  strip.dataset.channel = key;
  strip.innerHTML = `
    <label class="pan" ${pan ? "" : 'style="visibility: hidden"'}><input type="range" min="-100" max="100" step="1"><output></output></label>
    <div class="fader"><input type="range" min="${QUIETEST_DB}" max="${LOUDEST_DB}" step="0.5"><output></output></div>
    <div class="switches"><button class="flat mute" aria-pressed="false" title="Mute">M</button>${solo ? `<button class="flat solo" aria-pressed="false" title="Solo">S</button>` : ""}</div>
    <div class="name"></div>`;
  const [panInput, faderInput] = strip.querySelectorAll("input");
  strip.querySelector(".name").textContent = name;
  strip.title = name;
  panInput.setAttribute("aria-label", `Pan, ${name}`);
  faderInput.setAttribute("aria-label", `Volume, ${name}`);
  const show = () => {
    panInput.value = settings.pan;
    strip.querySelector(".pan output").textContent = panText(settings.pan);
    faderInput.value = settings.volume;
    strip.querySelector(".fader output").textContent = decibelText(settings.volume);
    strip.querySelector(".mute").setAttribute("aria-pressed", String(settings.mute));
    strip.querySelector(".solo")?.setAttribute("aria-pressed", String(settings.solo));
  };
  show();
  const change = values => { Object.assign(settings, values); show(); mixerChanged(); };
  panInput.addEventListener("input", () => change({ pan: Number(panInput.value) }));
  panInput.addEventListener("dblclick", () => change({ pan: 0 }));
  faderInput.addEventListener("input", () => change({ volume: Number(faderInput.value) }));
  faderInput.addEventListener("dblclick", () => change({ volume: 0 }));
  strip.querySelector(".mute").addEventListener("click", () => change({ mute: !settings.mute }));
  strip.querySelector(".solo")?.addEventListener("click", () => change({ solo: !settings.solo }));
  return strip;
}

let mixing = null; // The settings the strips are showing, which playback follows as they move.

function drawMixer() {
  if (!score) return;
  mixing = mixerOf(score);
  const strips = (score.partIds ?? []).map((id, index) => mixerStrip(`part:${id}`, score.parts?.[index] || `Instrument ${index + 1}`, mixing.parts[id]));
  strips.push(mixerStrip("chords", "Chord symbols", mixing.chords));
  strips.push(mixerStrip("metronome", "Metronome", mixing.metronome, { solo: false }));
  strips.push(mixerStrip("master", "Master", mixing.master, { solo: false, pan: false }));
  strips.at(-1).classList.add("master");
  $("mixerStrips").replaceChildren(...strips);
}

// What's playing hears a change at once, and the score keeps it a moment later, as a fader moves.
function mixerChanged() {
  score.mixer = structuredClone(mixing);
  applyMixer(mixing);
  clearTimeout(mixerSaving);
  const id = scoreId, settings = structuredClone(mixing);
  const waiting = { id, settings };
  mixerWaiting = waiting;
  mixerSaving = setTimeout(async () => {
    const response = await fetch(`/api/scores/${id}/mixer`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) });
    if (mixerWaiting === waiting) mixerWaiting = null;
    if (!response.ok) message("The mixer couldn't be saved.");
  }, MIXER_SAVE_MS);
}

// An edit's reply carries the mixer the server had, which may be behind a fader still being saved.
function keepMixer(data) {
  if (mixerWaiting?.id === scoreId) data.mixer = structuredClone(mixerWaiting.settings);
}

// The piano keyboard shows the selected notes, and the notes sounding while the score plays. Its keys
// are the pitches the staves show, which at concert pitch is how they sound, so a key clicked in note
// input is the note it lights for, as a MIDI keyboard's key is.
const keyboard = { width: Number(readSetting("keyWidth", "22")) || 22, sounding: new Map() };
let keyPreview = null;

function noteName(midi) {
  return `${["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"][midi % 12]}${Math.floor(midi / 12) - 1}`;
}

// The key a written note is shown on: as written, or where it sounds at concert pitch.
function shownKey(data, written, part) {
  return written + (score?.concertPitch ? data.transpositions?.[part] ?? 0 : 0);
}

// And the key a sounding note is shown on, for playback.
function keyOfSound(data, midi, part) {
  return midi - (score?.concertPitch ? 0 : data.transpositions?.[part] ?? 0);
}

function drawKeyboard() {
  const keys = $("pianoKeys");
  keys.style.setProperty("--key", `${keyboard.width}px`);
  if (!keys.childElementCount) {
    let whites = 0;
    for (let midi = LOWEST_KEY; midi <= HIGHEST_KEY; midi++) {
      const key = document.createElement("button");
      const black = BLACK_KEYS.has(midi % 12);
      key.className = `key ${black ? "black" : "white"}`;
      key.dataset.midi = midi;
      key.tabIndex = -1;
      key.setAttribute("aria-label", noteName(midi));
      key.title = noteName(midi);
      if (black) key.style.setProperty("--at", String(whites));
      else key.style.setProperty("--at", String(whites++));
      if (midi % 12 === 0) key.innerHTML = `<span>C${midi / 12 - 1}</span>`;
      key.addEventListener("pointerdown", event => { event.preventDefault(); pressKey(midi, event.shiftKey); });
      keys.append(key);
    }
    keys.style.setProperty("--whites", String(whites));
    requestAnimationFrame(() => {
      const middle = keys.querySelector('[data-midi="60"]');
      $("pianoPanel").scrollLeft = middle.offsetLeft - $("pianoPanel").clientWidth / 2;
    });
  }
  lightKeys();
}

// Light the keys of the selected notes, and of the notes playing.
function lightKeys() {
  if (!dockOpen("piano")) return;
  const data = playState.data;
  const chosen = new Set();
  const ids = range ? range.ids : [actionTarget()].filter(Boolean);
  for (const id of ids) {
    const found = data?.events?.[id];
    if (found && found[3] !== null) chosen.add(shownKey(data, found[3], found[4]));
  }
  document.querySelectorAll("#pianoKeys .key").forEach(key => {
    const midi = Number(key.dataset.midi);
    key.classList.toggle("chosen", chosen.has(midi));
    key.classList.toggle("sounding", (keyboard.sounding.get(midi) ?? 0) > 0);
  });
}

// Playback lights a key for as long as its note sounds.
function soundKey(midi, seconds) {
  keyboard.sounding.set(midi, (keyboard.sounding.get(midi) ?? 0) + 1);
  lightKeys();
  return setTimeout(() => {
    keyboard.sounding.set(midi, Math.max(0, (keyboard.sounding.get(midi) ?? 1) - 1));
    lightKeys();
  }, Math.max(seconds, 0.05) * 1000);
}

function quietKeys() {
  keyboard.sounding.clear();
  lightKeys();
}

// A key pressed writes its note while entering notes, or adds it to the chord with Shift, as a MIDI
// keyboard does. Otherwise it's only heard.
function pressKey(midi, addToChord) {
  hearKey(midi);
  if (!editor.on) return;
  enqueue(async () => {
    if (addToChord && editor.last) await send(`notes/${editor.last}`, { action: "stack", midi });
    else if (editor.cursor) await send(`notes/${editor.cursor}`, { action: "write", type: editor.type, dots: editor.dots, midi, voice: editor.voice });
  });
}

function hearKey(midi) {
  try {
    keyPreview ??= new (window.AudioContext || window.webkitAudioContext)();
    tone(keyPreview, midi, keyPreview.currentTime + 0.01, PREVIEW_SECONDS, 0.3, "triangle");
  } catch {
    // Without sound the key still writes its note.
  }
}

// The timeline: a column for each measure, rows for what changes where, and a row for each
// instrument showing the measures it plays in. A click selects that measure, Shift+click stretches
// the selection to it, as in MuseScore.
function drawTimeline() {
  const line = score?.timeline;
  if (!line) return;
  const row = (label, cells, kind) => {
    const holder = document.createElement("div");
    holder.className = `row ${kind}`;
    const name = document.createElement("div");
    name.className = "label";
    name.textContent = label;
    name.title = label;
    const strip = document.createElement("div");
    strip.className = "cells";
    strip.append(...cells);
    holder.append(name, strip);
    return holder;
  };
  const cell = (measure, part, text = "", filled = false) => {
    const found = document.createElement("i");
    found.className = `cell${filled ? " filled" : ""}${text ? " marked" : ""}`;
    found.dataset.measure = measure;
    found.dataset.part = part;
    if (text) { found.textContent = text; found.title = text; }
    return found;
  };
  const numbers = line.measures.map((measure, index) => cell(index, 0, index === 0 || (index + 1) % 5 === 0 ? String(index + 1) : ""));
  const rows = [row("", numbers, "numbers")];
  for (const [key, label] of TIMELINE_ROWS) rows.push(row(label, line.measures.map((measure, index) => cell(index, 0, measure[key])), "meta"));
  line.parts.forEach((filled, part) => {
    rows.push(row(score.parts?.[part] || `Instrument ${part + 1}`, filled.map((played, index) => cell(index, part, "", played)), "part"));
  });
  $("timelineGrid").replaceChildren(...rows);
  markTimeline();
}

// Show the selected measures in the timeline, on the rows of the instruments they're in.
function markTimeline() {
  if (!dockOpen("timeline") || $("timelinePanel").hidden) return;
  const measures = new Map([...document.querySelectorAll("#pages g.measure")].map((measure, index) => [measure, index]));
  const chosen = new Set();
  for (const id of range ? range.ids : [actionTarget()].filter(Boolean)) {
    const element = document.getElementById(id);
    const index = element && measures.get(element.closest("g.measure"));
    if (index !== undefined && index !== null) chosen.add(`${partOfStaff(staffNumber(element))}:${index}`);
  }
  document.querySelectorAll("#timelineGrid .part .cell").forEach(cell => cell.classList.toggle("chosen", chosen.has(`${cell.dataset.part}:${cell.dataset.measure}`)));
}

function clickTimeline(event) {
  const cell = event.target.closest(".cell");
  if (!cell || !score) return;
  const part = Number(cell.dataset.part), index = Number(cell.dataset.measure);
  const measure = document.querySelectorAll("#pages g.measure")[index];
  const first = (score.partStaves ?? []).slice(0, part).reduce((total, count) => total + count, 0);
  const staff = measure?.querySelectorAll(":scope > g.staff")[first];
  if (!staff) return;
  const extend = event.shiftKey;
  enqueue(async () => {
    const opening = [...staff.querySelectorAll(EVENTS)].find(element => stepOf(element) === element);
    if (editor.on) { if (opening) moveCursor(opening.id); }
    else await selectMeasure(staff, extend);
    staff.scrollIntoView({ block: "nearest", inline: "nearest" });
  });
}

function setUpDock() {
  $("timelineGrid").addEventListener("click", clickTimeline);
  $("pianoPanel").addEventListener("wheel", event => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    keyboard.width = Math.min(WIDEST_KEY, Math.max(NARROWEST_KEY, keyboard.width + (event.deltaY < 0 ? 2 : -2)));
    keepSetting("keyWidth", String(keyboard.width));
    drawKeyboard();
  }, { passive: false });
  $("dockClose").addEventListener("click", () => { if (dock.active) toggleDock(dock.active); });
}

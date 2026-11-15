// The panel along the bottom of the window. It shows the chosen part's instrument (P), and can show
// the mixer (F10) or the timeline (F12) in its place. Each mode remembers whether it's out: Write and
// Practice start with the instrument, Read with nothing but the score.
// The timeline's first rows, each saying where something changes.
const TIMELINE_ROWS = [["tempo", "Tempo"], ["time", "Time signature"], ["rehearsal", "Rehearsal mark"], ["key", "Key signature"], ["barline", "Barlines"], ["jump", "Jumps and markers"]];
const DOCK_VIEWS = ["instrument", "mixer", "timeline"];
const DOCK_PANELS = { mixer: "mixerPanel", timeline: "timelinePanel" };
const QUIETEST_DB = -60, LOUDEST_DB = 12;
const MIXER_SAVE_MS = 300;
const dock = { view: "instrument" };
let mixerSaving = 0;
let mixerWaiting = null; // Settings on their way to the server, which an edit's reply mustn't undo.

function dockKept() {
  return readSetting(`dock-${currentMode()}`, currentMode() === "read" ? "hidden" : "shown") === "shown";
}

function dockOpen(name) {
  return Boolean(score) && dockKept() && dock.view === name;
}

function dockLabel(name) {
  return name === "instrument" ? instrumentLabel() : name === "mixer" ? "Mixer" : "Timeline";
}

function drawDock() {
  const shown = Boolean(score) && dockKept() && !$("workspace").hidden;
  $("dock").hidden = !shown;
  $("dockTabs").replaceChildren(...DOCK_VIEWS.map(name => {
    const tab = document.createElement("button");
    tab.setAttribute("role", "tab");
    tab.dataset.dock = name;
    tab.textContent = dockLabel(name);
    tab.setAttribute("aria-selected", String(name === dock.view));
    tab.addEventListener("click", () => showDock(name));
    return tab;
  }));
  for (const [name, panel] of Object.entries(DOCK_PANELS)) $(panel).hidden = !shown || name !== dock.view;
  if (!shown || dock.view !== "instrument") for (const panel of ["pianoPanel", "fretboardPanel", "fingerboardPanel"]) $(panel).hidden = true;
  $("trayNote").textContent = shown && dock.view === "instrument" ? trayNote() : "";
  if (shown && dock.view === "instrument") drawInstrument();
  if (shown && dock.view === "mixer") drawMixer();
  if (shown && dock.view === "timeline") drawTimeline();
  drawCursor();
}

// What the instrument panel is showing, and what a press on it does just now.
function trayNote() {
  const part = partLabel(activePart());
  if (practice.follower) return `${part}: play the lit notes`;
  if (editor.on) return `✎ Writing: what you play on the ${KINDS[activeKind()].label.toLowerCase()} is written at the cursor. Shift adds to the chord`;
  if (currentMode() === "practice") return `${part}: the notes light as they sound`;
  if (range) return `${part}: select one note to change it from here, or press N to write`;
  if (actionTarget()) return `${part}: what you play becomes the selected note. Shift adds to its chord`;
  return `${part}: click to hear a note. Select one to change it, or press N to write`;
}

function showDock(name) {
  dock.view = name;
  keepSetting(`dock-${currentMode()}`, "shown");
  drawDock();
}

// P, F10 and F12 show their panel, or put the panel away if it's the one showing.
function toggleDock(name) {
  if (name === "piano") name = "instrument";
  if (dockOpen(name)) {
    keepSetting(`dock-${currentMode()}`, "hidden");
    drawDock();
  } else {
    showDock(name);
  }
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
  $("dockClose").addEventListener("click", () => toggleDock(dock.view));
}

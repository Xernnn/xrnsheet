// The panels docked below the score, as MuseScore docks them, each one a tab when more than one is
// open: the mixer (F10).
const DOCK_PANELS = { mixer: { label: "Mixer", keys: "F10" } };
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

function setUpDock() {
  $("dockClose").addEventListener("click", () => { if (dock.active) toggleDock(dock.active); });
}

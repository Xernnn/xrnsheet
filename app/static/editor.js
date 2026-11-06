// Note entry, selection, the toolbar and playback. This loads after the main script and starts the page.
const LENGTHS = [
  { key: "7", type: "whole", label: "Whole note" },
  { key: "6", type: "half", label: "Half note" },
  { key: "5", type: "quarter", label: "Quarter note" },
  { key: "4", type: "eighth", label: "Eighth note" },
  { key: "3", type: "16th", label: "Sixteenth note" },
  { key: "2", type: "32nd", label: "32nd note" },
  { key: "1", type: "64th", label: "64th note" },
];
const AFTER = "after:"; // A cursor past the last note of the score.
const GROUP_ACTIONS = new Set(["up", "down", "octaveUp", "octaveDown", "sharp", "flat", "natural", "delete", "clear"]);
const REACH_IN_STEPS = 10; // How far off a staff a click still counts as on it.
const EVENTS = "g.note, g.rest, g.mRest";
const editor = { on: false, type: "quarter", dots: 0, cursor: null, last: null, trail: [], future: [] };
let player = null;
let range = null; // A selected stretch: { first, last, ids }.
let clipboard = readClipboard();
let lyricNote = null;
let lyricSoFar = "";
let hint = "";

function noteIcon(type) {
  const open = type === "whole" || type === "half";
  const flags = { eighth: 1, "16th": 2, "32nd": 3, "64th": 4 }[type] || 0;
  const head = `<ellipse cx="5.5" cy="16" rx="4" ry="2.9" transform="rotate(-20 5.5 16)" fill="${open ? "none" : "currentColor"}" stroke="currentColor" stroke-width="1.4"/>`;
  const stem = type === "whole" ? "" : `<path d="M9.2 15V2" stroke="currentColor" stroke-width="1.3"/>`;
  let marks = "";
  for (let flag = 0; flag < flags; flag++) marks += `<path d="M9.2 ${2 + flag * 2.6}c1 2.4 4.2 2.8 3.3 6.2" fill="none" stroke="currentColor" stroke-width="1.3"/>`;
  return `<svg viewBox="0 0 15 20" width="13" height="18" aria-hidden="true">${head}${stem}${marks}</svg>`;
}

function plainId(id) {
  return id?.startsWith(AFTER) ? id.slice(AFTER.length) : id;
}

// What an action applies to: the note just written while entering notes, otherwise the selection.
function actionTarget() {
  return plainId(editor.on ? (editor.last || editor.cursor) : selected);
}

// A note's whole shape includes its lyric, so anything about its position uses the notehead.
function headBox(element) {
  return (element.querySelector(".notehead") || element).getBoundingClientRect();
}

// The arrows step over a chord as one place, whichever of its notes they start from.
function stepOf(element) {
  return element?.closest("g.chord")?.querySelector("g.note") ?? element;
}

function allEvents() {
  return [...document.querySelectorAll(`#pages ${EVENTS.replaceAll(", ", ", #pages ")}`)].filter(element => stepOf(element) === element);
}

function staffNumber(element) {
  const staff = element?.closest("g.staff");
  return staff ? [...staff.parentElement.children].filter(child => child.classList.contains("staff")).indexOf(staff) : -1;
}

function receive(data) {
  score = data;
  if (data.written) editor.last = data.written;
  if (data.type) { editor.type = data.type; editor.dots = data.dots ?? 0; }
  if (data.cursor) {
    if (editor.on) editor.cursor = data.cursor;
    else selected = data.cursor.startsWith(AFTER) ? data.written : data.cursor;
  }
  message(data.message);
  draw();
}

function updateTools() {
  const target = actionTarget();
  document.querySelectorAll("#tools [data-needs]").forEach(control => { control.disabled = !target; });
  document.querySelectorAll("#lengths button").forEach(button => {
    button.setAttribute("aria-pressed", String(editor.on && button.dataset.type === editor.type));
  });
  $("dotButton").setAttribute("aria-pressed", String(editor.on && editor.dots > 0));
  $("inputToggle").setAttribute("aria-pressed", String(editor.on));
  $("inputToggle").textContent = editor.on ? "Stop entering" : "Enter notes";
  $("pitches").hidden = !editor.on;
  $("paste").disabled = !clipboard || !(editor.on ? editor.cursor : selected);
  const selection = range ? `${range.ids.length} notes selected. Copy them, or use an action.` : selected ? "Selected. Choose a length or an action. Shift-click selects a stretch." : "Click a note or rest, or press N to enter notes.";
  $("toolLabel").textContent = hint || (editor.on ? "Entering notes. Press A to G, 0 for a rest, or click the staff." : selection);
  hint = "";
}

function staffLines(staff) {
  const lines = [...staff.children].filter(child => child.tagName === "path");
  const top = lines[0].getBoundingClientRect();
  const bottom = lines.at(-1).getBoundingClientRect();
  return { left: top.left, right: top.right, top: top.top, bottom: bottom.top, step: (bottom.top - top.top) / 8 };
}

function drawCursor() {
  const caret = $("caret");
  if (!editor.on) { caret.hidden = true; return; }
  if (!document.getElementById(plainId(editor.cursor))) {
    const kept = editor.last && document.getElementById(editor.last);
    editor.cursor = kept ? editor.last : allEvents()[0]?.id ?? null;
  }
  const element = editor.cursor && document.getElementById(plainId(editor.cursor));
  if (!element) { caret.hidden = true; return; }
  const frame = $("pagesFrame").getBoundingClientRect();
  const box = headBox(element);
  const staff = element.closest("g.staff");
  const lines = staff ? staffLines(staff) : { top: box.top, bottom: box.bottom };
  const past = editor.cursor.startsWith(AFTER);
  caret.hidden = false;
  caret.style.left = `${(past ? box.right + 6 : box.left - 3) - frame.left + $("pagesFrame").scrollLeft}px`;
  caret.style.top = `${lines.top - frame.top - 6}px`;
  caret.style.width = `${past ? 10 : Math.max(box.width, 10) + 6}px`;
  caret.style.height = `${lines.bottom - lines.top + 12}px`;
  element.scrollIntoView({ block: "nearest", inline: "nearest" });
}

function drawRange() {
  if (!range) return;
  range.ids = range.ids.filter(id => document.getElementById(id));
  if (!document.getElementById(range.first) || !document.getElementById(range.last)) { range = null; return; }
  range.ids.forEach(id => document.getElementById(id).classList.add("selected"));
}

function drawBarChecks() {
  const checks = score.barChecks ?? [];
  $("barChecks").hidden = checks.length === 0;
  const chips = checks.slice(0, 40).map(check => {
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.textContent = `Bar ${check.measure + 1}: ${check.problem === "long" ? "too many beats" : "too few beats"}`;
    chip.addEventListener("click", () => enqueue(async () => {
      if (editor.on) moveCursor(check.note); else select(check.note);
      document.getElementById(check.note)?.scrollIntoView({ block: "center" });
    }));
    return chip;
  });
  $("barChecks").replaceChildren(...chips);
}

function drawTitle() {
  for (const [id, value] of [["titleInput", score.title], ["composerInput", score.composer]]) {
    if (document.activeElement !== $(id)) $(id).value = value ?? "";
  }
}

function moveCursor(id) {
  editor.cursor = id;
  editor.last = null;
  drawCursor();
  updateTools();
}

function setInput(on) {
  if (on === editor.on) return;
  editor.on = on;
  document.body.classList.toggle("inputting", on);
  if (on) {
    editor.cursor = selected ?? allEvents()[0]?.id ?? null;
    editor.last = null;
    editor.trail = [];
    editor.future = [];
    select(null);
  } else {
    const keep = editor.last ?? plainId(editor.cursor);
    editor.cursor = editor.last = null;
    $("ghost").hidden = true;
    select(keep && document.getElementById(keep) ? keep : null);
  }
  drawCursor();
  updateTools();
}

function toggleInput() {
  return enqueue(async () => setInput(!editor.on));
}

function writeAt(step) {
  return enqueue(async () => {
    const at = editor.cursor;
    if (!at) return;
    await send(`notes/${at}`, { action: "write", type: editor.type, dots: editor.dots, step });
  });
}

function placeUnder(x, y) {
  let found = null;
  let smallest = Infinity;
  for (const staff of document.querySelectorAll("#pages g.staff")) {
    const lines = staffLines(staff);
    if (x < lines.left || x > lines.right) continue;
    const gap = Math.max(lines.top - y, y - lines.bottom, 0);
    if (gap <= lines.step * REACH_IN_STEPS && gap < smallest) { found = { staff, lines }; smallest = gap; }
  }
  if (!found) return null;
  let target = null;
  let latest = -Infinity;
  for (const element of found.staff.querySelectorAll(EVENTS)) {
    const left = headBox(element).left;
    if (left - 8 <= x && left > latest) { target = element; latest = left; }
  }
  target ??= found.staff.querySelector(EVENTS);
  if (!target) return null;
  const box = headBox(target);
  return { id: target.id, line: Math.round((y - found.lines.top) / found.lines.step), lines: found.lines, x: box.left + box.width / 2, isNote: target.matches("g.note") };
}

function drawGhost(x, y) {
  const spot = editor.on ? placeUnder(x, y) : null;
  const ghost = $("ghost");
  ghost.hidden = !spot;
  if (!spot) return;
  const frame = $("pagesFrame").getBoundingClientRect();
  const width = spot.lines.step * 2.8;
  ghost.style.width = `${width}px`;
  ghost.style.height = `${spot.lines.step * 2}px`;
  ghost.style.left = `${spot.x - width / 2 - frame.left + $("pagesFrame").scrollLeft}px`;
  ghost.style.top = `${spot.lines.top + (spot.line - 1) * spot.lines.step - frame.top}px`;
}

function placeNote(spot, stack) {
  return enqueue(async () => {
    if (stack && spot.isNote) await send(`notes/${spot.id}`, { action: "stack", line: spot.line });
    else await send(`notes/${spot.id}`, { action: "write", type: editor.type, dots: editor.dots, line: spot.line });
  });
}

function chooseLength(type) {
  return enqueue(async () => {
    if (editor.on) {
      editor.type = type;
      editor.dots = 0;
      updateTools();
    } else if (selected) {
      await applyToNote("length", { type, dots: 0 });
    }
  });
}

function chooseDot() {
  return enqueue(async () => {
    if (editor.on) {
      editor.dots = editor.dots ? 0 : 1;
      updateTools();
    } else if (selected) {
      await applyToNote("dot");
    }
  });
}

function toggleTriplet() {
  return enqueue(async () => {
    if (editor.on && editor.cursor) {
      editor.last = null;
      await send(`notes/${editor.cursor}`, { action: "tuplet", type: editor.type, dots: editor.dots });
    } else if (!editor.on && selected) {
      await send(`notes/${selected}`, { action: "tupletHere" });
    }
  });
}

function moveAlong(delta, extend = false) {
  return enqueue(async () => {
    const position = editor.on ? editor.cursor : range?.last ?? selected;
    const current = stepOf(document.getElementById(plainId(position)));
    const staff = staffNumber(current);
    const list = allEvents().filter(element => staff < 0 || staffNumber(element) === staff);
    // A cursor past the last note is one place further on than that note.
    const index = list.indexOf(current) + (position?.startsWith(AFTER) ? 1 : 0) + delta;
    if (editor.on && index === list.length && current) { moveCursor(AFTER + list.at(-1).id); return; }
    const next = list[Math.min(Math.max(index, 0), list.length - 1)];
    if (!next) return;
    if (editor.on) moveCursor(next.id);
    else if (extend) await growRange(next.id);
    else select(next.id);
  });
}

async function growRange(id) {
  const first = range?.first ?? selected;
  if (!first) { select(id); return; }
  const data = await askServer("range", { first, last: id });
  document.querySelectorAll("#pages g.selected").forEach(element => element.classList.remove("selected"));
  range = { first, last: id, ids: data.ids };
  selected = id;
  drawRange();
  updateTools();
}

function selectEverything() {
  return enqueue(async () => {
    const events = allEvents();
    const start = selected ?? events[0]?.id;
    if (!start) return;
    const staff = staffNumber(stepOf(document.getElementById(start)));
    const inStaff = events.filter(element => staffNumber(element) === staff);
    selected = inStaff[0].id;
    range = null;
    await growRange(inStaff.at(-1).id);
  });
}

// A request that only reads, so it changes nothing and draws nothing.
async function askServer(path, body) {
  const response = await fetch(`/api/scores/${scoreId}/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "That didn't work.");
  return data;
}

function readClipboard() {
  try {
    return JSON.parse(sessionStorage.getItem("xrnsheet-clipboard"));
  } catch {
    return null;
  }
}

function copySelection() {
  return enqueue(async () => {
    const first = range?.first ?? actionTarget();
    if (!first) return;
    const data = await askServer("copy", { first, last: range?.last ?? first });
    clipboard = data.events;
    try {
      sessionStorage.setItem("xrnsheet-clipboard", JSON.stringify(clipboard));
    } catch {
      // Without storage it can still be pasted until the page is reloaded.
    }
    hint = "Copied. Choose where it goes, then press Ctrl+V.";
    updateTools();
  });
}

function cutSelection() {
  copySelection();
  noteAction("delete");
}

function pasteHere() {
  return enqueue(async () => {
    const at = editor.on ? editor.cursor : range?.first ?? selected;
    if (!clipboard || !at) return;
    range = null;
    await send("paste", { at, events: clipboard });
  });
}

function placeLyricBox(id) {
  const element = document.getElementById(id);
  const input = $("lyricInput");
  const frame = $("pagesFrame").getBoundingClientRect();
  const box = headBox(element);
  const staff = element.closest("g.staff");
  input.style.left = `${box.left - frame.left + $("pagesFrame").scrollLeft - 8}px`;
  input.style.top = `${(staff ? staffLines(staff).bottom : box.bottom) - frame.top + 18}px`;
  element.scrollIntoView({ block: "nearest", inline: "nearest" });
}

function openLyric(id) {
  const element = id && document.getElementById(id);
  if (!element?.matches("g.note")) { message("Choose a note for the lyric first."); return; }
  const input = $("lyricInput");
  lyricNote = id;
  lyricSoFar = input.placeholder = "";
  input.hidden = false;
  placeLyricBox(id);
  input.focus();
  // Whoever is typing quickly has already started on this syllable, so leave it alone.
  if (input.value) return;
  input.value = element.querySelector(".syl")?.textContent.trim() ?? "";
  input.select();
}

function startLyrics() {
  return enqueue(async () => openLyric(actionTarget()));
}

function closeLyric() {
  const input = $("lyricInput");
  lyricNote = null;
  lyricSoFar = input.placeholder = "";
  input.hidden = true;
  input.value = "";
}

// The next syllable can be typed before this one is saved, so the note is only read in its turn.
// The last note has no next one, so the word goes on in the box, whatever separates its parts.
function saveLyric(joined, moveOn, separator = "") {
  const input = $("lyricInput");
  const text = input.value;
  input.value = "";
  return enqueue(async () => {
    if (!lyricNote) return;
    const word = (lyricSoFar + text).replace(/[ -]+$/, "");
    const data = await send(`lyrics/${lyricNote}`, { text: word, joined });
    if (moveOn && data.next) openLyric(data.next);
    else if (moveOn) lyricSoFar = input.placeholder = word + separator;
    else closeLyric();
  });
}

// Undo and redo also put the cursor back, so entering notes can carry straight on.
function undoEdit() {
  return enqueue(async () => {
    if (!score?.canUndo) return;
    const step = editor.on ? editor.trail.pop() : null;
    await send("undo");
    if (step) { editor.future.push(step); moveCursor(step.before); }
  });
}

function redoEdit() {
  return enqueue(async () => {
    if (!score?.canRedo) return;
    const step = editor.on ? editor.future.pop() : null;
    await send("redo");
    if (step) { editor.trail.push(step); moveCursor(step.after); }
  });
}

function barAction(action, extra = {}) {
  return enqueue(async () => { await send("bars", { action, note: range?.first ?? actionTarget(), ...extra }); });
}

function tone(audio, midi, at, length, volume, wave) {
  const oscillator = audio.createOscillator();
  const gain = audio.createGain();
  const end = at + Math.max(length, 0.05);
  oscillator.type = wave;
  oscillator.frequency.value = 440 * 2 ** ((midi - 69) / 12);
  gain.gain.setValueAtTime(0, at);
  gain.gain.linearRampToValueAtTime(volume, at + 0.012);
  gain.gain.linearRampToValueAtTime(volume * 0.7, Math.max(at + 0.02, end - 0.04));
  gain.gain.linearRampToValueAtTime(0, end);
  oscillator.connect(gain).connect(audio.destination);
  oscillator.start(at);
  oscillator.stop(end + 0.02);
}

// Works with speakers or with an offline context, which is how the tests check what would be heard.
function schedule(audio, data, from, zero) {
  const beat = 60 / data.tempo;
  const notes = data.notes.filter(note => note.start >= from);
  for (const note of notes) tone(audio, note.midi, zero + (note.start - from) * beat, note.length * beat, 0.16, "triangle");
  const chords = data.chords.filter(chord => chord.start + chord.length > from);
  for (const chord of chords) {
    const begins = Math.max(chord.start, from);
    for (const midi of chord.midi) tone(audio, midi, zero + (begins - from) * beat, (chord.start + chord.length - begins) * beat, 0.045, "sine");
  }
  return { notes, chords, beat };
}

function clearPlaying() {
  document.querySelectorAll("#pages .playing").forEach(element => element.classList.remove("playing"));
}

function stopPlaying() {
  if (!player) return;
  player.timers.forEach(clearTimeout);
  player.audio.close();
  player = null;
  clearPlaying();
  $("play").textContent = "Play";
}

async function startPlaying() {
  const response = await fetch(`/api/scores/${scoreId}/playback`);
  if (!response.ok) { message("This sheet couldn't be played."); return; }
  const data = await response.json();
  const audio = new (window.AudioContext || window.webkitAudioContext)();
  const chosen = data.notes.find(note => note.id === plainId(editor.on ? editor.cursor : selected));
  const from = chosen ? chosen.start : 0;
  const { notes, chords, beat } = schedule(audio, data, from, audio.currentTime + 0.1);
  const timers = notes.map(note => setTimeout(() => {
    clearPlaying();
    const element = document.getElementById(note.id);
    element?.classList.add("playing");
    element?.scrollIntoView({ block: "nearest" });
  }, 100 + (note.start - from) * beat * 1000));
  timers.push(setTimeout(stopPlaying, 300 + (data.total - from) * beat * 1000));
  player = { audio, timers };
  $("play").textContent = "Stop";
  $("play").dataset.notes = notes.length;
  $("play").dataset.chords = chords.length;
}

function togglePlaying() {
  return player ? stopPlaying() : startPlaying();
}

function setUpToolbar() {
  $("lengths").replaceChildren(...LENGTHS.map(({ key, type, label }) => {
    const button = document.createElement("button");
    button.dataset.type = type;
    button.title = `${label} (${key})`;
    button.setAttribute("aria-label", label);
    button.innerHTML = noteIcon(type);
    button.addEventListener("click", () => chooseLength(type));
    return button;
  }));
  $("pitches").replaceChildren(...[..."CDEFGAB"].map(step => {
    const button = document.createElement("button");
    button.textContent = step;
    button.addEventListener("click", () => writeAt(step));
    return button;
  }), Object.assign(document.createElement("button"), { textContent: "Rest", onclick: () => writeAt(null) }));
  const names = ["C♭", "G♭", "D♭", "A♭", "E♭", "B♭", "F", "C", "G", "D", "A", "E", "B", "F♯", "C♯"];
  const minors = ["a♭", "e♭", "b♭", "f", "c", "g", "d", "a", "e", "b", "f♯", "c♯", "g♯", "d♯", "a♯"];
  const options = names.map((name, index) => new Option(`${name} major / ${minors[index]} minor`, index - 7));
  $("keyChoice").append(...options);
  $("blankKey").append(...options.map(option => new Option(option.text, option.value)));
  $("blankKey").value = "0";
  $("dotButton").addEventListener("click", chooseDot);
  $("tripletButton").addEventListener("click", toggleTriplet);
  $("inputToggle").addEventListener("click", toggleInput);
  $("addBar").addEventListener("click", () => barAction("insert"));
  $("deleteBar").addEventListener("click", () => barAction("delete"));
  $("copy").addEventListener("click", copySelection);
  $("paste").addEventListener("click", pasteHere);
  $("lyricButton").addEventListener("click", startLyrics);
  $("keyChoice").addEventListener("change", event => {
    if (event.target.value !== "") barAction("key", { fifths: Number(event.target.value) });
    event.target.value = "";
    event.target.blur();
  });
  $("timeChoice").addEventListener("change", event => {
    if (event.target.value) {
      const [beats, beat_type] = event.target.value.split("/").map(Number);
      barAction("time", { beats, beat_type });
    }
    event.target.value = "";
    event.target.blur();
  });
  $("repeatChoice").addEventListener("change", event => {
    const choice = event.target.value;
    if (choice === "forward" || choice === "backward") barAction(choice);
    else if (choice) barAction("ending", { number: Number(choice), last: range?.last ?? null });
    event.target.value = "";
    event.target.blur();
  });
  // A clicked button keeps the focus, and Space would press it again instead of playing.
  $("tools").addEventListener("click", event => event.target.closest("button")?.blur());
  $("play").addEventListener("click", event => { event.currentTarget.blur(); togglePlaying(); });
  $("redo").addEventListener("click", redoEdit);
  window.addEventListener("resize", drawCursor);
  $("pages").addEventListener("mousemove", event => drawGhost(event.clientX, event.clientY));
  $("pages").addEventListener("mouseleave", () => { $("ghost").hidden = true; });
  for (const id of ["titleInput", "composerInput"]) {
    // Read now, because a reply arriving first redraws these fields with what the server knew.
    $(id).addEventListener("change", () => {
      const body = { title: $("titleInput").value, composer: $("composerInput").value };
      enqueue(() => send("title", body));
    });
    $(id).addEventListener("keydown", event => { if (event.key === "Enter") event.target.blur(); });
  }
  $("lyricInput").addEventListener("keydown", event => {
    if (event.key === " ") { event.preventDefault(); saveLyric(false, true, " "); }
    else if (event.key === "-") { event.preventDefault(); saveLyric(true, true, "-"); }
    else if (event.key === "Enter") { event.preventDefault(); saveLyric(null, false); }
    else if (event.key === "Escape") closeLyric();
  });
}

function setUpBlankForm() {
  $("blankOpen").addEventListener("click", () => { $("blankForm").hidden = false; $("blankOpen").hidden = true; });
  $("blankForm").addEventListener("submit", async event => {
    event.preventDefault();
    const [beats, beat_type] = $("blankTime").value.split("/").map(Number);
    const response = await fetch("/api/scores/blank", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fifths: Number($("blankKey").value), beats, beat_type, clef: $("blankClef").value, measures: Number($("blankBars").value) }),
    });
    const data = await response.json();
    if (!response.ok) { message(typeof data.detail === "string" ? data.detail : "Those settings didn't work."); return; }
    scoreId = data.id;
    location.hash = scoreId;
    await poll();
    setInput(true);
  });
}

const PITCH_KEYS = { "#": "sharp", "+": "sharp", "-": "flat", "=": "natural" };

function handleControlKey(event) {
  const key = event.key.toLowerCase();
  const actions = {
    z: () => (event.shiftKey ? redoEdit() : undoEdit()),
    y: redoEdit,
    c: copySelection,
    x: cutSelection,
    v: pasteHere,
    a: selectEverything,
    l: startLyrics,
    3: toggleTriplet,
    arrowup: () => noteAction("octaveUp"),
    arrowdown: () => noteAction("octaveDown"),
    delete: () => noteAction("close"),
    backspace: () => noteAction("close"),
  };
  if (actions[key]) { event.preventDefault(); actions[key](); }
}

document.addEventListener("keydown", event => {
  if (["INPUT", "SELECT"].includes(event.target.tagName) || $("workspace").hidden) return;
  const key = event.key;
  if (event.ctrlKey || event.metaKey) { handleControlKey(event); return; }
  const handled = () => event.preventDefault();

  if (key === "Escape") {
    closeEditor();
    enqueue(async () => { if (editor.on) setInput(false); else select(null); });
    return;
  }
  if (key === " ") { handled(); togglePlaying(); return; }
  if (key === "n" || key === "N") { handled(); toggleInput(); return; }
  if (key === "ArrowLeft") { handled(); moveAlong(-1, event.shiftKey); return; }
  if (key === "ArrowRight") { handled(); moveAlong(1, event.shiftKey); return; }
  if (key === "ArrowUp") { handled(); noteAction("up"); return; }
  if (key === "ArrowDown") { handled(); noteAction("down"); return; }
  if (key === ".") { handled(); chooseDot(); return; }
  if (key === "t" || key === "T") { handled(); noteAction("tie"); return; }
  if (PITCH_KEYS[key]) { handled(); noteAction(PITCH_KEYS[key]); return; }
  const length = LENGTHS.find(item => item.key === key);
  if (length) { handled(); chooseLength(length.type); return; }
  if (editor.on) {
    if (key === "0") { handled(); writeAt(null); return; }
    if (key === "Backspace") { handled(); undoEdit(); return; }
    if (/^[a-gA-G]$/.test(key)) {
      handled();
      if (event.shiftKey) noteAction("stack", { step: key.toUpperCase() }); else writeAt(key.toUpperCase());
    }
    return;
  }
  if (key === "0" || key === "Delete" || key === "Backspace") { handled(); noteAction("delete"); return; }
  if (key === "b") { handled(); noteAction("flat"); }
});

setUpToolbar();
setUpBlankForm();
updateTools();
if (location.hash.length > 1) { scoreId = location.hash.slice(1); poll(); } else drawHistory();

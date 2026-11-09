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
const GROUP_ACTIONS = new Set(["up", "down", "octaveUp", "octaveDown", "sharp", "flat", "natural", "delete", "clear", "respell", "flip"]);
const REACH_IN_STEPS = 10; // How far off a staff a click still counts as on it.
const EVENTS = "g.note, g.rest, g.mRest";
const VOICES = 4;
const MOST_VERSES = 6;
const CHORD_WINDOW_MS = 40; // Keys of a MIDI keyboard pressed this close together are one chord.
const DRAG_START_PX = 4;
const CURVE_REACH_PX = 6; // A slur is hair-thin, so a click this close to it counts.
const CURVE_SAMPLE_PX = 4;
const AUDIO_RATE = 44100;
const AUDIO_TAIL_SECONDS = 0.5;
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
const EXPRESSIONS = { staff: "Text, e.g. solo", expression: "Expression, e.g. dolce" };
const editor = { on: false, type: "quarter", dots: 0, voice: 1, cursor: null, last: null, trail: [], future: [] };
let player = null;
let range = null; // A selected stretch: { first, last, ids }.
let clipboard = readClipboard();
let lyricNote = null;
let lyricSoFar = "";
let lyricVerse = 1;
let hint = "";
let dragged = false;
let drag = null;
let lastMidiKey = 0;
let midiAsked = false;

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

function layerNumber(element) {
  const layer = element?.closest("g.layer");
  return layer ? [...layer.parentElement.children].filter(child => child.classList.contains("layer")).indexOf(layer) : 0;
}

// The arrows follow one voice through a bar. A bar without that voice is walked through as it is.
function eventsInVoice(staff, layer) {
  const onStaff = allEvents().filter(element => staff < 0 || staffNumber(element) === staff);
  const layersIn = new Map();
  for (const element of onStaff) {
    const bar = element.closest("g.measure");
    layersIn.set(bar, (layersIn.get(bar) ?? new Set()).add(layerNumber(element)));
  }
  return onStaff.filter(element => {
    const here = layersIn.get(element.closest("g.measure"));
    return !here.has(layer) || layerNumber(element) === layer;
  });
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
  document.querySelectorAll("#voices button").forEach(button => {
    button.setAttribute("aria-pressed", String(editor.on && Number(button.dataset.voice) === editor.voice));
    button.disabled = !editor.on && !target;
  });
  for (const id of ["markChoice", "dynamicChoice", "tempoChoice", "textChoice", "jumpChoice", "clefChoice"]) $(id).disabled = !target;
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
    setUpMidi();
    editor.cursor = selected ?? allEvents()[0]?.id ?? null;
    editor.voice = editor.cursor ? layerNumber(document.getElementById(editor.cursor)) + 1 : 1;
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
    await send(`notes/${at}`, { action: "write", type: editor.type, dots: editor.dots, step, voice: editor.voice });
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
    else await send(`notes/${spot.id}`, { action: "write", type: editor.type, dots: editor.dots, line: spot.line, voice: editor.voice });
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

function toggleTuplet(actual = 3) {
  return enqueue(async () => {
    if (editor.on && editor.cursor) {
      editor.last = null;
      await send(`notes/${editor.cursor}`, { action: "tuplet", type: editor.type, dots: editor.dots, actual });
    } else if (!editor.on && selected) {
      await send(`notes/${selected}`, { action: "tupletHere", actual });
    }
  });
}

function moveAlong(delta, extend = false) {
  return enqueue(async () => {
    const position = editor.on ? editor.cursor : range?.last ?? selected;
    const current = stepOf(document.getElementById(plainId(position)));
    const staff = staffNumber(current);
    const list = eventsInVoice(staff, layerNumber(current));
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

// Copy what's chosen, and say whether anything was. A copy that fails throws.
async function copyChosen() {
  const first = range?.first ?? actionTarget();
  if (!first) return false;
  const data = await askServer("copy", { first, last: range?.last ?? first });
  clipboard = data.events;
  try {
    sessionStorage.setItem("xrnsheet-clipboard", JSON.stringify(clipboard));
  } catch {
    // Without storage it can still be pasted until the page is reloaded.
  }
  hint = "Copied. Choose where it goes, then press Ctrl+V.";
  updateTools();
  return true;
}

function copySelection() {
  return enqueue(copyChosen);
}

// One task, so the notes are only deleted once they've been copied. The queue would carry on
// past a copy that failed, and the notes would be lost.
function cutSelection() {
  return enqueue(async () => {
    if (await copyChosen()) await applyToNote("delete");
  });
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
  input.value = element.querySelectorAll(".verse")[lyricVerse - 1]?.querySelector(".syl")?.textContent.trim() ?? "";
  input.select();
}

function changeVerse(by) {
  return enqueue(async () => {
    if (!lyricNote) return;
    const input = $("lyricInput");
    await send(`lyrics/${lyricNote}`, { text: input.value, joined: null, verse: lyricVerse });
    lyricVerse = Math.max(1, Math.min(MOST_VERSES, lyricVerse + by));
    input.value = "";
    hint = `Verse ${lyricVerse}`;
    openLyric(lyricNote);
    updateTools();
  });
}

function startLyrics() {
  return enqueue(async () => {
    lyricVerse = 1;
    openLyric(actionTarget());
  });
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
    const data = await send(`lyrics/${lyricNote}`, { text: word, joined, verse: lyricVerse });
    if (moveOn && data.next) openLyric(data.next);
    else if (moveOn) lyricSoFar = input.placeholder = word + separator;
    else closeLyric();
  });
}

function markTarget() {
  if (range) return range.ids;
  const id = actionTarget();
  return id ? [id] : [];
}

function markAction(action, extra = {}) {
  return enqueue(async () => {
    const notes = markTarget();
    if (!notes.length) { message("Choose a note first."); return; }
    return send("marks", { action, notes, ...extra });
  });
}

function addGrace() {
  return enqueue(async () => {
    const notes = markTarget();
    if (!notes.length) { message("Choose a note first."); return; }
    const data = await send("marks", { action: "grace", notes });
    if (!editor.on && data.written) select(data.written);
  });
}

function chooseVoice(number) {
  return enqueue(async () => {
    if (editor.on) {
      editor.voice = number;
      updateTools();
    } else if (markTarget().length) {
      await send("voice", { notes: markTarget(), voice: number });
    }
  });
}

function repeatSelection() {
  return enqueue(async () => {
    const first = range?.first ?? actionTarget();
    if (!first) return;
    const data = await askServer("copy", { first, last: range?.last ?? first });
    const at = editor.on ? editor.cursor : data.next;
    range = null;
    await send("paste", { at, events: data.events });
  });
}

function transposeBy(choice) {
  return enqueue(async () => {
    const [interval, direction] = choice.split(":");
    const first = range?.first ?? (selected && !editor.on ? selected : null);
    const body = interval === "key" ? { key: Number(direction), direction: "closest" } : { interval, direction, first, last: range?.last ?? first };
    await send("transpose", body);
  });
}

// A small box for typing, shared with chords, so a mark's text is changed where it's drawn.
function askForText(placeholder, newMark, value = "") {
  return enqueue(async () => {
    const id = actionTarget();
    const note = id && document.getElementById(id);
    if (!note) { message("Choose a note first."); return; }
    openEditor(headBox(note), null, value);
    editing = { newMark: { ...newMark, notes: [id] } };
    $("chordInput").placeholder = placeholder;
  });
}

// A slur or hairpin carried over a line break is drawn in two pieces, and the second has only a class.
function markIdOf(element) {
  return element.dataset.mark || element.id || [...element.classList].find(name => name.startsWith("id-"))?.slice(3);
}

function openMark(element) {
  const mark = score.marks.find(item => item.id === markIdOf(element));
  if (!mark) return;
  const box = element.getBoundingClientRect();
  openEditor(box, null, mark.text);
  editing = { mark: mark.id };
  $("chordDelete").hidden = false;
  $("chordInput").hidden = $("chordSave").hidden = !mark.text || mark.kind === "jump" || mark.kind === "repeat-sign";
  $("chordInput").placeholder = "";
  element.classList.add("selected");
}

// Verovio makes up its own ids for these, so they're matched up by order within each bar. If a
// bar doesn't draw the number expected, it's safer to leave its marks unclickable than to guess.
function tagMarks() {
  const groups = new Map();
  for (const mark of score.marks ?? []) {
    if (mark.drawn === "slur" || mark.drawn === "hairpin") continue;
    const key = `${mark.bar}/${mark.drawn}`;
    groups.set(key, [...(groups.get(key) ?? []), mark]);
  }
  for (const [key, marks] of groups) {
    const [bar, drawn] = key.split("/");
    const shown = document.getElementById(bar)?.querySelectorAll(`g.${drawn}`) ?? [];
    if (shown.length !== marks.length) continue;
    shown.forEach((element, index) => { element.dataset.mark = marks[index].id; });
  }
}

// A slur's box covers the notes under it, so this measures to the curve itself. It's also why a
// click is never left to the browser, which only finds a curve this thin some of the time.
function slurNear(x, y) {
  const reach = CURVE_REACH_PX;
  for (const slur of document.querySelectorAll("#pages g.slur")) {
    const box = slur.getBoundingClientRect();
    if (x < box.left - reach || x > box.right + reach || y < box.top - reach || y > box.bottom + reach) continue;
    for (const path of slur.querySelectorAll("path")) {
      const matrix = path.getScreenCTM();
      const length = path.getTotalLength();
      const samples = Math.ceil((box.width + box.height) / CURVE_SAMPLE_PX) * 2;
      for (let step = 0; step <= samples; step++) {
        const { x: pathX, y: pathY } = path.getPointAtLength(length * step / samples);
        const point = new DOMPoint(pathX, pathY).matrixTransform(matrix);
        if (Math.hypot(point.x - x, point.y - y) <= reach) return slur;
      }
    }
  }
  return null;
}

function markAt(event) {
  if (event.target.closest("g.note, g.rest, g.mRest")) return null;
  return event.target.closest("g[data-mark], g.slur, g.hairpin")
    ?? slurNear(event.clientX, event.clientY)
    ?? nearest("#pages g[data-mark], #pages g.hairpin", event.clientX, event.clientY, 4);
}

function drawParts() {
  const names = score.parts ?? [];
  const choice = $("partPdf");
  choice.hidden = names.length < 2;
  if (choice.hidden) return;
  choice.replaceChildren(new Option("Print a part…", ""), ...names.map((name, index) => new Option(name || `Instrument ${index + 1}`, index)));
}

function startDrag(event) {
  if (editor.on || event.shiftKey) return;
  const note = event.target.closest("g.note");
  if (!note || note.id !== selected) return;
  const staff = note.closest("g.staff");
  drag = { id: note.id, y: event.clientY, step: staffLines(staff).step, note, steps: 0 };
}

function moveDrag(event) {
  if (!drag) return;
  const distance = drag.y - event.clientY;
  if (!drag.moved && Math.abs(distance) < DRAG_START_PX) return;
  drag.moved = true;
  drag.steps = Math.round(distance / drag.step);
  drag.note.style.transform = `translateY(${-drag.steps * drag.step}px)`;
}

function endDrag() {
  if (!drag) return;
  const { id, steps, moved, note } = drag;
  drag = null;
  note.style.transform = "";
  if (!moved) return;
  dragged = true;
  if (steps) enqueue(() => send(`notes/${id}`, { action: "moveBy", steps }));
}

// Keys pressed together stack into a chord, the way they would on paper.
function midiKey(number) {
  return enqueue(async () => {
    if (!editor.on || !editor.cursor) return;
    const now = performance.now();
    const together = now - lastMidiKey < CHORD_WINDOW_MS && editor.last;
    lastMidiKey = now;
    if (together) await send(`notes/${editor.last}`, { action: "stack", midi: number });
    else await send(`notes/${editor.cursor}`, { action: "write", type: editor.type, dots: editor.dots, midi: number, voice: editor.voice });
  });
}

// Asked for only when someone starts writing, as a browser may show a permission prompt for it.
function setUpMidi() {
  if (midiAsked) return;
  midiAsked = true;
  navigator.requestMIDIAccess?.().then(access => {
    const listen = input => {
      input.onmidimessage = ({ data }) => { if ((data[0] & 0xf0) === 0x90 && data[2] > 0) midiKey(data[1]); };
    };
    access.inputs.forEach(listen);
    access.onstatechange = ({ port }) => { if (port.type === "input" && port.state === "connected") listen(port); };
  }).catch(() => {});
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

// Turns places in quarter notes into seconds, which takes adding up every tempo before them.
function clockFor(data, speed) {
  const changes = data.tempos?.length ? data.tempos : [{ start: 0, bpm: data.tempo }];
  let seconds = 0;
  const marks = changes.map((change, index) => {
    if (index) seconds += (change.start - changes[index - 1].start) * 60 / (changes[index - 1].bpm * speed);
    return { ...change, seconds };
  });
  return place => {
    const mark = marks.findLast(item => item.start <= place) ?? marks[0];
    return mark.seconds + (place - mark.start) * 60 / (mark.bpm * speed);
  };
}

function soundOf(data, note) {
  const program = data.programs?.[note.part] ?? 1;
  return FAMILY_SOUNDS[Math.floor((program - 1) / 8)] ?? USUAL_SOUND;
}

// Works with speakers or with an offline context, which is how the tests check what would be heard.
function schedule(audio, data, from, zero, speed = 1) {
  const clock = clockFor(data, speed);
  const at = place => zero + clock(place) - clock(from);
  const notes = data.notes.filter(note => note.start >= from);
  for (const note of notes) {
    const { wave, level } = soundOf(data, note);
    tone(audio, note.midi, at(note.start), at(note.start + note.length) - at(note.start), 0.4 * note.volume ** 2 * level, wave);
  }
  const chords = data.chords.filter(chord => chord.start + chord.length > from);
  for (const chord of chords) {
    const begins = Math.max(chord.start, from);
    for (const midi of chord.midi) tone(audio, midi, at(begins), at(chord.start + chord.length) - at(begins), 0.045, "sine");
  }
  return { notes, chords, at };
}

// A plain 16-bit WAV, which any program can open.
function wavFile(samples, rate) {
  const peak = Math.max(1, samples.reduce((top, sample) => Math.max(top, Math.abs(sample)), 0));
  const file = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const write = (at, word) => [...word].forEach((letter, index) => file.setUint8(at + index, letter.charCodeAt(0)));
  write(0, "RIFF");
  file.setUint32(4, 36 + samples.length * 2, true);
  write(8, "WAVEfmt ");
  file.setUint32(16, 16, true);
  file.setUint16(20, 1, true); // Plain samples, not compressed.
  file.setUint16(22, 1, true); // One channel.
  file.setUint32(24, rate, true);
  file.setUint32(28, rate * 2, true);
  file.setUint16(32, 2, true);
  file.setUint16(34, 16, true);
  write(36, "data");
  file.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, index) => file.setInt16(44 + index * 2, sample / peak * 0x7fff, true));
  return new Blob([file], { type: "audio/wav" });
}

// Made by the same code as Play, so the file sounds just as the score does, only faster than real time.
function saveAudio() {
  return enqueue(async () => {
    const data = await (await fetch(`/api/scores/${scoreId}/playback`)).json();
    const seconds = clockFor(data, 1)(data.total) + AUDIO_TAIL_SECONDS;
    const audio = new OfflineAudioContext(1, Math.ceil(seconds * AUDIO_RATE), AUDIO_RATE);
    schedule(audio, data, 0, 0);
    const rendered = await audio.startRendering();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(wavFile(rendered.getChannelData(0), AUDIO_RATE));
    link.download = "score.wav";
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  });
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
  const { notes, chords, at } = schedule(audio, data, from, audio.currentTime + 0.1, Number($("speed").value));
  const started = audio.currentTime + 0.1;
  const timers = notes.map(note => setTimeout(() => {
    clearPlaying();
    const element = document.getElementById(note.id);
    element?.classList.add("playing");
    element?.scrollIntoView({ block: "nearest" });
  }, (at(note.start) - started + 0.1) * 1000));
  timers.push(setTimeout(stopPlaying, (at(data.total) - started + 0.3) * 1000));
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
  $("voices").replaceChildren(...Array.from({ length: VOICES }, (_, index) => {
    const button = document.createElement("button");
    button.dataset.voice = index + 1;
    button.textContent = `V${index + 1}`;
    button.title = `Write in voice ${index + 1}, or move the selection to it (Ctrl+Alt+${index + 1})`;
    button.addEventListener("click", () => chooseVoice(index + 1));
    return button;
  }));
  $("transposeKeys").append(...names.map((name, index) => new Option(`To ${name} major`, `key:${index - 7}`)));
  const whenChosen = (id, act) => $(id).addEventListener("change", event => {
    if (event.target.value) act(event.target.value);
    event.target.value = "";
    event.target.blur();
  });
  whenChosen("markChoice", name => markAction("toggle", { name }));
  whenChosen("dynamicChoice", name => markAction("dynamic", { name }));
  whenChosen("tempoChoice", choice => {
    if (choice === "type") askForText("Tempo, e.g. Allegro 132", { action: "tempo" });
    else if (choice === "swing" || choice === "straight") markAction("swing", { on: choice === "swing" });
    else markAction("tempo", { text: choice });
  });
  whenChosen("textChoice", choice => {
    if (choice === "rehearsal") markAction("rehearsal");
    else askForText(EXPRESSIONS[choice], { action: "text", name: choice });
  });
  whenChosen("jumpChoice", name => markAction("jump", { name }));
  whenChosen("clefChoice", clef => barAction("clef", { clef }));
  whenChosen("transposeChoice", transposeBy);
  whenChosen("partChoice", name => enqueue(() => send("parts", name === "remove" ? { action: "remove", note: actionTarget() } : { action: "add", name })));
  whenChosen("layoutChoice", choice => (choice === "break" ? barAction("break") : barAction("barsPerLine", { count: Number(choice) })));
  $("partPdf").addEventListener("change", event => {
    if (event.target.value !== "") window.open(`/api/scores/${scoreId}/score.pdf?part=${event.target.value}`);
    event.target.value = "";
  });
  $("slurButton").addEventListener("click", () => markAction("slur"));
  $("crescButton").addEventListener("click", () => markAction("hairpin", { name: "crescendo" }));
  $("dimButton").addEventListener("click", () => markAction("hairpin", { name: "diminuendo" }));
  $("graceButton").addEventListener("click", addGrace);
  const instruments = [...$("partChoice").querySelectorAll("optgroup option")];
  $("blankClef").append(...instruments.filter(option => option.value !== "piano").map(option => new Option(option.text, option.value)));
  $("pages").addEventListener("pointerdown", startDrag);
  window.addEventListener("pointermove", moveDrag);
  window.addEventListener("pointerup", endDrag);
  $("audio").addEventListener("click", saveAudio);
  $("dotButton").addEventListener("click", chooseDot);
  $("tripletButton").addEventListener("click", () => toggleTuplet());
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
    const choice = event.target.value;
    if (choice.startsWith("pickup:")) barAction("pickup", { count: Number(choice.slice("pickup:".length)) });
    else if (choice) {
      const [beats, beat_type] = choice.split("/").map(Number);
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
    else if (event.key === "ArrowDown") { event.preventDefault(); changeVerse(1); }
    else if (event.key === "ArrowUp") { event.preventDefault(); changeVerse(-1); }
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
const MARK_KEYS = { S: "staccato", V: "accent", N: "tenuto", O: "marcato" };

function handleControlKey(event) {
  const key = event.key.toLowerCase();
  if (event.altKey && /^[1-4]$/.test(key)) { event.preventDefault(); chooseVoice(Number(key)); return; }
  const actions = {
    z: () => (event.shiftKey ? redoEdit() : undoEdit()),
    y: redoEdit,
    c: copySelection,
    x: cutSelection,
    v: pasteHere,
    a: selectEverything,
    l: startLyrics,
    3: () => toggleTuplet(3),
    5: () => toggleTuplet(5),
    6: () => toggleTuplet(6),
    7: () => toggleTuplet(7),
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
  if (MARK_KEYS[key]) { handled(); markAction("toggle", { name: MARK_KEYS[key] }); return; }
  if (key === "s") { handled(); markAction("slur"); return; }
  if (key === "<") { handled(); markAction("hairpin", { name: "crescendo" }); return; }
  if (key === ">") { handled(); markAction("hairpin", { name: "diminuendo" }); return; }
  if (key === "/") { handled(); addGrace(); return; }
  if (key === "r") { handled(); repeatSelection(); return; }
  if (key === "j") { handled(); noteAction("respell"); return; }
  if (key === "x") { handled(); noteAction("flip"); return; }
  if (key === "Enter" && !editor.on) { handled(); barAction("break"); return; }
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

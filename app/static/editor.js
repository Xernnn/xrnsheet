// Note entry, selection, the note input bar and the keys. This loads last and starts the page.
// In the order the note input bar shows them: shortest first.
const LENGTHS = [
  { key: "1", type: "64th", label: "64th note", glyph: "E1DD" },
  { key: "2", type: "32nd", label: "32nd note", glyph: "E1DB" },
  { key: "3", type: "16th", label: "16th note", glyph: "E1D9" },
  { key: "4", type: "eighth", label: "Eighth note", glyph: "E1D7" },
  { key: "5", type: "quarter", label: "Quarter note", glyph: "E1D5" },
  { key: "6", type: "half", label: "Half note", glyph: "E1D3" },
  { key: "7", type: "whole", label: "Whole note", glyph: "E1D2" },
];
const AFTER = "after:"; // A cursor past the last note of the score.
const GROUP_ACTIONS = new Set(["up", "down", "octaveUp", "octaveDown", "semitoneUp", "semitoneDown", "doubleSharp", "doubleFlat", "sharp", "flat", "natural", "delete", "clear", "respell", "flip"]);
const REACH_IN_STEPS = 10; // How far off a staff a click still counts as on it.
const EVENTS = "g.note, g.rest, g.mRest";
const VOICES = 4;
const MOST_VERSES = 6;
const CHORD_WINDOW_MS = 40; // Keys of a MIDI keyboard pressed this close together are one chord.
const DRAG_START_PX = 4;
const CURVE_REACH_PX = 6; // A slur is hair-thin, so a click this close to it counts.
const CURVE_SAMPLE_PX = 4;
const MARK_NAMES = {
  dynamic: "Dynamic", tempo: "Tempo", text: "Text", jump: "Jump", rehearsal: "Rehearsal mark", "repeat-sign": "Sign", slur: "Slur", hairpin: "Hairpin", ottava: "Ottava", pedal: "Pedal",
  textline: "Line", fingering: "Fingering", pluck: "Right-hand finger", string: "String number", bend: "Bend", hammer: "Hammer-on", pull: "Pull-off",
  slide: "Slide", glissando: "Glissando", vibrato: "Vibrato", harmonic: "Harmonic", upbow: "Up-bow", downbow: "Down-bow", open: "Open string", stopped: "Stopped",
  staccato: "Staccato", staccatissimo: "Staccatissimo", tenuto: "Tenuto", accent: "Accent", marcato: "Marcato",
};
// Words a mark is known by, where its kind alone doesn't say: a line's, or a position's.
const MARK_WORDS = [[/^P\.M\.$/, "Palm mute"], [/^let ring$/, "Let ring"], [/^sul (pont\.|tasto)$/, "Sul ponticello or tasto"], [/^sul /, "String to play on"], [/ pos\.$/, "Position"], [/^(pizz\.|arco)$/, "Pizzicato or arco"], [/^[RL]\.H\.$/, "Hand"]];
const EXPRESSIONS = { staff: "Text, e.g. solo", expression: "Expression, e.g. dolce" };
// While entering notes, `tab` says the cursor is on a tablature staff, at `string`.
const editor = { on: false, type: "quarter", dots: 0, voice: 1, cursor: null, last: null, trail: [], future: [], tab: false, string: 1 };
const FRET_PAUSE_MS = 600; // A second digit typed this soon after the first makes a two-digit fret.
let fretTyping = null; // A first digit waiting to see whether a second one follows.
let range = null; // A selected stretch: { first, last, ids }.
let selectedMark = null; // The id of a selected dynamic, text, slur or other mark.
let pan = null; // The pages being dragged about.
let clipboard = readClipboard();
let lyricNote = null;
let lyricSoFar = "";
let lyricVerse = 1;
let hint = "";
let dragged = false;
let drag = null;
let pointer = null; // Where the mouse is over the pages, so the ghost note is drawn again when they are.
let ghostSpot = null; // Where the ghost note is, and so what a click writes: nothing while none shows.
let lastMidiKey = 0;
let midiAsked = false;

function plainId(id) {
  return id?.startsWith(AFTER) ? id.slice(AFTER.length) : id;
}

// Naming a note before it's written, as the server will: the staff's top line, the key, an 8va
// over it and any sharp or flat earlier in the bar (see places in pipeline/timeline.py).
const STEP_LETTERS = "CDEFGAB";
const SHARPS_IN_ORDER = "FCGDAEB";
const STEP_SEMITONES = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };

function keyAlter(step, fifths) {
  const at = SHARPS_IN_ORDER.indexOf(step);
  if (at < 0) return 0;
  return fifths > 0 && at < fifths ? 1 : fifths < 0 && at >= 7 + fifths ? -1 : 0;
}

function fifthsAt(element) {
  const part = partOfStaff(staffNumber(element)) ?? 0;
  const index = score?.places?.[ownId(element.id)]?.[0] ?? measureIndexOf(element);
  return score?.timeline?.keys?.[part]?.[index] ?? 0;
}

function pitchText(step, alter, octave) {
  return `${step}${ALTERATIONS[alter] ?? ""}${octave}`;
}

// What a plain note means at an event: the key's sharp or flat, or one written earlier in the bar.
function alterInForce(element, step, octave) {
  let alter = keyAlter(step, fifthsAt(element));
  const places = score?.places ?? {};
  const at = places[element.id]?.[1] ?? Infinity;
  const staff = staffNumber(element);
  const earlier = [...(element.closest("g.measure")?.querySelectorAll("g.note") ?? [])]
    .filter(note => staffNumber(note) === staff && (places[note.id]?.[1] ?? Infinity) < at)
    .sort((first, second) => places[first.id][1] - places[second.id][1]);
  for (const note of earlier) {
    const [noteStep, noteAlter, noteOctave] = score.notes?.[note.id] ?? [];
    if (noteStep === step && noteOctave === octave) alter = noteAlter;
  }
  return alter;
}

// The note a place on the staff is at an event, counted in steps down from the top line.
function pitchAtLine(element, line) {
  const place = score?.places?.[element?.id];
  if (!place || place[2] === null || place[2] === undefined) return null;
  const height = place[2] - line + 7 * place[3];
  const step = STEP_LETTERS[((height % 7) + 7) % 7], octave = Math.floor(height / 7);
  const alter = alterInForce(element, step, octave);
  return { step, alter, octave, name: pitchText(step, alter, octave), midi: 12 * (octave + 1) + STEP_SEMITONES[step] + alter };
}

const BEAT_FRACTIONS = { 0.25: "¼", 0.5: "½", 0.75: "¾" };

// Where an event is in time, as a player counts it: its bar and beat.
function whenOf(element) {
  const place = score?.places?.[ownId(element?.id)];
  if (!place) return "";
  const number = measureFacts(place[0]).number;
  const beat = place[4];
  if (!beat) return `bar ${number}`;
  const whole = Math.floor(beat + EARLY), part = Math.round((beat - whole) * 100) / 100;
  return `bar ${number}, beat ${whole}${part ? BEAT_FRACTIONS[part] ?? `.${String(part).slice(2)}` : ""}`;
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
  keepMixer(data);
  score = data;
  if (data.written) editor.last = data.written;
  if (data.type) { editor.type = data.type; editor.dots = data.dots ?? 0; }
  if (data.cursor) {
    if (editor.on) editor.cursor = data.cursor;
    else selected = data.cursor.startsWith(AFTER) ? data.written : data.cursor;
  }
  message(data.message);
  draw();
  // The playback position stays with the selection, or the cursor, through every edit and undo.
  const at = positionTarget();
  if (at) playFromNote(at);
  refreshTile();
}

// Where the playback position follows: the cursor while writing, otherwise the selection.
function positionTarget() {
  if (editor.on) return plainId(editor.cursor);
  return ownId(range?.first ?? selected) ?? null;
}

// Switch off what needs a note while there's none to act on. Palettes call this when they're redrawn.
function enableTools() {
  const target = score ? actionTarget() : null;
  document.querySelectorAll("[data-needs]").forEach(control => { control.disabled = !target; });
  document.querySelectorAll("#voices button").forEach(button => { button.disabled = !editor.on && !target; });
}

// What the note input bar shows pressed: while writing, what the next note will be; otherwise what
// the selected notes are, so the bar and the inspector always say the same.
function toolState() {
  const ids = range ? range.ids : [actionTarget()].filter(Boolean);
  const elements = ids.map(id => document.getElementById(id)).filter(Boolean);
  const details = elements.map(element => score?.notes?.[ownId(element.id)] ?? []);
  const same = values => (values.length && values.every(value => value === values[0]) ? values[0] : undefined);
  const notes = elements.filter(element => element.matches("g.note"));
  const ties = new Set(score?.ties ?? []);
  return {
    type: editor.on ? editor.type : same(details.map(found => found[3] || null)),
    dots: editor.on ? editor.dots : same(details.map(found => found[4] ?? 0)),
    voice: editor.on ? editor.voice : same(elements.map(element => layerNumber(element) + 1)),
    accidental: notes.length === elements.length ? same(notes.map(element => {
      const [step, alter] = score?.notes?.[ownId(element.id)] ?? [];
      return alter || (keyAlter(step, fifthsAt(element)) ? 0 : null); // A natural only where the key would have it otherwise.
    })) : undefined,
    rest: elements.length > 0 && notes.length === 0,
    tie: notes.length > 0 && notes.every(element => ties.has(ownId(element.id))),
    tuplet: elements.length > 0 && elements.every(element => element.closest("g.tuplet")),
  };
}

const ACCIDENTAL_ALTERS = { doubleFlat: -2, flat: -1, natural: 0, sharp: 1, doubleSharp: 2 };

function updateTools() {
  enableTools();
  const state = toolState();
  document.querySelectorAll("#lengths button").forEach(button => {
    button.setAttribute("aria-pressed", String(button.dataset.type === state.type));
  });
  $("dotButton").setAttribute("aria-pressed", String(state.dots > 0));
  document.querySelectorAll("#voices button").forEach(button => {
    button.setAttribute("aria-pressed", String(Number(button.dataset.voice) === state.voice));
  });
  document.querySelectorAll("#noteBar [data-action]").forEach(button => {
    const pressed = button.dataset.action in ACCIDENTAL_ALTERS ? state.accidental === ACCIDENTAL_ALTERS[button.dataset.action] : button.dataset.action === "tie" ? state.tie : false;
    button.setAttribute("aria-pressed", String(pressed));
  });
  $("restButton").setAttribute("aria-pressed", String(!editor.on && state.rest));
  $("tripletButton").setAttribute("aria-pressed", String(!editor.on && state.tuplet));
  $("inputToggle").setAttribute("aria-pressed", String(editor.on));
  $("inputLabel").textContent = editor.on ? "Writing" : "Write notes";
  $("caret").dataset.voice = editor.voice;
  const under = editor.on && !editor.cursor?.startsWith(AFTER) ? document.getElementById(plainId(editor.cursor) ?? "") : null;
  const over = under?.matches("g.note") ? (under.closest("g.chord") ? [...under.closest("g.chord").querySelectorAll("g.note")] : [under]).map(spelledName).join(" ") : "";
  $("caret").classList.toggle("over", Boolean(over));
  $("caret").dataset.over = over ? `writes over ${over}` : "";
  const selection = range ? `${range.ids.length} notes selected. Copy them, or use an action.` : tabSelected() ? "Selected on the tablature. Type a fret, Ctrl+↑ ↓ for another string, or Shift and a digit for a length." : selected ? "Selected. Drag it up or down, press a key below, or choose a duration. Shift-click selects a stretch." : "Click a note or rest, or press N to write notes.";
  const mark = selectedMark && score?.marks.find(item => item.id === selectedMark);
  const said = mark?.text ? `: ${mark.text}`.replace(/\.$/, "") : "";
  const chord = selectedChord !== null && score?.chords?.[selectedChord];
  const chosen = mark ? `${markName(mark)}${said}. Press Delete to remove it${markEditable(mark) ? ", or double-click to change it" : ""}.` : chord ? `Chord ${chord.text}. Type another, or change how it's played in the inspector.` : selection;
  const practising = currentMode() === "practice" ? "Space plays from the selected note, or with a loop set, round it from A. A and B set the loop's ends there, and Hear and Wait for choose a part or a hand." : null;
  const entering = tabInput() ? "Writing on the tablature. Type a fret for the string the cursor is on, ↑ ↓ for another string, Shift and a digit for a length, or click a string. Esc stops." : over ? `Writing over ${over} and what follows it, from the cursor on. Click the staff, type A to G or play below. Click a note to select it. Esc stops.` : "Writing. Click the staff, type A to G, 0 for a rest, or play below. Click a note to select it. Esc stops.";
  $("toolLabel").textContent = hint || (editor.on ? entering : practising ?? chosen);
  hint = "";
  drawInspector();
  lightKeys();
  markTimeline();
  if (dockOpen("instrument")) $("trayNote").textContent = trayNote();
  $("trayNote").classList.toggle("writing", editor.on && !practice.follower);
  // Note input turned on, or another length or voice chosen, changes the ghost under a still pointer.
  redrawGhost();
}

// A tablature staff's lines are drawn in pieces, broken round each fret number on them, so how far
// the staff reaches across is taken from every piece: the first alone stopped at the first fret
// number, and a click or the ghost past it found no staff there.
function staffLines(staff) {
  const lines = [...staff.children].filter(child => child.tagName === "path").map(line => line.getBoundingClientRect());
  const top = Math.min(...lines.map(line => line.top)), bottom = Math.max(...lines.map(line => line.top));
  return { left: Math.min(...lines.map(line => line.left)), right: Math.max(...lines.map(line => line.right)), top, bottom, step: (bottom - top) / 8 };
}

// A box around a selected stretch on each staff it crosses, as MuseScore draws one. Where every
// note of a measure is selected the box takes in the whole measure.
function drawRangeBoxes() {
  document.querySelectorAll(".rangeBox").forEach(box => box.remove());
  if (!range || editor.on) return;
  const frame = $("pagesFrame").getBoundingClientRect();
  const chosen = new Set(range.ids);
  const staves = new Map();
  const boxes = new Map();
  for (const id of range.ids) {
    const element = document.getElementById(id);
    const staff = element?.closest("g.staff");
    if (!staff) continue;
    if (!staves.has(staff)) {
      const events = [...staff.querySelectorAll(EVENTS)];
      staves.set(staff, { lines: staffLines(staff), whole: events.every(event => chosen.has(event.id)) });
    }
    const { lines, whole } = staves.get(staff);
    const head = headBox(element);
    const key = `${[...document.querySelectorAll("#pages g.system")].indexOf(staff.closest("g.system"))}/${staffNumber(element)}`;
    const box = boxes.get(key) ?? { left: Infinity, right: -Infinity, top: lines.top - lines.step * 2, bottom: lines.bottom + lines.step * 2 };
    box.left = Math.min(box.left, whole ? lines.left : head.left - 4);
    box.right = Math.max(box.right, whole ? lines.right : head.right + 4);
    boxes.set(key, box);
  }
  for (const box of boxes.values()) {
    const shown = document.createElement("div");
    shown.className = "rangeBox";
    shown.style.left = `${box.left - frame.left}px`;
    shown.style.top = `${box.top - frame.top}px`;
    shown.style.width = `${box.right - box.left}px`;
    shown.style.height = `${box.bottom - box.top}px`;
    $("pagesFrame").append(shown);
  }
}

function drawCursor() {
  forgetStaves(); // Called whenever the pages are drawn or move on the desk.
  drawRangeBoxes();
  drawLoopMarkers();
  const caret = $("caret");
  if (!editor.on) { caret.hidden = true; return; }
  // While writing, the note just written, or one clicked, is the selection the tools act on.
  document.querySelectorAll("#pages g.selected").forEach(element => element.classList.remove("selected"));
  const last = editor.last && document.getElementById(editor.last);
  if (last) markSelected(last);
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
  // On tablature the cursor is a box on the string a fret number typed goes on, as in MuseScore.
  const tab = tabInput() ? tabStaffFor(element) : null;
  const strings = tab ? stringLines(tab) : null;
  if (strings) editor.string = Math.min(Math.max(editor.string, 1), strings.count);
  const top = strings ? strings.top + (editor.string - 1.6) * strings.gap : lines.top - 6;
  caret.hidden = false;
  caret.classList.toggle("onTab", Boolean(strings));
  caret.style.left = `${(past ? box.right + 6 : box.left - 3) - frame.left + $("pagesFrame").scrollLeft}px`;
  caret.style.top = `${top - frame.top}px`;
  caret.style.width = `${past ? 10 : Math.max(box.width, 10) + 6}px`;
  caret.style.height = `${strings ? strings.gap * 1.2 : lines.bottom - lines.top + 12}px`;
  element.scrollIntoView({ block: "nearest", inline: "nearest" });
}

// Whether notes are being entered on a tablature staff: one of its own, or one linked under the
// standard staff that was clicked.
function tabInput() {
  if (!editor.on) return false;
  const element = document.getElementById(plainId(editor.cursor) ?? "");
  return Boolean(element) && (onTab(element) || (editor.tab && Boolean(tabStaffFor(element))));
}

function tabSelected() {
  return !editor.on && !range && onTab(document.getElementById(selected ?? ""));
}

// A digit typed for a fret. One that could begin a two-digit fret waits a moment for the next;
// `send` makes the edit with the fret, and `key` says what it's typed for.
function typeFret(digit, key, most, send) {
  if (fretTyping?.key === key && Number(fretTyping.digits + digit) <= most) {
    clearTimeout(fretTyping.timer);
    const fret = Number(fretTyping.digits + digit);
    fretTyping = null;
    send(fret);
    return;
  }
  flushFret();
  if (digit !== "0" && Number(digit) * 10 <= most) {
    fretTyping = { key, digits: digit, send, timer: setTimeout(flushFret, FRET_PAUSE_MS) };
    $("toolLabel").textContent = `Fret ${digit}. Type another digit at once for fret ${digit}0 and up.`;
    return;
  }
  send(Number(digit));
}

function flushFret() {
  if (!fretTyping) return;
  const { digits, send, timer } = fretTyping;
  clearTimeout(timer);
  fretTyping = null;
  send(Number(digits));
}

// A fret typed while entering notes on tablature writes that note on the cursor's string.
function writeFret(fret) {
  return enqueue(async () => {
    const element = document.getElementById(plainId(editor.cursor) ?? "");
    const part = element ? partOfStaff(staffNumber(element)) : null;
    const board = part === null ? null : frettedOf(part);
    if (!board) return;
    if (fret > board.frets) { message(`This instrument's frets go from 0 to ${board.frets}.`); return; }
    await send(`notes/${editor.cursor}`, { action: "write", type: editor.type, dots: editor.dots, midi: pitchAtFret(part, editor.string, fret), voice: editor.voice, string: editor.string });
  });
}

// The keys of tablature: digits are frets, Shift and a digit a length, and while entering notes
// the arrows go from string to string. Say whether the key was one.
function tabKey(event) {
  const key = event.key;
  const input = tabInput();
  if (!input && !tabSelected()) return false;
  const length = /^Digit([0-7])$/.exec(event.code);
  if (event.shiftKey && length) {
    event.preventDefault();
    if (length[1] === "0") { if (input) writeAt(null); else noteAction("delete"); } else chooseLength(LENGTHS[Number(length[1]) - 1].type);
    return true;
  }
  if (event.shiftKey || event.altKey) return false;
  const element = document.getElementById(plainId(input ? editor.cursor : selected) ?? "");
  const board = frettedOf(partOfStaff(staffNumber(element)));
  if (/^[0-9]$/.test(key)) {
    event.preventDefault();
    if (input) typeFret(key, "input", board?.frets ?? 24, writeFret);
    else {
      const target = selected;
      typeFret(key, target, board?.frets ?? 24, fret => enqueue(() => send(`notes/${target}`, { action: "fret", fret })));
    }
    return true;
  }
  if (input && (key === "ArrowUp" || key === "ArrowDown")) {
    event.preventDefault();
    editor.string = Math.min(Math.max(editor.string + (key === "ArrowUp" ? -1 : 1), 1), board?.strings?.length ?? 6);
    drawCursor();
    return true;
  }
  return false;
}

// Ctrl+↑ and ↓ move a fret number to the string above or below, keeping its sound, as in MuseScore.
function moveToString(by) {
  return enqueue(async () => {
    const played = playedAs(selected);
    if (!played.string) { message("No string plays this note yet."); return; }
    await applyToNote("string", { string: played.string + by });
  });
}

function drawRange() {
  if (!range) return;
  range.ids = range.ids.filter(id => document.getElementById(id));
  if (!document.getElementById(range.first) || !document.getElementById(range.last)) { range = null; return; }
  range.ids.forEach(id => markSelected(document.getElementById(id)));
}

// Measures that don't add up, notes no string can play, and notes out of a bowed string's range.
// The bars are one line to step through, below; the bar it's on is lit in the score, and the others
// only faintly marked. The notes' problems are a chip each, that goes there.
let barStep = null; // The bar last stepped to, by its index in the score, or null.

function drawBarChecks() {
  const checks = score.barChecks ?? [];
  $("barChecks").hidden = checks.length === 0 && !(score.fretProblems ?? []).length && !(score.rangeProblems ?? []).length;
  document.querySelectorAll("#pages g.barCheck").forEach(measure => measure.classList.remove("barCheck", "barCheckHere"));
  const measures = document.querySelectorAll("#pages g.measure");
  for (const check of checks) measures[check.measure]?.classList.add("barCheck", ...(check.measure === barStep ? ["barCheckHere"] : []));
  // A step taken from the keyboard keeps its key's focus, though the line is made again.
  const focused = document.activeElement?.closest?.("#barChecks [data-step]")?.dataset.step;
  const line = checks.length ? [stepLine("barSteps", "Bars that don't add up", checks, checks.map(check => check.measure), barStep, {
    said: check => `Bar ${measureFacts(check.measure).number}: ${check.problem === "long" ? "too many beats" : "too few beats"}`,
    many: `${checks.length} bars don't add up`,
    why: "The beats don't add up to the time signature, which is often a rhythm misread.",
    before: "The bar before that doesn't add up",
    next: "The next bar that doesn't add up",
    go: stepToBarCheck,
  })] : [];
  $("barChecks").replaceChildren(...line, ...fretChips());
  if (focused) $("barChecks").querySelector(`[data-step="${focused}"]`)?.focus();
}

// Bars that don't add up, and chords the reader wasn't sure of, are each one line: how many there
// are, or the one stepped to and where it is among them, with ‹ and › to step from one to the next,
// as a browser's find does, the last coming round to the first. A chip for each was a wall: a real
// handwritten page had twenty-one bars, four rows deep over the music, and thirteen chords beside it.
// Each is known by its key, its bar or its chord's number, so once the one stepped to is put right
// the line says how many are left, and the steps go on from where it was.
function stepLine(id, label, items, keys, step, { said, many, why, before, next, go }) {
  const at = step === null ? -1 : keys.indexOf(step);
  const after = keys.findIndex(key => step === null || key > step);
  const earlier = keys.findLastIndex(key => step === null || key < step);
  const group = document.createElement("span");
  group.id = id;
  group.className = "steps";
  group.setAttribute("role", "group");
  group.setAttribute("aria-label", label);
  const button = (name, text, title, item) => {
    const element = document.createElement("button");
    element.className = "chip";
    element.dataset.step = name;
    element.textContent = text;
    element.title = title;
    if (name !== "here") element.setAttribute("aria-label", title);
    // A click lets go of the focus, leaving the keys to the score; a step pressed from the keyboard keeps it.
    element.addEventListener("click", event => { if (event.detail) element.blur(); go(item); });
    return element;
  };
  if (items.length === 1) {
    group.append(button("here", said(items[0]), `${why} Click to go to it.`, items[0]));
    return group;
  }
  const onward = items[after < 0 ? 0 : after];
  group.append(
    button("here", at < 0 ? many : `${said(items[at])} · ${at + 1} of ${items.length}`, `${why} Click to go to ${at < 0 ? "one" : "it"}, then step through them with ‹ and ›.`, at < 0 ? onward : items[at]),
    button("back", "‹", before, items[earlier < 0 ? items.length - 1 : earlier]),
    button("on", "›", next, onward),
  );
  return group;
}

function stepToBarCheck(check) {
  barStep = check.measure;
  return enqueue(async () => {
    if (editor.on) moveCursor(check.note); else select(check.note);
    drawBarChecks();
    document.getElementById(check.note)?.scrollIntoView({ block: "center" });
  });
}

// A file saved from the score is named after its title, as the server names the ones it sends, so
// each score's files can be told apart in a folder: "Maple Leaf Rag.wav", or "score.wav" untitled.
function savedName(extension) {
  const title = (score?.title ?? "").replace(/[\\/:*?"<>|\u0000-\u001f]+/g, " ").split(/\s+/).join(" ").slice(0, 100).replace(/^[\s.]+|[\s.]+$/g, "");
  return `${title || "score"}${extension}`;
}

function drawTitle() {
  $("scoreName").textContent = score.title || "Untitled score";
  $("scoreName").title = `${[score.title || "Untitled score", score.composer].filter(Boolean).join(", ")}. Click to change the title and composer`;
  fitTopBar();
  document.title = score.title ? `${score.title} - xrnsheet` : "xrnsheet";
  $("pdfSave").download = savedName(".pdf");
  const tile = document.querySelector(`#historyList a[data-score="${scoreId}"] .name`);
  if (tile) tile.textContent = score.title || "Untitled score";
  // The inspector's fields, unless they're being typed in.
  if (document.activeElement !== $("scoreTitle")) $("scoreTitle").value = score.title ?? "";
  if (document.activeElement !== $("scoreComposer")) $("scoreComposer").value = score.composer ?? "";
}

function moveCursor(id) {
  editor.cursor = id;
  editor.last = null;
  playFromNote(plainId(id));
  drawCursor();
  updateTools();
}

// The place after an event in its voice, or past the end of the staff after the last one.
function placeAfter(id) {
  const element = stepOf(document.getElementById(plainId(id) ?? ""));
  if (!element) return null;
  const list = eventsInVoice(staffNumber(element), layerNumber(element));
  const next = list[list.indexOf(element) + 1];
  return next ? next.id : AFTER + list.at(-1).id;
}

// Where writing starts. On a selected rest, which is where the note goes. After a selected note or
// stretch that ends the music in its voice, so writing carries on from it rather than writing over
// it. On one in the middle of the music, as MuseScore does, to write the passage again from there or
// a second voice under it: the cursor is drawn there as writing over that note, and the hint says
// so. With nothing selected, after the last note of the instrument's staff.
function startingPlace() {
  const chosen = stepOf(document.getElementById(plainId(range?.first ?? selected) ?? ""));
  if (chosen) {
    if (chosen.matches("g.rest, g.mRest")) return chosen.id;
    const last = stepOf(document.getElementById(range?.last ?? chosen.id)) ?? chosen;
    const list = eventsInVoice(staffNumber(last), layerNumber(last));
    const more = list.slice(list.indexOf(last) + 1).some(element => element.matches("g.note"));
    return more ? chosen.id : placeAfter(last.id);
  }
  const staff = firstStaffOf(activePart());
  const list = eventsInVoice(staff, 0);
  const last = list.findLastIndex(element => element.matches("g.note"));
  if (last < 0) return list[0]?.id ?? allEvents()[0]?.id ?? null;
  return list[last + 1]?.id ?? AFTER + list[last].id;
}

function setInput(on) {
  if (on === editor.on) return;
  // Note input is only of use in Write, where the staff takes clicks and letters. Turned on from
  // Read or Practice, by a new score, the menu or the command palette, it goes there too, as N does,
  // or it would show its blue edge and ghost note while nothing could be written.
  if (on && !writing()) setMode("write");
  editor.on = on;
  document.body.classList.toggle("inputting", on);
  if (on) {
    setUpMidi();
    // A note selected on the tablature starts note input there, on its string.
    editor.tab = onTab(document.getElementById(selected ?? ""));
    editor.string = (editor.tab && playedAs(selected).string) || editor.string;
    if (editor.tab) selected = ownId(selected);
    editor.cursor = startingPlace();
    editor.voice = editor.cursor ? layerNumber(document.getElementById(plainId(editor.cursor))) + 1 : 1;
    editor.last = null;
    editor.trail = [];
    editor.future = [];
    select(null);
    playFromNote(plainId(editor.cursor));
  } else {
    const keep = editor.last ?? plainId(editor.cursor);
    editor.cursor = editor.last = null;
    hideGhost();
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

// The staff and place a click lands on, while entering notes. A tablature staff's lines are strings,
// not pitches, so a click there is a string, and its place in time is read from the notes and rests
// of the staff it's linked under.
// Where each staff's lines are, measured once a drawing, a zoom or the window changes rather than at
// every move of the mouse: a long score has hundreds of staves, and the ghost note is drawn at each
// move. They're kept from the pages' own corner, which scrolling doesn't move.
let staffCache = null;

function forgetStaves() {
  staffCache = null;
}

// The staves whose lines are within some steps of a point, with their lines where they are now.
function stavesNear(x, y, steps) {
  const frame = $("pagesFrame").getBoundingClientRect();
  staffCache ??= [...document.querySelectorAll("#pages g.staff")].filter(staff => [...staff.children].some(child => child.tagName === "path")).map(staff => {
    const lines = staffLines(staff);
    return { staff, left: lines.left - frame.left, right: lines.right - frame.left, top: lines.top - frame.top, bottom: lines.bottom - frame.top, step: lines.step };
  });
  const found = [];
  for (const known of staffCache) {
    const left = known.left + frame.left, right = known.right + frame.left, top = known.top + frame.top, bottom = known.bottom + frame.top;
    if (x < left - HEAD_REACH_PX || x > right + HEAD_REACH_PX || y < top - known.step * steps || y > bottom + known.step * steps) continue;
    found.push({ staff: known.staff, lines: { left, right, top, bottom, step: known.step } });
  }
  return found;
}

function placeUnder(x, y) {
  let found = null;
  let smallest = Infinity;
  for (const { staff, lines } of stavesNear(x, y, REACH_IN_STEPS)) {
    const tab = isTabStaff(staffNumber(staff));
    if (x < lines.left || x > lines.right) continue;
    const gap = Math.max(lines.top - y, y - lines.bottom, 0);
    if (gap <= lines.step * (tab ? 2 : REACH_IN_STEPS) && gap < smallest) { found = { staff, lines, tab }; smallest = gap; }
  }
  if (!found) return null;
  let target = null;
  let latest = -Infinity;
  const events = found.tab ? eventsBeside(found.staff) : [...found.staff.querySelectorAll(EVENTS)];
  for (const element of events) {
    const left = headBox(element).left;
    if (left - 8 <= x && left > latest) { target = element; latest = left; }
  }
  target ??= events[0];
  if (!target) return null;
  const box = headBox(target);
  const spot = { id: target.id, line: Math.round((y - found.lines.top) / found.lines.step), lines: found.lines, x: box.left + box.width / 2, isNote: target.matches("g.note") };
  if (!found.tab) return spot;
  const strings = stringLines(found.staff);
  const string = Math.min(Math.max(Math.round((y - strings.top) / strings.gap) + 1, 1), strings.count);
  return { ...spot, tab: true, string, part: strings.part, gap: strings.gap, y: strings.top + (string - 1) * strings.gap };
}

// A ghost note is drawn from the music font's own pieces: its notehead, a stem and a flag, up or down
// as the staff would have it, as the engraved page has them.
const GHOST_HEADS = { whole: "E0A2", half: "E0A3", other: "E0A4" };
const GHOST_FLAGS = { eighth: ["E240", "E241"], "16th": ["E242", "E243"], "32nd": ["E244", "E245"], "64th": ["E246", "E247"] };
const STEM_UNITS = 875, STEM_WIDTH_UNITS = 30, STEM_FROM_UNITS = 42; // 3.5 spaces long, as the font counts them.

// The note's outline in the font's units, with y going down as SVG has it, and its box.
function ghostNote(type, up) {
  const head = setGlyphs(GHOST_HEADS[type] ?? GHOST_HEADS.other);
  const box = { left: head.left, right: head.right, top: head.top, bottom: head.bottom };
  let paths = head.paths;
  if (type !== "whole") {
    const x = up ? head.right - STEM_WIDTH_UNITS : head.left;
    paths += up ? `<rect x="${x}" y="${-STEM_UNITS}" width="${STEM_WIDTH_UNITS}" height="${STEM_UNITS - STEM_FROM_UNITS}"/>` : `<rect x="${x}" y="${STEM_FROM_UNITS}" width="${STEM_WIDTH_UNITS}" height="${STEM_UNITS - STEM_FROM_UNITS}"/>`;
    box.top = Math.max(box.top, up ? STEM_UNITS : 0);
    box.bottom = Math.min(box.bottom, up ? 0 : -STEM_UNITS);
    const flag = GHOST_FLAGS[type] && setGlyphs(GHOST_FLAGS[type][up ? 0 : 1]);
    if (flag) {
      const at = up ? STEM_UNITS : -STEM_UNITS;
      paths += `<g transform="translate(${x} ${-at})">${flag.paths}</g>`;
      box.right = Math.max(box.right, x + flag.right);
      box.top = Math.max(box.top, at + flag.top);
      box.bottom = Math.min(box.bottom, at + flag.bottom);
    }
  }
  return { paths, ...box };
}
const HEAD_REACH_PX = 3; // A press this near a notehead is on it.

// The notehead under the pointer, or very near it: a click there selects the note, and a drag moves it.
function noteheadAt(x, y) {
  let found = null, nearest = Infinity;
  for (const { staff } of stavesNear(x, y, 14)) {
    for (const note of staff.querySelectorAll("g.note")) {
      const box = headBox(note);
      if (x < box.left - HEAD_REACH_PX || x > box.right + HEAD_REACH_PX || y < box.top - HEAD_REACH_PX || y > box.bottom + HEAD_REACH_PX) continue;
      const away = Math.hypot(x - (box.left + box.right) / 2, y - (box.top + box.bottom) / 2);
      if (away < nearest) { found = note; nearest = away; }
    }
  }
  return found;
}

function hideGhost() {
  $("ghost").hidden = true;
  ghostSpot = null;
  document.querySelectorAll("#pages .replacing, #pages .picked").forEach(element => element.classList.remove("replacing", "picked"));
}

// Draw a note that isn't written yet: where a click will write it, or where a dragged note is going.
// It's in the length and the voice's colour it will have, with the ledger lines it needs, and says
// its name, when it falls in time and what it replaces.
function paintGhost({ lines, line, x, type, dots = 0, voice, label, band = null }) {
  const ghost = $("ghost");
  const frame = $("pagesFrame").getBoundingClientRect();
  const space = lines.step * 2, scale = space / UNITS_PER_SPACE;
  const head = GLYPHS[GHOST_HEADS[type] ?? GHOST_HEADS.other];
  const headWidth = (head ? head[3] : 295) * scale;
  const glyph = ghostNote(type, line >= 4);
  const originX = x - headWidth / 2, originY = lines.top + line * lines.step;
  ghost.className = "";
  ghost.dataset.voice = voice;
  ghost.style.left = `${originX - frame.left}px`;
  ghost.style.top = `${originY - space / 2 - frame.top}px`;
  ghost.style.width = `${headWidth}px`;
  ghost.style.height = `${space}px`;
  const ledgers = [];
  for (let at = -2; at >= line; at -= 2) ledgers.push(at);
  for (let at = 10; at <= line; at += 2) ledgers.push(at);
  const thick = Math.max(1, space * 0.16);
  const dotted = dots ? `<span class="dot" style="left:${headWidth + space * 0.3}px;top:${space / 2 - (line % 2 === 0 ? lines.step : 0) - space * 0.2}px;width:${space * 0.4}px;height:${space * 0.4}px"></span>` : "";
  const width = (glyph.right - glyph.left) * scale, height = (glyph.top - glyph.bottom) * scale;
  ghost.innerHTML = `${band ? `<span class="band" style="left:${band.left - originX}px;width:${band.width}px;top:${lines.top - lines.step * 3 - (originY - space / 2)}px;height:${lines.bottom - lines.top + lines.step * 6}px"></span>` : ""}`
    + ledgers.map(at => `<span class="ledger" style="left:${-space * 0.35}px;width:${headWidth + space * 0.7}px;top:${space / 2 + (at - line) * lines.step - thick / 2}px;height:${thick}px"></span>`).join("")
    + `<svg class="glyphs" style="left:${glyph.left * scale}px;top:${space / 2 - glyph.top * scale}px" width="${width}" height="${height}" viewBox="${glyph.left} ${-glyph.top} ${glyph.right - glyph.left} ${glyph.top - glyph.bottom}" aria-hidden="true">${glyph.paths}</svg>`
    + dotted
    + (label ? `<span class="tag" style="left:${Math.max(glyph.right * scale, headWidth) + space * 0.9}px;top:${space / 2}px"></span>` : "");
  if (label) ghost.querySelector(".tag").textContent = label;
  ghost.hidden = false;
}

// The page can move under a pointer that stays where it is: scrolled by the wheel or the keys,
// zoomed, or with the window resized. What a click there writes moves with it, so the ghost is drawn
// again where the pointer is, or the click would write something other than the ghost shows.
function redrawGhost() {
  if (pointer) drawGhost(pointer.x, pointer.y, pointer.shift);
}

function drawGhost(x, y, shift = false) {
  if (drag) return; // A note being dragged has the ghost.
  hideGhost();
  if (!editor.on || pan?.moved) return;
  // Over a note, a click selects it, so no note is offered to write there.
  const head = shift ? null : noteheadAt(x, y);
  if (head) { head.classList.add("picked"); return; }
  const spot = placeUnder(x, y);
  if (!spot) return;
  const ghost = $("ghost");
  const target = document.getElementById(spot.id);
  if (spot.tab) {
    const frame = $("pagesFrame").getBoundingClientRect();
    const width = spot.gap * 1.3, height = spot.gap * 1.1;
    ghost.className = "onTab";
    ghost.dataset.voice = editor.voice;
    ghost.innerHTML = `<span class="tag" style="left:${width + 6}px;top:${height / 2}px"></span>`;
    ghost.querySelector(".tag").textContent = [`${noteName(pitchAtFret(spot.part, spot.string, 0))}, string ${spot.string} open: type its fret`, whenOf(target)].filter(Boolean).join(" · ");
    Object.assign(ghost.style, { width: `${width}px`, height: `${height}px`, left: `${spot.x - width / 2 - frame.left}px`, top: `${spot.y - height / 2 - frame.top}px` });
    ghost.hidden = false;
    ghostSpot = spot;
    return;
  }
  const pitch = pitchAtLine(target, spot.line);
  const stacking = shift && spot.isNote;
  const voiceHere = target && layerNumber(target) + 1 === editor.voice;
  const replaced = !stacking && voiceHere && target.matches("g.note") ? (target.closest("g.chord") ? [...target.closest("g.chord").querySelectorAll("g.note")] : [target]) : [];
  replaced.forEach(note => note.classList.add("replacing"));
  const names = replaced.map(note => spelledName(note)).filter(Boolean);
  const when = whenOf(target);
  const label = [stacking ? `+ ${pitch?.name ?? ""} to the chord` : pitch?.name, when, names.length ? `replaces ${names.join(" ")}` : ""].filter(Boolean).join(" · ");
  const box = headBox(target);
  paintGhost({ lines: spot.lines, line: spot.line, x: spot.x, type: editor.type, dots: editor.dots, voice: editor.voice, label, band: { left: box.left - 3, width: box.width + 6 } });
  ghostSpot = spot;
}

// A note clicked while writing becomes the one the tools act on, as a note just written is: the
// arrows, a key below, a duration or Delete change it, and a drag moves it. The cursor stays where
// writing goes on.
function pickWhileWriting(note) {
  return enqueue(async () => {
    editor.last = note.id;
    hearNote(note);
    drawCursor();
    updateTools();
  });
}

// Sound a note or chord that's been chosen, so it can be heard as well as seen, as MuseScore does.
function hearNote(element) {
  if (!element?.matches("g.note")) return;
  const notes = element.closest("g.chord") ? [...element.closest("g.chord").querySelectorAll("g.note")] : [element];
  const part = partOfStaff(staffNumber(element)) ?? 0;
  for (const note of notes) {
    const [step, alter, octave] = score?.notes?.[ownId(note.id)] ?? [];
    if (step) hearKey(soundingPitch(12 * (octave + 1) + STEP_SEMITONES[step] + alter, part), part);
  }
}

// A click on a staff writes the note there. On tablature it's the open string clicked, and the
// fret is typed next.
function placeNote(spot, stack) {
  editor.tab = Boolean(spot.tab);
  if (spot.tab) editor.string = spot.string;
  updateTools();
  return enqueue(async () => {
    const pitch = spot.tab ? { midi: pitchAtFret(spot.part, spot.string, 0), string: spot.string } : { line: spot.line };
    if (stack && spot.isNote) await send(`notes/${spot.id}`, { action: "stack", ...pitch });
    else await send(`notes/${spot.id}`, { action: "write", type: editor.type, dots: editor.dots, voice: editor.voice, ...pitch });
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

// The measure's staff a click landed on, if it wasn't on a note.
function staffUnder(x, y) {
  for (const staff of document.querySelectorAll("#pages g.staff")) {
    const lines = staffLines(staff);
    if (x >= lines.left && x <= lines.right && y >= lines.top - lines.step && y <= lines.bottom + lines.step) return staff;
  }
  return null;
}

// Clicking an empty part of a measure selects the whole measure, and with Shift everything up to it.
async function selectMeasure(staff, extend) {
  const events = [...(staff.querySelector("g.layer") ?? staff).querySelectorAll(EVENTS)].filter(element => stepOf(element) === element);
  if (!events.length) { select(null); return; }
  if (!extend || !selected) select(events[0].id);
  await growRange(events.at(-1).id);
}

// Ctrl+Delete takes the selected time out of the score: measures selected from end to end go, and
// anything less closes up. A whole-measure rest is all there is in its measure, so the measure goes.
function removeRange() {
  if (!range && !document.getElementById(actionTarget() ?? "")?.matches("g.mRest")) return noteAction("close");
  return enqueue(async () => {
    const notes = range ? range.ids : [actionTarget()];
    range = null;
    selected = null;
    await send("group", { action: "remove", notes });
  });
}

// Delete every measure the selection touches.
function deleteBars() {
  return enqueue(async () => {
    const body = { action: "delete", note: range?.first ?? actionTarget(), last: range?.last ?? null };
    range = null;
    selected = null;
    await send("bars", body);
  });
}

// A mark with words can be retyped. A jump's words are fixed, a hammer-on's H and a pull-off's P
// come with them, and a sign, slur or hairpin has none.
function markEditable(mark) {
  return Boolean(mark?.text) && !["jump", "repeat-sign", "hammer", "pull"].includes(mark.kind);
}

function markName(mark) {
  if (mark.kind === "text" || mark.kind === "textline") return MARK_WORDS.find(([words]) => words.test(mark.text ?? ""))?.[1] ?? MARK_NAMES[mark.kind];
  return MARK_NAMES[mark.kind] ?? "Mark";
}

function selectMark(element) {
  const id = markIdOf(element);
  if (!score.marks.some(item => item.id === id)) return;
  select(null);
  selectedMark = id;
  markElements(id).forEach(piece => piece.classList.add("selected"));
  updateTools();
}

// A slur or hairpin carried over a line break is drawn in pieces, and they're selected together.
function markElements(id) {
  return [...document.querySelectorAll("#pages g[data-mark], #pages g.slur, #pages g.hairpin")].filter(element => markIdOf(element) === id);
}

function removeMark() {
  return enqueue(async () => {
    if (!selectedMark) return;
    const id = selectedMark;
    selectedMark = null;
    await send("marks", { action: "label", id, text: "" });
  });
}

function openSelectedMark() {
  const element = selectedMark && markElements(selectedMark)[0];
  if (element) openMark(element);
}

// Go to a note or rest: move the cursor there, select it, or stretch the selection to it.
async function goTo(element, extend) {
  if (!element) return;
  if (editor.on) moveCursor(element.id);
  else if (extend) await growRange(element.id);
  else select(element.id);
  element.scrollIntoView({ block: "nearest", inline: "nearest" });
}

function moveByMeasure(delta, extend = false) {
  return enqueue(async () => {
    const position = editor.on ? editor.cursor : range?.last ?? selected;
    const current = stepOf(document.getElementById(plainId(position)));
    if (!current) return;
    const measures = [...document.querySelectorAll("#pages g.measure")];
    const here = measures.indexOf(current.closest("g.measure"));
    const staff = staffNumber(current);
    const inMeasure = measure => allEvents().filter(element => element.closest("g.measure") === measure && staffNumber(element) === staff);
    // Back goes to the start of this measure first, then to the one before.
    const atStart = inMeasure(measures[here])[0] === current;
    const target = measures[Math.min(Math.max(here + (delta < 0 && !atStart ? 0 : delta), 0), measures.length - 1)];
    await goTo(inMeasure(target)[0], extend);
  });
}

function moveToEnd(last, extend = false) {
  return enqueue(async () => {
    const position = editor.on ? editor.cursor : range?.last ?? selected;
    const current = stepOf(document.getElementById(plainId(position))) ?? allEvents()[0];
    if (!current) return;
    const list = eventsInVoice(staffNumber(current), layerNumber(current));
    await goTo(last ? list.at(-1) : list[0], extend);
  });
}

// W and Q: twice or half as long. With Shift, one step along quarter, dotted quarter, half.
function scaleLength(longer, byDot) {
  return enqueue(async () => {
    if (!editor.on) {
      await applyToNote(byDot ? (longer ? "dotLonger" : "dotShorter") : (longer ? "double" : "halve"));
      return;
    }
    let index = LENGTHS.findIndex(item => item.type === editor.type);
    let dots = editor.dots;
    if (!byDot) index += longer ? 1 : -1;
    else if (longer) [index, dots] = dots ? [index + 1, 0] : [index, 1];
    else [index, dots] = dots ? [index, 0] : [index - 1, 1];
    if (index < 0 || index >= LENGTHS.length) return;
    editor.type = LENGTHS[index].type;
    editor.dots = dots;
    updateTools();
  });
}

// What a right-click offers: the menus' own actions for whatever is under the pointer.
function contextItems() {
  const mark = selectedMark && score.marks.find(item => item.id === selectedMark);
  if (mark) {
    return [
      { label: "Edit text", keys: "F2", enabled: markEditable(mark), run: openSelectedMark },
      { label: "Delete", keys: "Del", run: removeMark },
    ];
  }
  if (!actionTarget()) {
    return [...menuItems("File", "Title and composer…"), ...menuItems("Format", "Measures per line…"), null, ...menuItems("View", "Zoom in", "Zoom out")];
  }
  return [
    ...menuItems("Edit", "Cut", "Copy", "Paste", "Delete"),
    null,
    ...addItems(),
    null,
    ...menuItems("Tools", "Voices", "Respell pitches", "Repeat selection"),
    null,
    ...menuItems("Edit", "Delete selected measures"),
    ...menuItems("Tools", "Remove selected range"),
  ];
}

// A right-click selects what it's on, unless that's already part of the selection, and opens its menu.
function openContextMenu(event) {
  if (reading()) return;
  event.preventDefault();
  closeEditor();
  closeMenus();
  if (editor.on) return;
  const x = event.clientX, y = event.clientY;
  const mark = markAt(event);
  const note = mark ? null : event.target.closest(EVENTS) || nearest("#pages g.note, #pages g.rest, #pages g.mRest", x, y, 14);
  const staff = mark || note ? null : staffUnder(x, y);
  enqueue(async () => {
    if (mark) selectMark(mark);
    else if (note) { if (note.id !== selected && !range?.ids.includes(note.id)) select(note.id); }
    else if (staff) { if (![...staff.querySelectorAll(EVENTS)].some(element => range?.ids.includes(element.id))) await selectMeasure(staff, false); }
    else select(null);
    openMenuAt(x, y, contextItems());
  });
}

function startPan(event) {
  // A drag that ended off the pages got no click to swallow, and mustn't swallow the next one.
  if (!drag) dragged = false;
  // A press on a note moves the note. Anywhere else on the paper it moves the pages, while writing too.
  if (event.button !== 0 || drag || event.target.closest("input, button")) return;
  const desk = $("desk");
  pan = { x: event.clientX, y: event.clientY, left: desk.scrollLeft, top: desk.scrollTop, inPages: Boolean(event.target.closest("#pages")), moved: false };
}

function movePan(event) {
  if (!pan) return;
  const dx = event.clientX - pan.x, dy = event.clientY - pan.y;
  if (!pan.moved && Math.hypot(dx, dy) < DRAG_START_PX) return;
  pan.moved = true;
  $("desk").scrollLeft = pan.left - dx;
  $("desk").scrollTop = pan.top - dy;
}

// A click follows a drag that ends on the pages, and mustn't select what the pointer happens to be over.
function endPan() {
  if (pan?.moved && pan.inPages) dragged = true;
  pan = null;
}

async function growRange(id) {
  const first = range?.first ?? selected;
  if (!first) { select(id); return; }
  const data = await askServer("range", { first, last: id });
  document.querySelectorAll("#pages g.selected").forEach(element => element.classList.remove("selected"));
  range = { first, last: id, ids: data.ids };
  selected = id;
  playFromNote(first);
  selectedMark = null;
  drawRange();
  drawRangeBoxes();
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
function askServer(path, body) {
  return postJson(`/api/scores/${scoreId}/${path}`, body);
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
  input.blur();
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
// An octave or pedal line carried over a line break goes on in a piece that names the first.
function tagMarks() {
  const groups = new Map();
  for (const mark of score.marks ?? []) {
    if (mark.drawn === "slur" || mark.drawn === "hairpin") continue;
    const key = `${mark.bar}/${mark.drawn}`;
    groups.set(key, [...(groups.get(key) ?? []), mark]);
  }
  for (const [key, marks] of groups) {
    const [bar, drawn] = key.split("/");
    const shown = document.getElementById(bar)?.querySelectorAll(`g.${drawn}:not(.spanning)`) ?? [];
    if (shown.length !== marks.length) continue;
    shown.forEach((element, index) => {
      element.dataset.mark = marks[index].id;
      document.querySelectorAll(`#pages g.spanning.id-${CSS.escape(element.id)}`).forEach(piece => { piece.dataset.mark = marks[index].id; });
    });
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

// A mark drawn on a note, such as a staccato or a bowing, or the diamond of a violin's harmonic, is
// that mark's, not the note's.
function markAt(event) {
  const own = event.target.closest("#pages g.artic[data-mark], #pages g.note[data-mark]");
  if (own) return own;
  if (event.target.closest("g.note, g.rest, g.mRest")) return null;
  return event.target.closest("g[data-mark], g.slur, g.hairpin")
    ?? slurNear(event.clientX, event.clientY)
    ?? nearest("#pages g[data-mark], #pages g.hairpin", event.clientX, event.clientY, 4);
}

// A note pressed and dragged goes up or down the staff, a step at a time, with its new name shown
// where it's going and heard as it gets there, as in MuseScore. It's selected as it's pressed, so
// it's the one that moves, while writing too. Only empty paper drags the pages about.
function startDrag(event) {
  if (event.button !== 0 || event.shiftKey || reading()) return;
  const note = noteheadAt(event.clientX, event.clientY);
  if (!note || onTab(note)) return;
  const lines = staffLines(note.closest("g.staff"));
  const box = headBox(note);
  const [step, alter, octave] = score?.notes?.[note.id] ?? [];
  if (!step) return;
  drag = { id: note.id, y: event.clientY, lines, note, steps: 0, moved: false, line: Math.round(((box.top + box.bottom) / 2 - lines.top) / lines.step), x: (box.left + box.right) / 2, pitch: { step, alter, octave }, heard: null };
}

// The note a drag has reached: moved by steps of the scale, with the key's sharps and flats, as
// edit.move_note moves it, keeping its own accidental when it moves by whole octaves.
function draggedPitch(steps) {
  const { step, alter, octave } = drag.pitch;
  const height = STEP_LETTERS.indexOf(step) + 7 * octave + steps;
  const newStep = STEP_LETTERS[((height % 7) + 7) % 7], newOctave = Math.floor(height / 7);
  const newAlter = steps % 7 ? keyAlter(newStep, fifthsAt(drag.note)) : alter;
  return { name: pitchText(newStep, newAlter, newOctave), midi: 12 * (newOctave + 1) + STEP_SEMITONES[newStep] + newAlter };
}

function moveDrag(event) {
  if (!drag) return;
  const distance = drag.y - event.clientY;
  if (!drag.moved && Math.abs(distance) < DRAG_START_PX) return;
  if (!drag.moved) {
    drag.moved = true;
    drag.note.classList.add("dragging");
    hideGhost();
  }
  drag.steps = Math.round(distance / drag.lines.step);
  const pitch = draggedPitch(drag.steps);
  const [, , , type, dots] = score.notes?.[drag.note.id] ?? [];
  paintGhost({ lines: drag.lines, line: drag.line - drag.steps, x: drag.x, type: type || "quarter", dots, voice: layerNumber(drag.note) + 1, label: drag.steps ? pitch.name : `${pitch.name}, as it was` });
  if (drag.heard !== pitch.midi) {
    drag.heard = pitch.midi;
    const part = partOfStaff(staffNumber(drag.note)) ?? 0;
    hearKey(soundingPitch(pitch.midi, part), part);
  }
}

function endDrag() {
  if (!drag) return;
  const { id, steps, moved, note } = drag;
  drag = null;
  note.classList.remove("dragging");
  hideGhost();
  if (!moved) return;
  dragged = true;
  enqueue(async () => {
    // The note dragged is the one selected afterwards, while writing too.
    if (editor.on) editor.last = id; else if (selected !== id || range) select(id);
    if (steps) await send(`notes/${id}`, { action: "moveBy", steps });
    else updateTools();
  });
}

// Keys pressed together stack into a chord, the way they would on paper. Together is when the keys
// arrived, not when their turn in the queue came: that waits for the write before, which can take
// longer than a chord's keys are apart, and then each key of a chord was written as a note of its own.
function midiKey(number) {
  const arrived = performance.now();
  return enqueue(async () => {
    if (!editor.on || !editor.cursor) return;
    const together = arrived - lastMidiKey < CHORD_WINDOW_MS && editor.last;
    lastMidiKey = arrived;
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
      input.onmidimessage = ({ data }) => midiMessage(data);
    };
    access.inputs.forEach(listen);
    practice.midi = access.inputs.size ? "ready" : "none";
    drawMidiState();
    access.onstatechange = ({ port }) => {
      if (port.type !== "input") return;
      if (port.state === "connected") listen(port);
      practice.midi = [...access.inputs.values()].some(input => input.state === "connected") ? "ready" : "none";
      drawMidiState();
    };
  }).catch(() => { practice.midi = "none"; drawMidiState(); });
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

function setUpToolbar() {
  // One frame for them all, so the notes stand on one line as they do in MuseScore's toolbar.
  const boxes = LENGTHS.map(({ glyph }) => setGlyphs(glyph));
  const frame = { top: Math.max(...boxes.map(box => box.top)), bottom: Math.min(...boxes.map(box => box.bottom)) };
  $("lengths").replaceChildren(...LENGTHS.map(({ key, type, label, glyph }) => {
    const button = document.createElement("button");
    button.className = "flat";
    button.dataset.type = type;
    button.title = `${label} (${key})`;
    button.setAttribute("aria-label", label);
    button.innerHTML = glyphIcon(glyph, 5.5, frame);
    button.addEventListener("click", () => chooseLength(type));
    return button;
  }));
  $("pitches").replaceChildren(...[..."CDEFGAB"].map(step => {
    const button = document.createElement("button");
    button.textContent = step;
    button.addEventListener("click", () => writeAt(step));
    return button;
  }), Object.assign(document.createElement("button"), { textContent: "Rest", onclick: () => writeAt(null) }));
  $("voices").replaceChildren(...Array.from({ length: VOICES }, (_, index) => {
    const button = document.createElement("button");
    button.className = "flat";
    button.dataset.voice = index + 1;
    button.innerHTML = `${glyphIcon("E1D5", 4.4)}<sup>${index + 1}</sup>`;
    button.setAttribute("aria-label", `Voice ${index + 1}`);
    button.title = `Voice ${index + 1} (Ctrl+Alt+${index + 1})`;
    button.addEventListener("click", () => chooseVoice(index + 1));
    return button;
  }));
  document.querySelectorAll("#noteBar button[data-mark]").forEach(button => {
    button.addEventListener("click", () => markAction("toggle", { name: button.dataset.mark }));
  });
  $("restButton").addEventListener("click", () => (editor.on ? writeAt(null) : noteAction("delete")));
  $("pages").addEventListener("pointerdown", startDrag);
  window.addEventListener("pointermove", moveDrag);
  window.addEventListener("pointerup", endDrag);
  $("desk").addEventListener("contextmenu", openContextMenu);
  $("desk").addEventListener("pointerdown", startPan);
  window.addEventListener("pointermove", movePan);
  window.addEventListener("pointerup", endPan);
  $("desk").addEventListener("wheel", event => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    stepZoom(event.deltaY < 0 ? 1 : -1);
  }, { passive: false });
  $("pages").addEventListener("dblclick", event => {
    if (editor.on || !writing()) return;
    const mark = markAt(event);
    if (mark) openMark(mark);
    else if (event.target.closest("g.pgHead")) openProject();
  });
  $("dotButton").addEventListener("click", chooseDot);
  $("tripletButton").addEventListener("click", () => toggleTuplet());
  $("inputToggle").addEventListener("click", toggleInput);
  $("addBar").addEventListener("click", () => barAction("insert"));
  $("deleteBar").addEventListener("click", deleteBars);
  $("lyricButton").addEventListener("click", startLyrics);
  // A clicked button keeps the focus, and Space would press it again instead of playing.
  for (const id of ["noteBar", "viewTools", "pitches", "playPopup", "speedPopup", "library"]) $(id).addEventListener("click", event => event.target.closest("button")?.blur());
  // So would Enter, instead of breaking the line, by the original sheet and the bars to check too. But
  // only a mouse's click lets go of the focus there: one pressed from the keyboard keeps it, so the
  // steps through what's to check can go on with Space.
  for (const id of ["original", "barChecks"]) $(id).addEventListener("click", event => { if (event.detail) event.target.closest("button")?.blur(); });
  $("redo").addEventListener("click", redoEdit);
  window.addEventListener("resize", () => { drawCursor(); redrawGhost(); });
  $("desk").addEventListener("scroll", redrawGhost, { passive: true });
  $("pages").addEventListener("mousemove", event => { pointer = { x: event.clientX, y: event.clientY, shift: event.shiftKey }; drawGhost(pointer.x, pointer.y, pointer.shift); });
  $("pages").addEventListener("mouseleave", () => { pointer = null; hideGhost(); });
  // Shift pressed or let go over the staff changes what a click there does: add to the chord, or write.
  for (const name of ["keydown", "keyup"]) {
    window.addEventListener(name, event => {
      if (event.key !== "Shift" || !pointer || pointer.shift === event.shiftKey) return;
      pointer.shift = event.shiftKey;
      drawGhost(pointer.x, pointer.y, pointer.shift);
    });
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

const PITCH_KEYS = { "#": "sharp", "+": "sharp", "-": "flat", "=": "natural" };
const MARK_KEYS = { S: "staccato", V: "accent", N: "tenuto", O: "marcato" };
// A down-bow and an up-bow, side by side on the keyboard as the strokes follow each other.
const BOW_KEYS = { "[": "downbow", "]": "upbow" };

// With Ctrl, outside Write, only the keys that change nothing in the score: zoom, copy, select, move
// about, and undo and redo, which the top bar offers everywhere.
const LOOKING_KEYS = new Set(["=", "+", "-", "0", "c", "a", "z", "y", "arrowleft", "arrowright", "home", "end"]);

function handleControlKey(event) {
  const key = event.key.toLowerCase();
  if (event.shiftKey && key === "p") { event.preventDefault(); openCommands(); return; }
  if (!writing() && !LOOKING_KEYS.has(key)) return;
  if (event.altKey && /^[1-4]$/.test(key)) { event.preventDefault(); chooseVoice(Number(key)); return; }
  const actions = {
    "=": () => stepZoom(1),
    "+": () => stepZoom(1),
    "-": () => stepZoom(-1),
    0: () => setZoom(100),
    f9: () => openCommands(),
    b: appendBar,
    k: addChordHere,
    t: () => askForText(EXPRESSIONS.staff, { action: "text", name: "staff" }),
    e: () => askForText(EXPRESSIONS.expression, { action: "text", name: "expression" }),
    m: () => markAction("rehearsal"),
    z: () => (event.shiftKey ? redoEdit() : undoEdit()),
    y: redoEdit,
    c: copySelection,
    x: cutSelection,
    v: pasteHere,
    a: selectEverything,
    l: startLyrics,
    f: askForFingers,
    3: () => toggleTuplet(3),
    5: () => toggleTuplet(5),
    6: () => toggleTuplet(6),
    7: () => toggleTuplet(7),
    arrowup: () => (event.shiftKey ? crossStaff("crossUp") : tabSelected() ? moveToString(-1) : noteAction("octaveUp")),
    arrowdown: () => (event.shiftKey ? crossStaff("crossDown") : tabSelected() ? moveToString(1) : noteAction("octaveDown")),
    arrowleft: () => moveByMeasure(-1, event.shiftKey),
    arrowright: () => moveByMeasure(1, event.shiftKey),
    home: () => moveToEnd(false, event.shiftKey),
    end: () => moveToEnd(true, event.shiftKey),
    delete: removeRange,
    backspace: removeRange,
  };
  if (actions[key]) { event.preventDefault(); actions[key](); }
}

// F8 and F9 show the inspector on the selection, where the marks are, and F7 on the score, with its instruments.
const INSPECTOR_KEYS = { F7: "score", F8: "selection", F9: "selection" };
const MODE_KEYS = { KeyR: "read", KeyW: "write", KeyP: "practice" };

// Practice keeps the keys for playing and finding a place, and A and B set the loop's ends.
function practiceKey(event) {
  const key = event.key;
  const handled = () => event.preventDefault();
  if (key === " ") { handled(); togglePlaying(); return; }
  if (key === "Escape") { closeEditor(); select(null); return; }
  if (key === "ArrowLeft" || key === "ArrowRight") { handled(); moveAlong(key === "ArrowLeft" ? -1 : 1, event.shiftKey); return; }
  if (key === "a" || key === "A") { handled(); setLoopEnd("first"); return; }
  if (key === "b" || key === "B") { handled(); setLoopEnd("last"); return; }
  if (key === "n" || key === "N") { handled(); setMode("write"); toggleInput(); return; }
  if (key === "/") { handled(); openCommands(); }
}

// What a focused control does itself with Enter and with Space.
const PRESSED_BY_ENTER = "button, a[href], summary, [role=button]";
const PRESSED_BY_SPACE = "button, summary, [role=button]";

document.addEventListener("keydown", event => {
  const key = event.key;
  if (event.defaultPrevented) return; // Already the key of whatever had it, such as the command palette.
  if (key === "Escape" && openMenus.length) { closeMenus(); return; }
  // A field keeps the focus for a moment after its dialog or box has closed. It isn't being typed in then.
  const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName) && event.target.getClientRects().length > 0;
  // A fader or a switch in a panel keeps its own keys, but the keys that show and hide panels still
  // work, and so does Space on a fader, which has no use for it.
  const control = typing && event.target.tagName === "INPUT" && ["range", "checkbox"].includes(event.target.type);
  const passed = control && (/^F\d+$/.test(key) || (key === " " && event.target.type === "range"));
  if ((typing && !passed) || document.querySelector("dialog[open]")) return;
  const handled = () => event.preventDefault();
  // A fret's first digit is sent before whatever the next key does.
  if (fretTyping && !/^[0-9]$/.test(key)) flushFret();
  // The command palette and the library are there with or without a score.
  if ((event.ctrlKey || event.metaKey) && event.shiftKey && key.toLowerCase() === "p") { handled(); openCommands(); return; }
  if (key === "F6") { handled(); toggleLibrary(); return; }
  if ($("workspace").hidden) {
    if (key === "/" && !event.ctrlKey) { handled(); openCommands(); }
    return;
  }
  if (event.altKey && !event.ctrlKey && !event.shiftKey && !event.metaKey && MODE_KEYS[event.code]) { handled(); setMode(MODE_KEYS[event.code]); return; }
  // A button someone has tabbed to is pressed by Enter or Space, and a link followed by Enter, so the
  // key mustn't also act on the score: Enter on one put a line break in at the selected note instead,
  // and in Read mode both keys turned the page. With nothing focused, they act on the score as ever.
  const pressed = { "Enter": PRESSED_BY_ENTER, " ": PRESSED_BY_SPACE }[key];
  if (pressed && !event.ctrlKey && !event.metaKey && !event.altKey && event.target.matches?.(pressed)) return;
  if (reading() && !event.ctrlKey && !event.metaKey && !event.altKey && readingKey(event)) return;
  if (event.ctrlKey || event.metaKey) { handleControlKey(event); return; }
  if (INSPECTOR_KEYS[key]) { handled(); toggleInspector(INSPECTOR_KEYS[key]); return; }
  if (key === "F10") { handled(); toggleDock("mixer"); return; }
  if (key === "F12") { handled(); toggleDock("timeline"); return; }
  if ((key === "p" || key === "P") && !event.shiftKey && !event.altKey) { handled(); toggleDock("instrument"); return; }
  if (!writing()) { if (!event.altKey) practiceKey(event); return; }
  if (key === "Insert") { handled(); barAction("insert"); return; }
  if (event.altKey && event.shiftKey && key.toLowerCase() === "t") { handled(); askForTempo(); return; }
  if (event.altKey && event.shiftKey && key === "ArrowUp") { handled(); noteAction("up"); return; }
  if (event.altKey && event.shiftKey && key === "ArrowDown") { handled(); noteAction("down"); return; }
  if (event.altKey && /^[2-9]$/.test(key)) { handled(); noteAction("interval", { interval: Number(key) }); return; }
  if (event.altKey) return;
  if (key === "i" || key === "I") { handled(); showInspector(true, "score"); openAddPart(); return; }

  if (key === "Escape") {
    closeEditor();
    enqueue(async () => { if (editor.on) setInput(false); else select(null); });
    return;
  }
  if (selectedMark && !editor.on) {
    if (key === "Delete" || key === "Backspace") { handled(); removeMark(); return; }
    if (key === "F2" || key === "Enter") { handled(); openSelectedMark(); return; }
  }
  if (key === " ") { handled(); togglePlaying(); return; }
  if (tabKey(event)) return;
  if (MARK_KEYS[key]) { handled(); markAction("toggle", { name: MARK_KEYS[key] }); return; }
  if (BOW_KEYS[key]) { handled(); markAction("toggle", { name: BOW_KEYS[key] }); return; }
  if (key === "s") { handled(); markAction("slur"); return; }
  if (key === "<") { handled(); markAction("hairpin", { name: "crescendo" }); return; }
  if (key === ">") { handled(); markAction("hairpin", { name: "diminuendo" }); return; }
  // A grace note goes before the chosen note. With none chosen, / finds a command instead.
  if (key === "/") { handled(); if (actionTarget()) addGrace(); else openCommands(); return; }
  if (key === "r") { handled(); repeatSelection(); return; }
  if (key === "j") { handled(); noteAction("respell"); return; }
  if (key === "x") { handled(); noteAction("flip"); return; }
  if (key === "Enter" && !editor.on) { handled(); barAction("break"); return; }
  if (key === "n" || key === "N") { handled(); toggleInput(); return; }
  if (key === "ArrowLeft") { handled(); moveAlong(-1, event.shiftKey); return; }
  if (key === "ArrowRight") { handled(); moveAlong(1, event.shiftKey); return; }
  if (key === "ArrowUp") { handled(); noteAction("semitoneUp"); return; }
  if (key === "ArrowDown") { handled(); noteAction("semitoneDown"); return; }
  if (key.toLowerCase() === "q" || key.toLowerCase() === "w") { handled(); scaleLength(key.toLowerCase() === "w", event.shiftKey); return; }
  if (key === ".") { handled(); chooseDot(); return; }
  if (key === "t" || key === "T") { handled(); noteAction("tie"); return; }
  if (PITCH_KEYS[key]) { handled(); noteAction(PITCH_KEYS[key]); return; }
  const length = LENGTHS.find(item => item.key === key);
  if (length) { handled(); chooseLength(length.type); return; }
  if (editor.on) {
    if (key === "0") { handled(); writeAt(null); return; }
    if (key === "Backspace") { handled(); undoEdit(); return; }
    // The note just written, or one clicked while writing, becomes a rest.
    if (key === "Delete") { handled(); if (editor.last) noteAction("delete"); return; }
    if (/^[a-gA-G]$/.test(key)) {
      handled();
      if (event.shiftKey) noteAction("stack", { step: key.toUpperCase() }); else writeAt(key.toUpperCase());
    }
    return;
  }
  if (key === "0" || key === "Delete" || key === "Backspace") { handled(); noteAction("delete"); return; }
  // Outside note input a letter renames the selected note, and with Shift adds to its chord.
  if (/^[a-gA-G]$/.test(key)) { handled(); noteAction(event.shiftKey ? "stack" : "repitch", { step: key.toUpperCase() }); }
});

setUpPage();
setUpShell();
setUpPlayback();
setUpDock();
setUpInstruments();
setUpInspector();
setUpPractice();
setUpTechnique();
setUpToolbar();
setUpWorkspace();
updateTools();
if (location.hash.length > 1) { scoreId = location.hash.slice(1); foldLibrary(); poll(); }
drawHistory();

// How a guitarist, a violinist and a pianist play the notes: the techniques, fingerings, strings and
// hands the score keeps, as cells the inspector shows beside the note and the command palette finds,
// a guitar's tuning, capo and tablature, and the fingering box (Ctrl+F). The server says how each note
// is played in the sixth item of its details (see note_details in app/server.py).
const TUNING_NAMES = { standard: "Standard", dropD: "Drop D", DADGAD: "DADGAD", openG: "Open G", openD: "Open D", halfStepDown: "Half a step down", custom: "Custom" };
const ROMAN = ["½", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"];
const BEND_LABELS = { half: "½", full: "full", oneAndHalf: "1½" };
const PLUCK_NAMES = { p: "thumb", i: "index finger", m: "middle finger", a: "ring finger" };
const SUL_STRINGS = ["C", "G", "D", "A", "E"];
const SHOWN_POSITIONS = 8; // ½ to VII beside the note; the palette has them all.
let tunings = null; // The named tunings, from the server, once asked for.

// A note's own id for a fret number on the tablature, which stands for the note it copies.
function ownId(id) {
  return id?.endsWith("-tab") ? id.slice(0, -4) : id;
}

// How the score says a note is played: its string and fret, finger, string number and the rest.
function playedAs(id) {
  return score?.notes?.[ownId(id)]?.[5] ?? {};
}

// A pitch name as the server writes one, such as "F#3" or "Bb2", as a MIDI key.
function midiOfName(name) {
  const found = /^([A-G])([#b]*)(-?\d+)$/.exec(name ?? "");
  if (!found) return null;
  const alter = [...found[2]].reduce((total, sign) => total + (sign === "#" ? 1 : -1), 0);
  return 12 * (Number(found[3]) + 1) + { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 }[found[1]] + alter;
}

function letterOf(name) {
  return (name ?? "").replace(/-?\d+$/, "").replace("#", "♯").replace(/b$/, "♭");
}

// The part a selection is in, and what's fretted or bowed about it.
function selectedPart() {
  const element = document.getElementById(actionTarget() ?? "");
  return element ? partOfStaff(staffNumber(element)) ?? activePart() : activePart();
}

function frettedOf(part) {
  return score?.fretted?.[part] ?? null;
}

function bowedStrings(part) {
  return kindOfPart(part) === "violin" ? score?.strings?.[part] ?? ["G3", "D4", "A4", "E5"] : null;
}

// Whether a staff on the page is a tablature staff, whose lines are strings.
function isTabStaff(staff) {
  const part = partOfStaff(staff);
  const board = part === null ? null : frettedOf(part);
  if (!board?.tabStaff) return false;
  return staff - firstStaffOf(part) === board.tabStaff - 1;
}

function firstStaffOf(part) {
  return (score?.partStaves ?? []).slice(0, part).reduce((total, count) => total + count, 0);
}

function onTab(element) {
  return Boolean(element) && isTabStaff(staffNumber(element));
}

// The tablature a note or rest is on, or the one linked under its staff, in the same measure.
function tabStaffFor(element) {
  if (!element) return null;
  if (onTab(element)) return element.closest("g.staff");
  const part = partOfStaff(staffNumber(element));
  const board = part === null ? null : frettedOf(part);
  if (!board?.tabStaff) return null;
  const staves = [...(element.closest("g.measure")?.children ?? [])].filter(child => child.classList.contains("staff"));
  return staves[firstStaffOf(part) + board.tabStaff - 1] ?? null;
}

// The notes and rests a tablature staff's place in time is read from: its own on its own, or the
// standard staff's it's linked under, as it doesn't draw its rests.
function eventsBeside(tabStaff) {
  const part = partOfStaff(staffNumber(tabStaff));
  if ((score.partStaves?.[part] ?? 1) < 2) return [...tabStaff.querySelectorAll(EVENTS)];
  const staves = [...tabStaff.parentElement.children].filter(child => child.classList.contains("staff"));
  const source = staves.find(staff => partOfStaff(staffNumber(staff)) === part && !isTabStaff(staffNumber(staff)));
  return [...(source ?? tabStaff).querySelectorAll(EVENTS)];
}

// Where each string of a tablature staff is drawn: string 1, the highest, on its top line.
function stringLines(tabStaff) {
  const part = partOfStaff(staffNumber(tabStaff));
  const count = frettedOf(part)?.strings?.length ?? 6;
  const lines = staffLines(tabStaff);
  return { ...lines, count, part, gap: (lines.bottom - lines.top) / Math.max(count - 1, 1) };
}

// The pitch the staff shows for a fret on a string of a part's neck.
function pitchAtFret(part, string, fret) {
  const board = frettedOf(part);
  return shownPitch(midiOfName(board.strings[board.strings.length - string]) + (board.capo ?? 0) + fret, part);
}

// Small pictures for the cells.
function circled(text) {
  return `<span class="words plain circled">${text}</span>`;
}

function bendCell(amount) {
  return `<svg class="cell" viewBox="0 0 44 44" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="1.3"><path d="M7 33h9c6 0 9-5 9-15"/><path d="M21.5 21l3.5-5.5 3.5 5.5" fill="currentColor"/></g><text x="31" y="15" font-size="10" text-anchor="middle" fill="currentColor" font-family="Edwin, C059, serif">${amount}</text></svg>`;
}

function joinCell(letter, kind) {
  const heads = `<ellipse cx="11" cy="30" rx="4" ry="3" transform="rotate(-20 11 30)" fill="currentColor"/><ellipse cx="33" cy="22" rx="4" ry="3" transform="rotate(-20 33 22)" fill="currentColor"/>`;
  const between = kind === "slide" ? `<path d="M16 28l12-5" stroke-width="1.4"/>` : kind === "glissando" ? `<path d="M15 29c2-3 3 1 5-2s3 1 5-2 3 1 4-1" stroke-width="1.2"/>` : `<path d="M9 22c6-9 20-12 26-6" stroke-width="1.2"/><text x="22" y="13" font-size="10" font-weight="700" text-anchor="middle" fill="currentColor" stroke="none" font-family="Edwin, C059, serif">${letter}</text>`;
  return `<svg class="cell" viewBox="0 0 44 44" aria-hidden="true"><g fill="none" stroke="currentColor">${heads}${between}</g></svg>`;
}

// The cells for the players' marks. Each knows whether the selected note has it already, so the
// inspector can show it pressed.
function techniquePalettes() {
  const cell = (key, label, html, run, pressed = null) => ({ key, label, html, needs: "note", run, pressed });
  const join = (name, label, letter) => cell(`join:${name}`, label, joinCell(letter, name), () => markAction("join", { name }));
  const bend = (name, label) => cell(`bend:${name}`, label, bendCell(BEND_LABELS[name]), () => markAction("bend", { name }), played => played.bend === name);
  const pluck = letter => cell(`pluck:${letter}`, `Pluck with ${letter} (${PLUCK_NAMES[letter]})`, wordsCell(letter), () => markAction("pluck", { text: letter }), played => played.pluck === letter);
  const finger = number => cell(`finger:${number}`, `Finger ${number}${number === 0 ? " (open string)" : ""}`, wordsCell(String(number), "bold"), () => markAction("fingering", { text: String(number) }), played => played.finger === String(number));
  const string = number => cell(`string:${number}`, `String number ${number} (circled)`, circled(number), () => markAction("stringNumber", { number }), played => played.mark === number);
  const bowedString = number => cell(`bowedString:${number}`, `String ${ROMAN[number]}`, wordsCell(ROMAN[number], "plain"), () => markAction("stringNumber", { number }), played => played.mark === number);
  const sul = where => cell(`sul:${where}`, where === "pont." ? "Sul ponticello (near the bridge)" : where === "tasto" ? "Sul tasto (over the fingerboard)" : `Sul ${where} (play on the ${where} string)`, wordsCell(`sul ${where}`), () => markAction("sul", { text: where }), played => played.sul === where);
  const position = number => cell(`position:${number}`, `${number ? `Position ${ROMAN[number]}` : "Half position"} (${ROMAN[number]} pos.)`, wordsCell(ROMAN[number], "plain"), () => markAction("position", { number }), played => played.position === number);
  return [
    { id: "guitar", name: "Guitar techniques", contexts: [], cells: [
      join("hammerOn", "Hammer-on (H) to the next note", "H"), join("pullOff", "Pull-off (P) to the next note", "P"),
      join("slide", "Slide to the next note"), join("glissando", "Glissando to the next note"),
      bend("half", "Bend a half tone (½)"), bend("full", "Bend a full tone"), bend("oneAndHalf", "Bend one and a half tones (1½)"),
      cell("vibrato", "Vibrato", drawnCell(`<path d="M6 22c2.5-5 4.5-5 7 0s4.5 5 7 0 4.5-5 7 0 4.5 5 7 0 4.5-5 5-2" stroke-width="1.6"/>`), () => markAction("vibrato")),
      cell("line:palmMute", "Palm mute (P.M.)", wordsCell("P.M.", "plain"), () => markAction("line", { name: "palmMute" })),
      cell("line:letRing", "Let ring", wordsCell("let ring"), () => markAction("line", { name: "letRing" })),
      cell("harmonic:natural", "Natural harmonic", `<span class="words plain">${glyphIcon("E614", 6)} harm.</span>`, () => markAction("harmonic", { name: "natural" }), played => played.harmonic === "natural"),
      cell("harmonic:artificial", "Artificial harmonic (A.H.)", wordsCell("A.H.", "plain"), () => markAction("harmonic", { name: "artificial" }), played => played.harmonic === "artificial"),
      cell("deadNote", "Dead note (x)", glyphCell("E0A9", 6), () => markAction("deadNote"), played => Boolean(played.dead)),
    ] },
    { id: "fingers", name: "Fingers and strings", contexts: [], cells: [
      ...[0, 1, 2, 3, 4, 5].map(finger),
      cell("finger:type", "Type fingers, one for each note…", wordsCell("1 3 5…", "plain"), askForFingers),
      ...["p", "i", "m", "a"].map(pluck),
      ...[1, 2, 3, 4, 5, 6].map(string),
      ...[1, 2, 3, 4].map(bowedString),
    ] },
    { id: "bowed", name: "Bowed strings", contexts: [], textual: true, cells: [
      ...SUL_STRINGS.map(sul), sul("pont."), sul("tasto"),
      cell("pizzicato:on", "Pizzicato (pizz.)", wordsCell("pizz."), () => markAction("pizzicato", { on: true })),
      cell("pizzicato:off", "Arco (with the bow)", wordsCell("arco"), () => markAction("pizzicato", { on: false })),
      ...ROMAN.map((_, number) => position(number)),
      cell("mark:open", "Open string (o)", wordsCell("o", "plain"), () => markAction("toggle", { name: "open" })),
      cell("mark:stopped", "Stopped (+)", wordsCell("+", "plain"), () => markAction("toggle", { name: "stopped" })),
    ] },
    { id: "hands", name: "Piano hands", contexts: [], textual: true, cells: [
      cell("hand:RH", "Right hand (R.H.)", wordsCell("R.H.", "plain"), () => markAction("hand", { name: "RH" })),
      cell("hand:LH", "Left hand (L.H.)", wordsCell("L.H.", "plain"), () => markAction("hand", { name: "LH" })),
      cell("cross:up", "Draw on the staff above, for the other hand", `<span class="words plain">${icon("up")}staff</span>`, () => crossStaff("crossUp"), played => played.cross !== undefined && played.cross === 1),
      cell("cross:down", "Draw on the staff below, for the other hand", `<span class="words plain">${icon("down")}staff</span>`, () => crossStaff("crossDown"), played => played.cross !== undefined && played.cross > 1),
    ] },
  ];
}

// The rows of cells beside a note, by the kind of instrument. Strings and positions only offer what
// the instrument has.
function techniqueRows(kind, part) {
  if (kind === "guitar") {
    const strings = frettedOf(part)?.strings?.length ?? 6;
    return [
      ["Joins", ["join:hammerOn", "join:pullOff", "join:slide", "join:glissando"]],
      ["Bend", ["bend:half", "bend:full", "bend:oneAndHalf", "vibrato"]],
      ["Lines", ["line:palmMute", "line:letRing"]],
      ["Sound", ["harmonic:natural", "harmonic:artificial", "deadNote"]],
      ["Right hand", ["pluck:p", "pluck:i", "pluck:m", "pluck:a"]],
      ["Left hand", ["finger:0", "finger:1", "finger:2", "finger:3", "finger:4", "finger:type"]],
      ["String number", Array.from({ length: Math.min(strings, 6) }, (_, index) => `string:${index + 1}`)],
    ];
  }
  if (kind === "violin") {
    const letters = (bowedStrings(part) ?? []).map(letterOf);
    return [
      ["Fingers", ["finger:0", "finger:1", "finger:2", "finger:3", "finger:4", "finger:type"]],
      ["String", [1, 2, 3, 4].map(number => `bowedString:${number}`)],
      ["Sul", [...SUL_STRINGS.filter(letter => letters.includes(letter)).map(letter => `sul:${letter}`), "sul:pont.", "sul:tasto"]],
      ["Position", ROMAN.slice(0, SHOWN_POSITIONS).map((_, number) => `position:${number}`)],
      ["Bow", ["pizzicato:on", "pizzicato:off", "mark:open", "mark:stopped", "harmonic:natural", "harmonic:artificial"]],
    ];
  }
  if (kind === "piano") {
    return [
      ["Fingers", ["finger:1", "finger:2", "finger:3", "finger:4", "finger:5", "finger:type"]],
      ["Hand", ["hand:RH", "hand:LH", "cross:up", "cross:down"]],
    ];
  }
  return [];
}

// The instrument's own controls: a guitar's strings and tablature, then the rows of marks.
function drawTechnique(element, kind, part) {
  const holder = $("instrumentSlots");
  const rows = techniqueRows(kind, part);
  const wanted = `${kind}:${part}:${frettedOf(part)?.strings?.length ?? ""}:${(bowedStrings(part) ?? []).join(",")}`;
  if (holder.dataset.built !== wanted) {
    holder.dataset.built = wanted;
    const pieces = [];
    if (kind === "guitar") pieces.push(stringChoice(), tabSwitches());
    for (const [label, keys] of rows) {
      const row = document.createElement("div");
      row.className = "slot";
      row.dataset.row = label;
      row.innerHTML = `<span class="label"></span><div class="cells"></div>`;
      row.firstChild.textContent = label;
      row.lastChild.append(...keys.map(findCell).filter(Boolean).map(cellButton));
      pieces.push(row);
    }
    holder.replaceChildren(...pieces);
  }
  const played = playedAs(element?.id);
  holder.querySelectorAll("[data-cell]").forEach(button => {
    const found = findCell(button.dataset.cell);
    if (found?.pressed) button.setAttribute("aria-pressed", String(!range && Boolean(found.pressed(played))));
  });
  if (kind === "guitar") drawStringChoice(element, part, played);
}

// A guitar's strings, to play the selected note on another. One that can't play it says so when
// pressed, as the server explains why.
function stringChoice() {
  const row = document.createElement("div");
  row.className = "slot";
  row.id = "stringChoice";
  row.innerHTML = `<span class="label">Played on</span><div class="row tight" id="stringButtons"></div><p class="info" id="stringInfo"></p>`;
  return row;
}

function drawStringChoice(element, part, played) {
  const board = frettedOf(part);
  const buttons = $("stringButtons");
  const info = $("stringInfo");
  const note = element?.matches("g.note") && !range;
  buttons.hidden = !board || !note;
  if (!board) { info.textContent = ""; return; }
  if (!note) { info.textContent = range ? "Select one note to choose its string." : ""; return; }
  const names = [...board.strings].reverse();
  const pitch = soundOfNote(element.id);
  if (buttons.childElementCount !== names.length || buttons.dataset.strings !== names.join(",")) {
    buttons.dataset.strings = names.join(",");
    buttons.replaceChildren(...names.map((name, index) => {
      const button = document.createElement("button");
      button.dataset.string = index + 1;
      button.dataset.needs = "note";
      button.innerHTML = `<span class="number">${index + 1}</span><span class="open"></span>`;
      button.lastChild.textContent = letterOf(name);
      button.addEventListener("click", () => { button.blur(); noteAction("string", { string: index + 1 }); });
      return button;
    }));
  }
  [...buttons.children].forEach((button, index) => {
    const open = midiOfName(names[index]) + (board.capo ?? 0);
    const fret = pitch === null ? null : pitch - open;
    const playable = fret !== null && fret >= 0 && fret <= board.frets;
    button.setAttribute("aria-pressed", String(played.string === index + 1));
    button.classList.toggle("cannot", !playable);
    button.title = playable ? `Play it on string ${index + 1}, ${letterOf(names[index])}, at fret ${fret}` : `String ${index + 1} can't play this note`;
  });
  const where = `String ${played.string}, fret ${played.fret}${board.capo ? ` from the capo on fret ${board.capo}` : ""}`;
  const unchosen = board.tab ? "No string can play this note." : "Choose a string to keep it there. Until then the fretboard shows it where it's easiest to play.";
  info.textContent = played.string ? `${where}${onTab(element) ? ". Type a fret number to change it" : ""}` : unchosen;
}

// What a selected note sounds, from its name as the page shows it, which is at concert pitch when
// the score is shown that way.
function soundOfNote(id) {
  const [step, alter, octave] = score?.notes?.[id] ?? [];
  if (!step) return null;
  const shown = 12 * (octave + 1) + { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 }[step] + alter;
  const element = document.getElementById(id);
  return soundingPitch(shown, element ? partOfStaff(staffNumber(element)) ?? 0 : 0);
}

// Tablature and chord diagrams on and off, beside a guitar's note.
function tabSwitches() {
  const row = document.createElement("div");
  row.className = "slot";
  row.innerHTML = `<span class="label">Show</span><div class="row tight"><button class="toggle" data-switch="tab" title="A tablature staff under the staff, with each note's string and fret">Tablature</button><button class="toggle" data-switch="diagrams" title="A fretboard diagram over each chord symbol">Chord diagrams</button></div><p class="info" data-tuning></p>`;
  row.querySelector('[data-switch="tab"]').addEventListener("click", () => switchTab(selectedPart()));
  row.querySelector('[data-switch="diagrams"]').addEventListener("click", switchDiagrams);
  return row;
}

function tuningText(board) {
  const name = board.tuning === "custom" ? board.strings.map(letterOf).join(" ") : `${TUNING_NAMES[board.tuning] ?? board.tuning} tuning`;
  return `${name}${board.capo ? `, capo on fret ${board.capo}` : ", no capo"}`;
}

function drawSwitches() {
  const part = selectedPart();
  const board = frettedOf(part);
  document.querySelectorAll('[data-switch="tab"]').forEach(button => { button.setAttribute("aria-pressed", String(Boolean(board?.tab))); button.disabled = !board; });
  document.querySelectorAll('[data-switch="diagrams"]').forEach(button => button.setAttribute("aria-pressed", String(Boolean(score?.diagrams))));
  document.querySelectorAll("[data-tuning]").forEach(line => { line.textContent = board ? `${tuningText(board)}. Change it on the Score tab.` : ""; });
}

function switchTab(part) {
  const board = frettedOf(part);
  if (!board) { message("Only a guitar, a bass or another fretted instrument has tablature."); return Promise.resolve(); }
  return enqueue(() => send("strings", { part, tab: !board.tab }));
}

function switchDiagrams() {
  return enqueue(() => send("diagrams", { on: !score.diagrams }));
}

async function loadTunings() {
  if (tunings) return tunings;
  const response = await fetch("/api/tunings");
  if (response.ok) tunings = await response.json();
  return tunings;
}

// The chosen instrument's settings on the Score tab: a fretted one's tuning, capo and tablature.
function drawPartSettings() {
  const holder = $("partSettings");
  if (!holder || !score) return;
  const part = chosenPart ?? (score.fretted ?? []).findIndex(Boolean);
  const board = part >= 0 ? frettedOf(part) : null;
  holder.hidden = !board;
  if (!board) return;
  $("partSettingsName").textContent = partLabel(part);
  holder.dataset.part = part;
  const known = tunings?.[board.strings.length === 4 ? "bass" : "guitar"] ?? {};
  const choices = [...board.tunings.map(name => [name, `${TUNING_NAMES[name] ?? name}${known[name] ? ` (${known[name].map(letterOf).join(" ")})` : ""}`]), ["custom", "Custom…"]];
  $("tuningChoice").replaceChildren(...choices.map(([value, label]) => new Option(label, value)));
  $("tuningChoice").value = board.tuning;
  $("customStrings").hidden = board.tuning !== "custom";
  if (document.activeElement !== $("customStrings")) $("customStrings").value = board.strings.join(" ");
  $("capoChoice").replaceChildren(...Array.from({ length: (tunings?.mostCapo ?? 12) + 1 }, (_, fret) => new Option(fret ? `Fret ${fret}` : "No capo", fret)));
  $("capoChoice").value = String(board.capo);
  $("tabSwitch").setAttribute("aria-pressed", String(board.tab));
  if (!tunings) loadTunings().then(found => { if (found) drawPartSettings(); });
}

function setStrings(changes) {
  const part = Number($("partSettings").dataset.part);
  return enqueue(() => send("strings", { part, ...changes }));
}

// The fingering box: digits typed for the selected notes, one finger for all of them or one each in
// turn, such as 135 for a chord or a run. It keeps the number keys for lengths everywhere else.
function askForFingers() {
  return enqueue(async () => {
    const notes = markTarget().filter(id => document.getElementById(id)?.matches("g.note"));
    if (!notes.length) { message("Choose the notes to finger first."); return; }
    openEditor(headBox(document.getElementById(notes[0])), null, "");
    editing = { newMark: { action: "fingering", notes } };
    $("chordInput").placeholder = notes.length > 1 ? `Fingers: one for all, or ${notes.length} in turn, e.g. ${"1352".slice(0, Math.min(notes.length, 4))}` : "Finger, e.g. 3";
  });
}

// Ctrl+Shift+↑ and ↓ draw the selected notes on the staff above or below, for the other hand, as
// MuseScore's keys do. The notes stay in their own staff's voice.
function crossStaff(action) {
  return enqueue(async () => {
    if (range) await send("group", { action, notes: range.ids });
    else if (actionTarget()) await send(`notes/${actionTarget()}`, { action });
    else message("Choose a note first.");
  });
}

// Notes no string can play, listed with the bar checks: a click goes to the note.
function fretChips() {
  return (score?.fretProblems ?? []).slice(0, 40).map(problem => {
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.dataset.fretProblem = problem.note;
    const element = document.getElementById(problem.note);
    const bar = element ? measureFacts(measureIndexOf(element)).number : "";
    chip.textContent = `${bar ? `Bar ${bar}: ` : ""}${problem.reason}`;
    chip.title = "No string can play this note, so the tablature leaves it out. Click to go to it.";
    chip.addEventListener("click", () => enqueue(async () => {
      if (editor.on) moveCursor(problem.note); else select(problem.note);
      document.getElementById(problem.note)?.scrollIntoView({ block: "center" });
    }));
    return chip;
  });
}

// Commands the palette offers for the instrument as a whole.
function techniqueCommands() {
  if (!score) return [];
  const part = selectedPart();
  const board = frettedOf(part);
  const items = [{ label: "Chord diagrams", where: "Guitar", keys: "", checked: Boolean(score.diagrams), run: switchDiagrams }];
  if (board) items.push({ label: `Tablature for ${partLabel(part)}`, where: "Guitar", keys: "", checked: board.tab, run: () => switchTab(part) });
  items.push({ label: "Type fingers for the selected notes", where: "Fingers and strings", keys: "Ctrl+F", enabled: Boolean(actionTarget()), run: askForFingers });
  return items.map(item => ({ enabled: true, ...item }));
}

function setUpTechnique() {
  $("tuningChoice").addEventListener("change", event => {
    event.target.blur();
    if (event.target.value === "custom") { $("customStrings").hidden = false; $("customStrings").focus(); return; }
    setStrings({ tuning: event.target.value });
  });
  $("customStrings").addEventListener("keydown", event => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const strings = event.target.value.trim().split(/[\s,]+/).filter(Boolean);
    event.target.blur();
    setStrings({ strings });
  });
  $("capoChoice").addEventListener("change", event => { event.target.blur(); setStrings({ capo: Number(event.target.value) }); });
  $("tabSwitch").addEventListener("click", () => switchTab(Number($("partSettings").dataset.part)));
  $("diagramsSwitch").addEventListener("click", switchDiagrams);
  setUpChordPane();
}

// A chord symbol's diagram in the inspector: chosen from the other shapes the chord has, or set by
// clicking a string at a fret for a finger, above the nut for an open or unplayed string, and a
// barre. Each change is an edit the score keeps as the chord's <frame>.
const DIAGRAM_ROWS = 5;
const chordPane = { index: null, first: null, shapes: null, asked: "" };
let selectedChord = null; // The chord symbol selected, by its index in score.chords.

function chooseChord(index) {
  selectedChord = index;
  chordPane.first = null;
  drawInspector();
}

// A diagram as an SVG: strings across, frets down from `first`, with dots, a barre and the marks
// above the nut. With `clickable`, each place is a target that says its string and fret.
function diagramSvg(diagram, count, { gap = 26, rows = DIAGRAM_ROWS, first = diagram?.first ?? 1, clickable = false } = {}) {
  const fretGap = gap * 1.15, left = gap * 0.9, top = gap * 1.2, right = first > 1 ? gap * 1.2 : 0;
  const width = left * 2 + gap * (count - 1) + right, height = top + fretGap * rows + gap * 0.5;
  const frets = diagram?.frets ?? Array(count).fill(null);
  const parts = [];
  for (let string = 0; string < count; string++) parts.push(`<path d="M${left + string * gap} ${top}V${top + fretGap * rows}" stroke="currentColor" stroke-width="1"/>`);
  for (let row = 0; row <= rows; row++) parts.push(`<path d="M${left}  ${top + row * fretGap}H${left + gap * (count - 1)}" stroke="currentColor" stroke-width="${row === 0 && first === 1 ? 3.5 : 1}"/>`);
  if (first > 1) parts.push(`<text x="${left + gap * (count - 1) + gap * 0.3}" y="${top + fretGap * 0.75}" font-size="${Math.max(gap * 0.45, 8)}" fill="currentColor">${first}${gap >= 20 ? "fr" : ""}</text>`);
  const barre = diagram?.barre;
  if (barre && barre[0] >= first && barre[0] < first + rows) {
    const y = top + (barre[0] - first + 0.5) * fretGap;
    parts.push(`<path d="M${left + barre[1] * gap} ${y}H${left + barre[2] * gap}" stroke="currentColor" stroke-width="${gap * 0.6}" stroke-linecap="round"/>`);
  }
  frets.forEach((fret, string) => {
    const x = left + string * gap, above = top - gap * 0.55, mark = gap * 0.22;
    if (fret === null) parts.push(`<path d="M${x - mark} ${above - mark}L${x + mark} ${above + mark}M${x - mark} ${above + mark}L${x + mark} ${above - mark}" stroke="currentColor" stroke-width="1.3"/>`);
    else if (fret === 0) parts.push(`<circle cx="${x}" cy="${above}" r="${mark}" fill="none" stroke="currentColor" stroke-width="1.3"/>`);
    else if (fret >= first && fret < first + rows && !(barre && fret === barre[0] && string >= barre[1] && string <= barre[2])) parts.push(`<circle cx="${x}" cy="${top + (fret - first + 0.5) * fretGap}" r="${gap * 0.3}" fill="currentColor"/>`);
  });
  if (clickable) {
    for (let string = 0; string < count; string++) {
      const x = left + string * gap - gap / 2;
      parts.push(`<rect data-string="${string}" data-fret="nut" x="${x}" y="0" width="${gap}" height="${top}"><title>String ${count - string}: open or not played</title></rect>`);
      for (let row = 0; row < rows; row++) parts.push(`<rect data-string="${string}" data-fret="${first + row}" x="${x}" y="${top + row * fretGap}" width="${gap}" height="${fretGap}"><title>String ${count - string}, fret ${first + row}</title></rect>`);
    }
  }
  return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="${!clickable}">${parts.join("")}</svg>`;
}

function sendFrame(body) {
  const index = selectedChord;
  return enqueue(() => send(`chords/${index}/frame`, body));
}

// A click on the diagram: a finger at a fret, or the same place again for an open string, and above
// the nut an open string or, again, one not played.
function clickDiagram(event) {
  const target = event.target.closest("[data-string]");
  const diagram = score?.chords?.[selectedChord]?.diagram;
  if (!target || !diagram) return;
  const frets = [...diagram.frets];
  const string = Number(target.dataset.string);
  if (target.dataset.fret === "nut") frets[string] = frets[string] === 0 ? null : 0;
  else frets[string] = frets[string] === Number(target.dataset.fret) ? 0 : Number(target.dataset.fret);
  const barre = diagram.barre && frets[diagram.barre[1]] === diagram.barre[0] && frets[diagram.barre[2]] === diagram.barre[0] && frets.slice(diagram.barre[1], diagram.barre[2] + 1).every(fret => fret !== null && fret >= diagram.barre[0]) ? diagram.barre : null;
  if (frets.every(fret => fret === null)) { message("A diagram needs a string to play."); return; }
  sendFrame({ frets, barre: diagram.custom ? barre : null });
}

// The first finger across the strings at the lowest fret, from the first string fretted there to the last.
function toggleBarre() {
  const diagram = score?.chords?.[selectedChord]?.diagram;
  if (!diagram) return;
  if (diagram.barre) { sendFrame({ frets: diagram.frets, barre: null }); return; }
  const fretted = diagram.frets.filter(fret => fret);
  const low = Math.min(...fretted);
  const on = diagram.frets.flatMap((fret, string) => (fret === low ? [string] : []));
  if (on.length < 2 || diagram.frets.slice(on[0], on.at(-1) + 1).some(fret => fret === null || fret < low)) {
    message("A barre needs two strings at its fret, with every string between them fretted there or higher.");
    return;
  }
  sendFrame({ frets: diagram.frets, barre: [low, on[0], on.at(-1)] });
}

async function loadShapes(index) {
  const asked = `${scoreId}:${index}:${score.chords[index]?.text}:${(score.fretted ?? []).map(board => board && `${board.strings}/${board.capo}`).join("|")}`;
  if (chordPane.asked === asked) return;
  chordPane.asked = asked;
  chordPane.shapes = null;
  const response = await fetch(`/api/scores/${scoreId}/chords/${index}/shapes`);
  if (response.ok && chordPane.asked === asked) {
    chordPane.shapes = await response.json();
    drawChordPane();
  }
}

function drawChordPane() {
  const chord = selectedChord === null ? null : score?.chords?.[selectedChord];
  $("propChord").hidden = !chord || shownPane() !== "selection";
  if (!chord) return;
  $("propChordName").textContent = `Chord ${chord.text}`;
  const diagram = chord.diagram;
  $("chordDiagramTools").hidden = !score.diagrams || !diagram;
  $("diagramShow").hidden = Boolean(score.diagrams);
  if (!score.diagrams) { $("propChordWhat").textContent = "Show chord diagrams to choose how this chord is played."; return; }
  if (!diagram) { $("propChordWhat").textContent = "This chord has no diagram."; return; }
  const count = diagram.frets.length;
  const most = chordPane.shapes?.frets ?? 19;
  chordPane.first = Math.min(Math.max(chordPane.first ?? diagram.first, 1), Math.max(most - DIAGRAM_ROWS + 1, 1));
  $("propChordWhat").textContent = diagram.custom ? "A shape of its own. Usual shape gives it the library's again." : "The usual shape. Click a string at a fret, or above the nut, to change it.";
  $("chordDiagram").innerHTML = diagramSvg(diagram, count, { first: chordPane.first, clickable: true });
  $("diagramNut").disabled = chordPane.first <= 1;
  $("diagramBody").disabled = chordPane.first >= most - DIAGRAM_ROWS + 1;
  $("diagramBarre").setAttribute("aria-pressed", String(Boolean(diagram.barre)));
  $("diagramUsual").disabled = !diagram.custom;
  const shapes = chordPane.shapes?.shapes ?? [];
  $("chordShapes").replaceChildren(...shapes.map(shape => {
    const button = document.createElement("button");
    button.className = "flat";
    const same = shape.frets.every((fret, string) => fret === diagram.frets[string]) && String(shape.barre) === String(diagram.barre);
    button.setAttribute("aria-pressed", String(same));
    button.title = `Play it ${shape.frets.map(fret => (fret === null ? "x" : fret)).join(" ")}`;
    button.innerHTML = diagramSvg(shape, count, { gap: 10, rows: 4, first: shape.first });
    button.addEventListener("click", () => { chordPane.first = shape.first; sendFrame({ frets: shape.frets, barre: shape.barre }); });
    return button;
  }));
  loadShapes(selectedChord);
}

function setUpChordPane() {
  $("chordDiagram").addEventListener("click", clickDiagram);
  $("diagramNut").addEventListener("click", () => { chordPane.first -= 1; drawChordPane(); });
  $("diagramBody").addEventListener("click", () => { chordPane.first += 1; drawChordPane(); });
  $("diagramBarre").addEventListener("click", toggleBarre);
  $("diagramUsual").addEventListener("click", () => { chordPane.first = null; sendFrame({ frets: null }); });
  $("diagramShow").addEventListener("click", switchDiagrams);
}

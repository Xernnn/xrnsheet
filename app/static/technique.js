// How a guitarist, a violinist and a pianist play the notes: the techniques, fingerings, strings and
// hands the score keeps, as cells the inspector shows beside the note and the command palette finds,
// a guitar's tuning, capo and tablature, and the fingering box (Ctrl+F). The server says how each note
// is played in the sixth item of its details (see note_details in app/server.py).
const TUNING_NAMES = { standard: "Standard", dropD: "Drop D", DADGAD: "DADGAD", openG: "Open G", openD: "Open D", halfStepDown: "Half a step down", custom: "Custom" };
const ROMAN = ["½", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"];
const BEND_LABELS = { half: "½", full: "full", oneAndHalf: "1½" };
const PLUCK_NAMES = { p: "thumb", i: "index finger", m: "middle finger", a: "ring finger" };
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

// Whether a staff on the page is a tablature staff, which takes no clicked notes.
function isTabStaff(staff) {
  const part = partOfStaff(staff);
  const board = part === null ? null : frettedOf(part);
  if (!board?.tabStaff) return false;
  const first = (score.partStaves ?? []).slice(0, part).reduce((total, count) => total + count, 0);
  return staff - first === board.tabStaff - 1;
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
  return [
    { id: "guitar", name: "Guitar techniques", contexts: [], cells: [
      join("hammerOn", "Hammer-on (H) to the next note", "H"), join("pullOff", "Pull-off (P) to the next note", "P"),
      join("slide", "Slide to the next note"), join("glissando", "Glissando to the next note"),
      bend("half", "Bend a half tone (½)"), bend("full", "Bend a full tone"), bend("oneAndHalf", "Bend one and a half tones (1½)"),
      cell("vibrato", "Vibrato", drawnCell(`<path d="M6 22c2.5-5 4.5-5 7 0s4.5 5 7 0 4.5-5 7 0 4.5 5 7 0 4.5-5 5-2" stroke-width="1.6"/>`), () => markAction("vibrato")),
      cell("line:palmMute", "Palm mute (P.M.)", wordsCell("P.M.", "plain"), () => markAction("line", { name: "palmMute" })),
      cell("line:letRing", "Let ring", wordsCell("let ring"), () => markAction("line", { name: "letRing" })),
      cell("harmonic:natural", "Natural harmonic", glyphCell("E614", 7), () => markAction("harmonic", { name: "natural" }), played => played.harmonic === "natural"),
      cell("harmonic:artificial", "Artificial harmonic (A.H.)", wordsCell("A.H.", "plain"), () => markAction("harmonic", { name: "artificial" }), played => played.harmonic === "artificial"),
      cell("deadNote", "Dead note (x)", glyphCell("E0A9", 6), () => markAction("deadNote"), played => Boolean(played.dead)),
    ] },
    { id: "fingers", name: "Fingers and strings", contexts: [], cells: [
      ...[0, 1, 2, 3, 4, 5].map(finger),
      cell("finger:type", "Type fingers, one for each note…", wordsCell("1 3 5…", "plain"), askForFingers),
      ...["p", "i", "m", "a"].map(pluck),
      ...[1, 2, 3, 4, 5, 6].map(string),
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
  buttons.hidden = !board?.tab || !note;
  if (!board?.tab) { info.textContent = "Turn tablature on to choose each note's string and fret."; return; }
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
  info.textContent = played.string ? `String ${played.string}, fret ${played.fret}${board.capo ? ` from the capo on fret ${board.capo}` : ""}` : "No string can play this note.";
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
}

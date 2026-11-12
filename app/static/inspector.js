// The inspector: what's selected, and everything that can be done to it, in place of palettes, menus
// and a properties panel. A note shows its pitch, length, accidental, voice, words and its
// instrument's own marks; a measure its key, time, clef, barline, repeats and tempo; and with nothing
// selected it shows the score: its title, instruments, pages and how it's shown.
const ALTERATIONS = { "-2": "𝄫", "-1": "♭", 0: "", 1: "♯", 2: "𝄪" };
const ACCIDENTAL_ACTIONS = [["doubleFlat", "Double flat", "E264"], ["flat", "Flat (-)", "E260"], ["natural", "Natural (=)", "E261"], ["sharp", "Sharp (+)", "E262"], ["doubleSharp", "Double sharp", "E263"]];
const GROUPS_KEY = "inspectorGroups";
const inspector = { pane: "selection", chosen: null, groups: new Set(readSetting(GROUPS_KEY, "articulations,dynamics").split(",").filter(Boolean)) };
// The marks each kind of instrument has a place for, beside the note. Below them come the player's
// own rows: a guitar's string and fret, techniques, fingers and strings (technique.js).
const INSTRUMENT_CELLS = {
  piano: ["line:pedal-pedstar", "line:pedal-line", "line:8va", "line:8vb", "mark:arpeggio"],
  guitar: ["mark:arpeggio", "line:slur"],
  violin: ["mark:downbow", "mark:upbow", "line:slur", "mark:staccato", "mark:tenuto"],
  other: [],
};

function inspectorShown() {
  return !document.body.classList.contains("inspectorHidden") && (innerWidth > 1180 || document.body.classList.contains("inspectorOpen"));
}

// Show the inspector, on the selection or on the score, or put it away.
function showInspector(on, pane = null) {
  document.body.classList.toggle("inspectorHidden", !on);
  document.body.classList.toggle("inspectorOpen", on);
  keepSetting("inspector", on ? "shown" : "hidden");
  $("inspectorToggle").setAttribute("aria-pressed", String(on));
  if (pane) inspector.pane = pane;
  if (on && !writing()) setMode("write");
  drawInspector();
  drawCursor();
}

// F8 and F9 show the inspector on the selection, F7 on the score, or put it away if it's showing that.
function toggleInspector(pane = null) {
  const showing = inspectorShown() && (!pane || shownPane() === pane);
  showInspector(!showing, pane);
}

function shownPane() {
  const target = score ? actionTarget() : null;
  if (inspector.pane === "score" || (!target && !selectedMark)) return "score";
  return "selection";
}

function setPane(pane) {
  inspector.pane = pane;
  drawInspector();
}

// A note's name as the page shows it, such as F♯4, and its written length, as the score says.
function spelledName(element) {
  const [step, alter, octave] = score?.notes?.[element?.id] ?? [];
  return step ? `${step}${ALTERATIONS[alter] ?? ""}${octave}` : "";
}

function lengthName(element) {
  const [, , , type, dots] = score?.notes?.[element?.id] ?? [];
  if (!type) return element?.matches("g.mRest, g.rest") ? "whole measure" : "";
  const tuplet = element.closest("g.tuplet") ? " in a tuplet" : "";
  return `${dots === 2 ? "double-dotted " : dots ? "dotted " : ""}${type}${tuplet}`;
}

// Which measure of the score an element is in, counted from 0 in the order the pages draw them.
function measureIndexOf(element) {
  const measure = element?.closest("g.measure");
  return measure ? [...document.querySelectorAll("#pages g.measure")].indexOf(measure) : -1;
}

// The key, time, tempo and barline in force at a measure, as the timeline lists where they change.
function measureFacts(index) {
  const measures = score?.timeline?.measures ?? [];
  const before = name => { for (let at = Math.min(index, measures.length - 1); at >= 0; at--) if (measures[at]?.[name]) return measures[at][name]; return ""; };
  return { number: measures[index]?.number ?? String(index + 1), key: before("key"), time: before("time"), tempo: before("tempo"), barline: measures[index]?.barline ?? "" };
}

function handName(part, staff) {
  if (kindOfPart(part) !== "piano" || (score?.partStaves?.[part] ?? 1) < 2) return "";
  return staff === 0 ? "right hand" : staff === 1 ? "left hand" : `staff ${staff + 1}`;
}

// The staff of its part a drawn element is on, counted from 0.
function staffInPart(element) {
  const staff = staffNumber(element);
  const part = partOfStaff(staff);
  if (part === null) return 0;
  return staff - (score.partStaves ?? []).slice(0, part).reduce((total, count) => total + count, 0);
}

// A group of cells that opens and closes, showing what's chosen in it now.
function groupElement(palette) {
  const holder = document.createElement("div");
  holder.className = "group" + (palette.textual ? " textual" : "");
  holder.dataset.group = palette.id;
  if (palette.wide) holder.style.setProperty("--cell", `${palette.wide}px`);
  const head = document.createElement("button");
  head.className = "groupHead";
  head.innerHTML = `<span class="name"></span><span class="current"></span>`;
  head.querySelector(".name").textContent = palette.name;
  head.addEventListener("click", () => toggleGroup(palette.id));
  holder.append(head);
  fillGroup(holder, palette);
  return holder;
}

// A group's cells are only made once it's opened, as most never are.
function fillGroup(holder, palette) {
  const open = inspector.groups.has(palette.id);
  holder.querySelector(".groupHead").setAttribute("aria-expanded", String(open));
  let cells = holder.querySelector(".cells");
  if (open && !cells) {
    cells = document.createElement("div");
    cells.className = "cells";
    cells.append(...palette.cells.map(cellButton));
    holder.append(cells);
  }
  if (cells) cells.hidden = !open;
}

function toggleGroup(id) {
  if (inspector.groups.has(id)) inspector.groups.delete(id); else inspector.groups.add(id);
  keepSetting(GROUPS_KEY, [...inspector.groups].join(","));
  const palette = catalogue().find(item => item.id === id);
  document.querySelectorAll(`#inspector .group[data-group="${id}"]`).forEach(holder => fillGroup(holder, palette));
  enableTools();
}

// Open a group, wherever it's shown, so a cell in it can be seen.
function openGroup(id) {
  if (!inspector.groups.has(id)) toggleGroup(id);
}

function buildGroups() {
  for (const [context, holder] of [["note", "noteGroups"], ["measure", "measureGroups"], ["score", "scoreGroups"]]) {
    $(holder).replaceChildren(...catalogue().filter(palette => palette.contexts.includes(context)).map(groupElement));
  }
  const glyphButton = (label, glyph, run, extra = {}) => {
    const button = document.createElement("button");
    button.className = "glyph";
    button.title = label;
    button.setAttribute("aria-label", label.replace(/ \(.*\)$/, ""));
    button.innerHTML = glyphIcon(glyph, 5.5);
    Object.entries(extra).forEach(([name, value]) => { button.dataset[name] = value; });
    button.addEventListener("click", () => { button.blur(); run(); });
    return button;
  };
  $("lengthRow").replaceChildren(...LENGTHS.map(({ key, type, label, glyph }) => glyphButton(`${label} (${key})`, glyph, () => chooseLength(type), { length: type, needs: "note" })),
    glyphButton("Dotted (.)", "E1E7", chooseDot, { needs: "note" }));
  $("accidentalRow").replaceChildren(...ACCIDENTAL_ACTIONS.map(([action, label, glyph]) => glyphButton(label, glyph, () => noteAction(action), { needs: "note" })));
  $("voiceRow").replaceChildren(...Array.from({ length: VOICES }, (_, index) => {
    const button = document.createElement("button");
    button.dataset.voice = index + 1;
    button.dataset.needs = "note";
    button.innerHTML = `${glyphIcon("E1D5", 4.4)}<sup>${index + 1}</sup>`;
    button.title = `Move to voice ${index + 1} (Ctrl+Alt+${index + 1})`;
    button.setAttribute("aria-label", `Voice ${index + 1}`);
    button.addEventListener("click", () => chooseVoice(index + 1));
    return button;
  }));
}

// The instruments in the score, each with its kind. Choosing one is choosing the instrument below.
function drawParts() {
  const names = score?.parts ?? [];
  if (chosenPart !== null && chosenPart >= names.length) chosenPart = null;
  $("partList").replaceChildren(...names.map((name, index) => {
    const row = document.createElement("li");
    row.setAttribute("role", "option");
    row.setAttribute("aria-selected", String(index === chosenPart));
    row.dataset.part = index;
    row.innerHTML = `<span class="kind">${icon(KINDS[kindOfPart(index)].icon)}</span><span class="name"></span>`;
    row.querySelector(".name").textContent = partLabel(index);
    row.addEventListener("click", () => choosePart(index));
    return row;
  }));
  $("partRemove").disabled = chosenPart === null || names.length < 2;
  drawPartSettings();
}

function drawInstrumentMarks(element) {
  const part = element ? partOfStaff(staffNumber(element)) ?? 0 : activePart();
  const kind = kindOfPart(part);
  const keys = INSTRUMENT_CELLS[kind] ?? [];
  const holder = $("propInstrument");
  holder.hidden = !element || !keys.length;
  if (holder.hidden) return;
  holder.dataset.kind = kind;
  $("propInstrumentName").innerHTML = `${icon(KINDS[kind].icon)}<span></span>`;
  $("propInstrumentName").lastChild.textContent = `For the ${playerOf(part)}`;
  if ($("instrumentCells").dataset.kind !== kind) {
    $("instrumentCells").dataset.kind = kind;
    $("instrumentCells").replaceChildren(...keys.map(findCell).filter(Boolean).map(cell => {
      const button = cellButton(cell);
      // The same cell is in its own group too, which keeps the name it's found by.
      button.dataset.instrumentCell = button.dataset.cell;
      delete button.dataset.cell;
      return button;
    }));
  }
  drawTechnique(element, kind, part);
  drawSwitches();
}

function drawInspector() {
  const pane = shownPane();
  for (const tab of document.querySelectorAll("#inspectorTabs button")) tab.setAttribute("aria-selected", String(tab.dataset.pane === pane));
  const target = score ? actionTarget() : null;
  const element = target && document.getElementById(target);
  const mark = selectedMark && score?.marks.find(item => item.id === selectedMark);
  $("inspectorTabs").querySelector('[data-pane="selection"]').disabled = !element && !mark;
  $("propScore").hidden = pane !== "score";
  $("propMark").hidden = pane !== "selection" || !mark;
  $("propNote").hidden = $("propMeasure").hidden = pane !== "selection" || !element;
  if (mark && pane === "selection") {
    $("propMarkKind").textContent = markName(mark);
    $("propMarkText").textContent = mark.text ? `“${mark.text}”` : "";
    $("markEdit").hidden = !markEditable(mark);
  }
  if (element && pane === "selection") drawSelection(element);
  drawSwitches();
  if (!editor.on && element) {
    const part = partOfStaff(staffNumber(element));
    if (part !== null && part !== chosenPart) choosePart(part, false);
  }
  enableTools();
}

function drawSelection(element) {
  const part = partOfStaff(staffNumber(element)) ?? 0;
  const hand = handName(part, staffInPart(element));
  const where = [`voice ${layerNumber(element) + 1}`, `${partLabel(part)}${hand ? `, ${hand}` : ""}`];
  const index = measureIndexOf(element);
  const facts = measureFacts(index);
  if (range) {
    const last = measureIndexOf(document.getElementById(range.last));
    $("propKind").textContent = `${range.ids.length} notes`;
    $("propWhat").textContent = `Measures ${facts.number}${last > index ? `–${measureFacts(last).number}` : ""} · ${partLabel(part)}`;
  } else if (element.matches("g.note")) {
    const chord = element.closest("g.chord");
    const names = chord ? [...chord.querySelectorAll("g.note")].map(spelledName) : [spelledName(element)];
    $("propKind").textContent = names.join(" ");
    $("propWhat").textContent = [`${lengthName(element) || "note"}`.replace(/^./, letter => letter.toUpperCase()), ...where].join(" · ");
  } else {
    $("propKind").textContent = "Rest";
    $("propWhat").textContent = [`${lengthName(element) || "rest"}`.replace(/^./, letter => letter.toUpperCase()), ...where].join(" · ");
  }
  $("propMeasureName").textContent = `Measure ${facts.number}`;
  const current = { keys: facts.key ? `${facts.key} major` : "", times: facts.time, tempo: facts.tempo, barlines: facts.barline };
  document.querySelectorAll("#measureGroups .group").forEach(holder => { holder.querySelector(".current").textContent = current[holder.dataset.group] ?? ""; });
  const typeNow = score.notes?.[element.id]?.[3];
  document.querySelectorAll("#lengthRow [data-length]").forEach(button => button.setAttribute("aria-pressed", String(!range && button.dataset.length === typeNow)));
  document.querySelectorAll("#voiceRow button").forEach(button => button.setAttribute("aria-pressed", String(!range && Number(button.dataset.voice) === layerNumber(element) + 1)));
  drawInstrumentMarks(element);
}

// The inspector's score fields keep what's typed once it's finished with, as one edit.
function saveTitleFields() {
  const body = { title: $("scoreTitle").value, composer: $("scoreComposer").value };
  if (!score || (body.title === (score.title ?? "") && body.composer === (score.composer ?? ""))) return;
  enqueue(() => send("title", body));
}

function setUpInspector() {
  buildGroups();
  for (const tab of document.querySelectorAll("#inspectorTabs button")) tab.addEventListener("click", () => setPane(tab.dataset.pane));
  $("inspectorToggle").addEventListener("click", () => showInspector(!inspectorShown()));
  $("inspectorClose").addEventListener("click", () => showInspector(false));
  $("graceButton").addEventListener("click", addGrace);
  $("staffTextButton").addEventListener("click", () => askForText(EXPRESSIONS.staff, { action: "text", name: "staff" }));
  $("breakButton").addEventListener("click", () => barAction("break"));
  for (const id of ["scoreTitle", "scoreComposer"]) {
    $(id).addEventListener("change", saveTitleFields);
    $(id).addEventListener("keydown", event => { if (event.key === "Enter" || event.key === "Escape") { event.preventDefault(); if (event.key === "Escape") drawTitle(); event.target.blur(); } });
  }
  $("inspector").addEventListener("click", event => event.target.closest("button")?.blur());
  if (readSetting("inspector", "shown") === "hidden") document.body.classList.add("inspectorHidden");
  $("inspectorToggle").setAttribute("aria-pressed", String(inspectorShown()));
}

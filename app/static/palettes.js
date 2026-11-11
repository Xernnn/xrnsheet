// The palettes: MuseScore's own, in its order, each cell applying to the selection with one click.
// A palette is only listed once the app can do what its cells stand for.
const KEY_NAMES = ["C♭", "G♭", "D♭", "A♭", "E♭", "B♭", "F", "C", "G", "D", "A", "E", "B", "F♯", "C♯"];
const MINOR_NAMES = ["A♭", "E♭", "B♭", "F", "C", "G", "D", "A", "E", "B", "F♯", "C♯", "G♯", "D♯", "A♯"];
const STAFF_TOP = 12; // Where a cell's staff starts, and how far apart its lines are.
const STAFF_SPACE = 5;
// Which line or space each sharp and flat of a key sits on, in half spaces down from the top line.
const SHARP_PLACES = [0, 3, -1, 2, 5, 1, 4];
const FLAT_PLACES = [4, 1, 5, 2, 6, 3, 7];
const PALETTE_STORE = "xrnsheet-palettes";
let paletteState = readPaletteState();
let palettes = null;

function readPaletteState() {
  try {
    const kept = JSON.parse(localStorage.getItem(PALETTE_STORE)) ?? {};
    return { open: kept.open ?? [], added: kept.added ?? [] };
  } catch {
    return { open: [], added: [] };
  }
}

function keepPaletteState() {
  try {
    localStorage.setItem(PALETTE_STORE, JSON.stringify(paletteState));
  } catch {
    // Without storage the palettes still work, and start closed next time.
  }
}

function keyName(fifths) {
  return `${KEY_NAMES[fifths + 7]} major, ${MINOR_NAMES[fifths + 7]} minor`;
}

function staffCell(width, inner) {
  let lines = "";
  for (let line = 0; line < 5; line++) {
    const y = STAFF_TOP + line * STAFF_SPACE;
    lines += `<line x1="0" x2="${width}" y1="${y}" y2="${y}" stroke="currentColor" stroke-width="0.55"/>`;
  }
  return `<svg class="cell" viewBox="0 0 ${width} 44" aria-hidden="true">${lines}${inner}</svg>`;
}

// Symbols on a cell's staff: `x` from the left, and half spaces down from the top line.
function glyphAt(codes, x, halfSpaces, anchor = "start") {
  const set = setGlyphs(codes);
  const scale = STAFF_SPACE / UNITS_PER_SPACE;
  const start = anchor === "middle" ? x - set.advance * scale / 2 : x;
  return `<g transform="translate(${start} ${STAFF_TOP + halfSpaces * STAFF_SPACE / 2}) scale(${scale})" fill="currentColor">${set.paths}</g>`;
}

function clefCell(glyph, line) {
  return staffCell(36, glyphAt(glyph, 9, line * 2));
}

function keyCell(fifths) {
  const places = fifths > 0 ? SHARP_PLACES : FLAT_PLACES;
  const count = Math.abs(fifths);
  const step = fifths > 0 ? 5.6 : 4.8;
  let signs = "";
  for (let index = 0; index < count; index++) signs += glyphAt(fifths > 0 ? "E262" : "E260", (52 - count * step) / 2 + index * step, places[index]);
  return staffCell(52, signs);
}

function timeDigits(number) {
  return [...String(number)].map(digit => `E08${digit}`).join(" ");
}

function timeCell(beats, beatType) {
  return staffCell(36, glyphAt(timeDigits(beats), 18, 2, "middle") + glyphAt(timeDigits(beatType), 18, 6, "middle"));
}

function glyphCell(codes, space = 6) {
  return glyphIcon(codes, space);
}

function wordsCell(text, style = "") {
  return `<span class="words ${style}">${text}</span>`;
}

function drawnCell(inner) {
  return `<svg class="cell" viewBox="0 0 44 44" aria-hidden="true"><g fill="none" stroke="currentColor">${inner}</g></svg>`;
}

function repeatCell(side) {
  const flip = side === "backward" ? ` transform="translate(44 0) scale(-1 1)"` : "";
  return staffCell(44, `<g${flip} stroke="currentColor"><line x1="17" x2="17" y1="12" y2="32" stroke-width="2.6"/><line x1="21.5" x2="21.5" y1="12" y2="32" stroke-width="0.9"/><circle cx="25.5" cy="19.5" r="1.4" fill="currentColor"/><circle cx="25.5" cy="24.5" r="1.4" fill="currentColor"/></g>`);
}

// A barline on a short piece of staff. Thin lines are the staff's own weight and thick ones a beam's.
const BARLINE_CELLS = {
  single: `<line x1="22" x2="22" y1="12" y2="32" stroke-width="0.9"/>`,
  double: `<line x1="20" x2="20" y1="12" y2="32" stroke-width="0.9"/><line x1="24" x2="24" y1="12" y2="32" stroke-width="0.9"/>`,
  dashed: `<line x1="22" x2="22" y1="12" y2="32" stroke-width="0.9" stroke-dasharray="3 2.67"/>`,
  final: `<line x1="19.5" x2="19.5" y1="12" y2="32" stroke-width="0.9"/><line x1="24" x2="24" y1="12" y2="32" stroke-width="2.6"/>`,
  heavy: `<line x1="22" x2="22" y1="12" y2="32" stroke-width="2.6"/>`,
  heavyDouble: `<line x1="19.5" x2="19.5" y1="12" y2="32" stroke-width="2.6"/><line x1="24.5" x2="24.5" y1="12" y2="32" stroke-width="2.6"/>`,
  dotted: `<line x1="22" x2="22" y1="12.7" y2="32" stroke-width="1.4" stroke-linecap="round" stroke-dasharray="0.01 2.65"/>`,
};

function barlineCell(style) {
  return staffCell(44, `<g stroke="currentColor">${BARLINE_CELLS[style]}</g>`);
}

function voltaCell(number) {
  const words = number ? `<text x="9" y="22" font-size="11" fill="currentColor" stroke="none" style="font-family: Edwin, C059, serif">${number}.</text>` : `<path d="M14 18l16 12M30 18L14 30" stroke-width="1.2"/>`;
  return drawnCell(`<path d="M5 30V12h34${number === 2 ? "v18" : ""}" stroke-width="1"/>${words}`);
}

function hairpinCell(kind) {
  return drawnCell(kind === "crescendo" ? `<path d="M38 15L6 22l32 7" stroke-width="1.1"/>` : `<path d="M6 15l32 7-32 7" stroke-width="1.1"/>`);
}

const SLUR_CELL = `<path d="M6 28c8-13 24-13 32 0c-8-10-24-10-32 0z" fill="currentColor" stroke-width="0.6"/>`;
const BREAK_CELL = `<path d="M33 12v12H12M17 19l-5 5 5 5" stroke-width="1.6"/>`;
const GRACE_CELL = `<g transform="translate(15 32) scale(0.014)" fill="currentColor" stroke="none">${setGlyphs("E1D7").paths}</g><path d="M13 22l13-8" stroke-width="1"/>`;
const ARPEGGIO_CELL = `<path d="M22 6c-4 2-4 4 0 6s4 4 0 6s-4 4 0 6s4 4 0 6s-4 4 0 6" stroke-width="1.6"/>`;

function tempoCell(codes, speed) {
  return `<span class="words plain">${glyphIcon(codes, 3.4)} = ${speed}</span>`;
}

function atNote(run) {
  return { needs: "note", run };
}

// Every palette, with the ones MuseScore shows at first marked as such. The rest are under Add Palettes.
function buildPalettes() {
  const key = fifths => ({ key: `key:${fifths}`, label: keyName(fifths), html: keyCell(fifths), run: () => barAction("key", { fifths }) });
  const time = (beats, beatType) => ({ key: `time:${beats}/${beatType}`, label: `${beats}/${beatType} time`, html: timeCell(beats, beatType), run: () => barAction("time", { beats, beat_type: beatType }) });
  const timeSign = (symbol, label, glyph, beats, beatType) => ({ key: `time:${symbol}`, label, html: staffCell(36, glyphAt(glyph, 18, 4, "middle")), run: () => barAction("time", { beats, beat_type: beatType, symbol }) });
  // A barline goes at the end of the selected measure, or of the last one of a selected stretch.
  const barline = (style, label) => ({ key: `barline:${style}`, label, html: barlineCell(style), ...atNote(() => barAction("barline", { style, note: range?.last ?? actionTarget() })) });
  const clef = (name, label, glyph, line) => ({ key: `clef:${name}`, label, html: clefCell(glyph, line), ...atNote(() => barAction("clef", { clef: name })) });
  const tempo = (words, speed) => ({ key: `tempo:${words} ${speed}`, label: `${words}, ${speed} beats a minute`, html: wordsCell(words, "bold"), ...atNote(() => markAction("tempo", { text: `${words} ${speed}` })) });
  const beat = (label, glyph, unit, dots) => ({ key: `tempo:${unit}${dots ? "." : ""}=80`, label: `${label} = 80`, html: tempoCell(glyph, 80), ...atNote(() => markAction("tempo", { text: "80", unit, dots })) });
  const dynamic = (name, glyph) => ({ key: `dynamic:${name}`, label: `${name} (dynamic)`, html: glyphCell(glyph), ...atNote(() => markAction("dynamic", { name })) });
  const mark = (name, label, glyph) => ({ key: `mark:${name}`, label, html: glyphCell(glyph, 8), ...atNote(() => markAction("toggle", { name })) });
  const jump = (name, label, html) => ({ key: `jump:${name}`, label, html: html ?? wordsCell(label), ...atNote(() => markAction("jump", { name })) });
  const hairpin = (name, label) => ({ key: `hairpin:${name}`, label, html: hairpinCell(name), ...atNote(() => markAction("hairpin", { name })) });
  const slur = { key: "slur", label: "Slur", html: drawnCell(SLUR_CELL), ...atNote(() => markAction("slur")) };
  const volta = (number, label) => ({ key: `ending:${number}`, label, html: voltaCell(number), ...atNote(() => barAction("ending", { number, last: range?.last ?? null })) });
  const repeat = (prefix, side, label) => ({ key: `${prefix}:${side}`, label, html: repeatCell(side), run: () => barAction(side) });
  return [
    { id: "clefs", name: "Clefs", usual: true, cells: [
      clef("treble", "Treble clef", "E050", 3), clef("treble8", "Treble clef 8va bassa", "E052", 3),
      clef("alto", "Alto clef", "E05C", 2), clef("tenor", "Tenor clef", "E05C", 1), clef("bass", "Bass clef", "E062", 1),
    ] },
    { id: "keys", name: "Key signatures", usual: true, wide: 56, cells: [1, 2, 3, 4, 5, 6, 7, -7, -6, -5, -4, -3, -2, -1, 0].map(key) },
    { id: "times", name: "Time signatures", usual: true, cells: [[2, 4], [3, 4], [4, 4], [5, 4], [6, 4], [3, 8], [4, 8], [5, 8], [6, 8], [7, 8], [9, 8], [12, 8]].map(pair => time(...pair)).concat(
      timeSign("common", "Common time", "E08A", 4, 4), timeSign("cut", "Cut time", "E08B", 2, 2), time(2, 2), time(3, 2)) },
    { id: "tempo", name: "Tempo", usual: true, textual: true, cells: [
      beat("Half note", "ECA3", "half", 0), beat("Quarter note", "ECA5", "quarter", 0), beat("Eighth note", "ECA7", "eighth", 0), beat("Dotted quarter note", "ECA5 ECB7", "quarter", 1),
      tempo("Grave", 35), tempo("Largo", 50), tempo("Lento", 52), tempo("Adagio", 71), tempo("Andante", 92), tempo("Moderato", 114),
      tempo("Allegretto", 116), tempo("Allegro", 144), tempo("Vivace", 172), tempo("Presto", 187),
      { key: "tempo:swing", label: "Swing", html: wordsCell("Swing", "plain"), ...atNote(() => markAction("swing", { on: true })) },
      { key: "tempo:straight", label: "Straight", html: wordsCell("Straight", "plain"), ...atNote(() => markAction("swing", { on: false })) },
    ] },
    { id: "accidentals", name: "Accidentals", usual: true, cells: [
      { key: "accidental:flat", label: "Flat", html: glyphCell("E260"), ...atNote(() => noteAction("flat")) },
      { key: "accidental:natural", label: "Natural", html: glyphCell("E261"), ...atNote(() => noteAction("natural")) },
      { key: "accidental:sharp", label: "Sharp", html: glyphCell("E262"), ...atNote(() => noteAction("sharp")) },
      { key: "accidental:doubleSharp", label: "Double sharp", html: glyphCell("E263"), ...atNote(() => noteAction("doubleSharp")) },
      { key: "accidental:doubleFlat", label: "Double flat", html: glyphCell("E264"), ...atNote(() => noteAction("doubleFlat")) },
    ] },
    { id: "dynamics", name: "Dynamics", usual: true, cells: [
      dynamic("ppp", "E52A"), dynamic("pp", "E52B"), dynamic("p", "E520"), dynamic("mp", "E52C"), dynamic("mf", "E52D"),
      dynamic("f", "E522"), dynamic("ff", "E52F"), dynamic("fff", "E530"), dynamic("fp", "E534"), dynamic("sf", "E536"),
      dynamic("sfz", "E539"), dynamic("sfp", "E537"), dynamic("rfz", "E53D"), dynamic("fz", "E535"),
      hairpin("crescendo", "Crescendo hairpin"), hairpin("diminuendo", "Diminuendo hairpin"),
    ] },
    { id: "articulations", name: "Articulations", usual: true, cells: [
      slur, mark("accent", "Accent", "E4A0"), mark("staccato", "Staccato", "E4A2"), mark("staccatissimo", "Staccatissimo", "E4A6"),
      mark("tenuto", "Tenuto", "E4A4"), mark("marcato", "Marcato", "E4AC"), mark("upbow", "Up bow", "E612"), mark("downbow", "Down bow", "E610"),
    ] },
    { id: "text", name: "Text", usual: true, textual: true, cells: [
      { key: "text:staff", label: "Staff text", html: wordsCell("Staff text", "plain"), ...atNote(() => askForText(EXPRESSIONS.staff, { action: "text", name: "staff" })) },
      { key: "text:expression", label: "Expression text", html: wordsCell("Expression"), ...atNote(() => askForText(EXPRESSIONS.expression, { action: "text", name: "expression" })) },
      { key: "text:rehearsal", label: "Rehearsal mark", html: wordsCell("<span style=\"border: 1px solid; padding: 0 4px\">A</span>", "bold"), ...atNote(() => markAction("rehearsal")) },
    ] },
    { id: "repeats", name: "Repeats & jumps", usual: true, wide: 60, cells: [
      jump("segno", "Segno", glyphCell("E047")), jump("coda", "Coda", glyphCell("E048")), jump("fine", "Fine"), jump("tocoda", "To Coda"),
      jump("dc", "D.C."), jump("dcfine", "D.C. al Fine"), jump("dccoda", "D.C. al Coda"), jump("dscoda", "D.S. al Coda"), jump("dsfine", "D.S. al Fine"), jump("ds", "D.S."),
      repeat("repeat", "forward", "Left (start) repeat sign"), repeat("repeat", "backward", "Right (end) repeat sign"),
      volta(1, "Prima volta"), volta(2, "Seconda volta"), volta(0, "Remove the volta"),
    ] },
    { id: "barlines", name: "Barlines", usual: true, cells: [
      barline("single", "Single barline"), barline("double", "Double barline"),
      repeat("barline", "forward", "Left (start) repeat sign"), repeat("barline", "backward", "Right (end) repeat sign"),
      barline("dashed", "Dashed barline"), barline("final", "Final barline"), barline("heavy", "Heavy barline"),
      barline("heavyDouble", "Heavy double barline"), barline("dotted", "Dotted barline"),
    ] },
    { id: "layout", name: "Layout", usual: true, cells: [
      { key: "layout:break", label: "System break", html: drawnCell(BREAK_CELL), ...atNote(() => barAction("break")) },
    ] },
    { id: "ornaments", name: "Ornaments", cells: [
      mark("turn", "Turn", "E567"), mark("trill", "Trill", "E566"), mark("prall", "Short trill", "E56C"), mark("mordent", "Mordent", "E56D"),
    ] },
    { id: "breaths", name: "Breaths & pauses", cells: [mark("fermata", "Fermata", "E4C0"), mark("breath", "Breath mark (comma)", "E4CE")] },
    { id: "grace", name: "Grace notes", cells: [{ key: "grace", label: "Acciaccatura", html: drawnCell(GRACE_CELL), ...atNote(() => addGrace()) }] },
    { id: "arpeggios", name: "Arpeggios & glissandos", cells: [{ key: "mark:arpeggio", label: "Arpeggio", html: drawnCell(ARPEGGIO_CELL), ...atNote(() => markAction("toggle", { name: "arpeggio" })) }] },
    { id: "lines", name: "Lines", cells: [
      { ...slur, key: "line:slur" }, { ...hairpin("crescendo", "Crescendo hairpin"), key: "line:crescendo" }, { ...hairpin("diminuendo", "Diminuendo hairpin"), key: "line:diminuendo" },
      { ...volta(1, "Prima volta"), key: "line:ending1" }, { ...volta(2, "Seconda volta"), key: "line:ending2" },
    ] },
  ];
}

function cellButton(cell) {
  const button = document.createElement("button");
  button.dataset.cell = cell.key;
  button.title = cell.label;
  button.setAttribute("aria-label", cell.label);
  if (cell.needs) button.dataset.needs = cell.needs;
  button.innerHTML = cell.html;
  button.addEventListener("click", () => { button.blur(); cell.run(); });
  return button;
}

function paletteElement(palette, cells, open) {
  const holder = document.createElement("div");
  holder.className = "palette" + (palette.textual ? " textual" : "");
  holder.dataset.palette = palette.id;
  if (palette.wide) holder.style.setProperty("--cell", `${palette.wide}px`);
  const name = document.createElement("button");
  name.className = "name";
  name.textContent = palette.name;
  name.setAttribute("aria-expanded", String(open));
  name.addEventListener("click", () => togglePalette(palette.id));
  holder.append(name);
  if (open) {
    const grid = document.createElement("div");
    grid.className = "cells";
    grid.append(...cells.map(cellButton));
    holder.append(grid);
  }
  return holder;
}

function togglePalette(id) {
  paletteState.open = paletteState.open.includes(id) ? paletteState.open.filter(open => open !== id) : [...paletteState.open, id];
  keepPaletteState();
  drawPalettes();
}

// With something typed in the search box, every palette is looked through and only what matches is shown.
function drawPalettes() {
  palettes ??= buildPalettes();
  const wanted = $("paletteSearchRow").hidden ? "" : $("paletteSearch").value.trim().toLowerCase();
  const shown = [];
  for (const palette of palettes) {
    if (wanted) {
      const named = palette.name.toLowerCase().includes(wanted);
      const cells = palette.cells.filter(cell => named || cell.label.toLowerCase().includes(wanted));
      if (cells.length) shown.push(paletteElement(palette, cells, true));
    } else if (palette.usual || paletteState.added.includes(palette.id)) {
      shown.push(paletteElement(palette, palette.cells, paletteState.open.includes(palette.id)));
    }
  }
  if (!shown.length) {
    const none = document.createElement("div");
    none.className = "note";
    none.textContent = "Nothing in the palettes goes by that name.";
    shown.push(none);
  }
  $("paletteTree").replaceChildren(...shown);
  enableTools();
}

function morePaletteItems() {
  palettes ??= buildPalettes();
  return palettes.filter(palette => !palette.usual).map(palette => ({
    label: palette.name,
    checked: paletteState.added.includes(palette.id),
    run: () => {
      const added = paletteState.added.includes(palette.id);
      paletteState.added = added ? paletteState.added.filter(id => id !== palette.id) : [...paletteState.added, palette.id];
      if (!added && !paletteState.open.includes(palette.id)) paletteState.open.push(palette.id);
      keepPaletteState();
      drawPalettes();
    },
  }));
}

function setUpPalettes() {
  $("addPalettes").addEventListener("click", event => openMenu(event.currentTarget, morePaletteItems()));
  $("paletteSearchOpen").addEventListener("click", () => searchPalettes($("paletteSearchRow").hidden));
  $("paletteSearch").addEventListener("input", drawPalettes);
  $("paletteSearch").addEventListener("keydown", event => { if (event.key === "Escape") searchPalettes(false); });
  drawPalettes();
}

function searchPalettes(on) {
  $("paletteSearchRow").hidden = !on;
  if (on) $("paletteSearch").focus(); else $("paletteSearch").value = "";
  drawPalettes();
}

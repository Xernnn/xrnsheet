// The window around the score: tabs, menus, panels, dialogs, zoom and theme.
const ICONS = {
  play: `<path d="M8 5l11 7-11 7z"/>`,
  stop: `<rect x="6.5" y="6.5" width="11" height="11" rx="1"/>`,
  rewind: `<path d="M19 5L9 12l10 7zM5 5v14"/>`,
  loop: `<path d="M17 3l3 3-3 3M20 6H8a4 4 0 00-4 4v2M7 21l-3-3 3-3M4 18h12a4 4 0 004-4v-2"/>`,
  metronome: `<path d="M9.5 4h5L19 20H5zM12 15l6-9M7 15h10"/>`,
  cog: `<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.2 2.2M16.2 16.2l2.2 2.2M5.6 18.4l2.2-2.2M16.2 7.8l2.2-2.2"/>`,
  undo: `<path d="M8 6L4 10l4 4M4 10h10a5 5 0 010 10h-3"/>`,
  redo: `<path d="M16 6l4 4-4 4M20 10H10a5 5 0 000 10h3"/>`,
  search: `<circle cx="11" cy="11" r="6"/><path d="M16 16l4 4"/>`,
  zoomIn: `<circle cx="11" cy="11" r="6"/><path d="M16 16l4 4M8.5 11h5M11 8.5v5"/>`,
  zoomOut: `<circle cx="11" cy="11" r="6"/><path d="M16 16l4 4M8.5 11h5"/>`,
  parts: `<path d="M7 3h8l4 4v14H7zM15 3v4h4M10 12h6M10 16h6"/>`,
  mixer: `<path d="M6 4v16M12 4v16M18 4v16M4 9h4M10 15h4M16 8h4"/>`,
  plus: `<path d="M12 5v14M5 12h14"/>`,
  trash: `<path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13M10 11v6M14 11v6"/>`,
  close: `<path d="M7 7l10 10M17 7L7 17"/>`,
  pen: `<path d="M4 20l1-4L16 5l3 3L8 19zM14 7l3 3"/>`,
  music: `<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>`,
  arrow: `<path d="M5 12h14M13 6l6 6-6 6"/>`,
  tie: `<ellipse cx="6" cy="9" rx="3.2" ry="2.3" fill="currentColor" stroke="none" transform="rotate(-20 6 9)"/><ellipse cx="18" cy="9" rx="3.2" ry="2.3" fill="currentColor" stroke="none" transform="rotate(-20 18 9)"/><path d="M6 15c4 4 8 4 12 0"/>`,
  slur: `<ellipse cx="5.5" cy="17" rx="2.8" ry="2" fill="currentColor" stroke="none" transform="rotate(-20 5.5 17)"/><ellipse cx="12" cy="14" rx="2.8" ry="2" fill="currentColor" stroke="none" transform="rotate(-20 12 14)"/><ellipse cx="18.5" cy="17" rx="2.8" ry="2" fill="currentColor" stroke="none" transform="rotate(-20 18.5 17)"/><path d="M4 10c5-6 11-6 16 0"/>`,
  tuplet: `<path d="M3.5 8V5h5M20.5 8V5h-5"/><text x="12" y="8" font-size="8" text-anchor="middle" fill="currentColor" stroke="none" font-family="serif" font-style="italic" font-weight="700">3</text><path d="M6.5 19v-8h12v8M12.5 19v-8" stroke-width="1.2"/><ellipse cx="4.6" cy="19" rx="2.2" ry="1.6" fill="currentColor" stroke="none"/><ellipse cx="10.6" cy="19" rx="2.2" ry="1.6" fill="currentColor" stroke="none"/><ellipse cx="16.6" cy="19" rx="2.2" ry="1.6" fill="currentColor" stroke="none"/>`,
  fork: `<path d="M9 3v7a3 3 0 006 0V3M12 13v8"/>`,
  flip: `<path d="M17 4v16M14 7l3-3 3 3M14 17l3 3 3-3"/><ellipse cx="7" cy="15" rx="3.2" ry="2.3" fill="currentColor" stroke="none" transform="rotate(-20 7 15)"/><path d="M10 14.5V5"/>`,
};
const ZOOMS = [25, 50, 75, 100, 125, 150, 200, 300, 400];
const PAGE_WIDTH_MM = 210, PAGE_HEIGHT_MM = 297, MM_TO_PX = 96 / 25.4;
const DESK_PADDING_PX = 48;
const FAMILIES = ["Woodwinds", "Free Reed", "Brass", "Percussion - Pitched", "Vocals", "Keyboards", "Strings - Plucked", "Strings - Bowed", "Plain staves"];
const SHORTCUTS = [
  ["N", "Start or stop note input"],
  ["A to G", "Write that note, then move on"],
  ["Shift+A to G", "Add that note to the chord just written"],
  ["1 to 7", "Duration, from a 64th to a whole note. 5 is a quarter note"],
  [".", "Augmentation dot"],
  ["0", "Rest"],
  ["T", "Tie to the next note"],
  ["+  -  =", "Sharp, flat, natural"],
  ["↑  ↓", "Move a note a semitone. With Ctrl, an octave"],
  ["Alt+Shift+↑  ↓", "Move a note one step of the scale"],
  ["A to G, on a selected note", "Rename it. With Shift, add that note to its chord"],
  ["Alt+2 to 9", "Add a note that interval above"],
  ["Q  W", "Half or twice as long. With Shift, through the dotted lengths"],
  ["←  →", "Previous or next note"],
  ["Ctrl+←  →", "Previous or next measure"],
  ["Ctrl+Home, Ctrl+End", "First or last note of the staff"],
  ["Shift+←  →, Shift+click", "Select a stretch of notes. Shift with the keys above stretches it further"],
  ["Click an empty part of a measure", "Select the whole measure"],
  ["Ctrl+A", "Select everything on the staff"],
  ["Ctrl+C  X  V", "Copy, cut, paste"],
  ["R", "Repeat this note or stretch"],
  ["Ctrl+Z, Ctrl+Shift+Z", "Undo, redo"],
  ["Del", "Make a rest"],
  ["Ctrl+Del", "Remove the selected range: whole measures go, anything less pulls later notes in"],
  ["Ins, Ctrl+B", "Insert a measure before the selection, or add one at the end"],
  ["Ctrl+3", "Triplet from the chosen duration. 5, 6 and 7 make other tuplets"],
  ["S", "Slur a stretch, or this note to the next"],
  ["<  >", "Crescendo, diminuendo"],
  ["Shift+S  V  N  O", "Staccato, accent, tenuto, marcato"],
  ["/", "Acciaccatura before this note"],
  ["J", "Respell: the same note under its other name"],
  ["X", "Flip the stem"],
  ["Ctrl+Alt+1 to 4", "Write in that voice, or move the selection to it"],
  ["Enter", "System break at this measure"],
  ["Ctrl+L", "Lyrics. Space moves on, - joins syllables, ↓ ↑ change verse, Enter finishes"],
  ["Ctrl+K", "Chord symbol"],
  ["Ctrl+T, Ctrl+E, Ctrl+M", "Staff text, expression text, rehearsal mark"],
  ["Alt+Shift+T", "Tempo marking"],
  ["Space", "Play or stop"],
  ["F9, F7, F8", "Show or hide Palettes, Instruments, Properties"],
  ["Ctrl+=, Ctrl+-, Ctrl+0", "Zoom in, zoom out, 100%"],
  ["Drag a selected note", "Move it up or down"],
  ["Click a dynamic, slur or text", "Select it. Del removes it, and a double-click or F2 changes it"],
  ["Double-click the title", "Change the title and composer"],
  ["Ctrl+wheel, drag the desk", "Zoom, and move the pages about"],
  ["Right-click", "A menu for the note, measure or mark under the pointer"],
  ["Click the staff", "In note input, place a note there. Shift+click adds it to a chord"],
];
let openMenus = [];
let instruments = [];
let zoom = readSetting("zoom", "100");
let sidePanel = "palettes";
let chosenPart = null; // Which instrument the Instruments panel has selected.

function icon(name) {
  return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;
}

function readSetting(name, usual) {
  try {
    return localStorage.getItem(`xrnsheet-${name}`) ?? usual;
  } catch {
    return usual;
  }
}

function keepSetting(name, value) {
  try {
    localStorage.setItem(`xrnsheet-${name}`, value);
  } catch {
    // Without storage the choice lasts until the page is closed.
  }
}

function applyTheme(choice) {
  if (choice === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = choice;
  keepSetting("theme", choice);
}

function originalHidden() {
  return readSetting("original", "shown") === "hidden";
}

function showOriginal(on) {
  keepSetting("original", on ? "shown" : "hidden");
  $("original").hidden = !on || !score || score.blank;
  drawCursor();
}

// Menus. An item is { label, keys, run, enabled, checked, items }, and null is a dividing line.
function closeMenus(from = 0) {
  for (const open of openMenus.splice(from)) {
    open.element.remove();
    open.owner?.setAttribute("aria-expanded", "false");
  }
}

function openMenu(owner, items, beside = false) {
  const level = beside ? openMenus.length : 0;
  closeMenus(level);
  const menu = document.createElement("div");
  menu.className = "menu";
  menu.setAttribute("role", "menu");
  for (const item of items) {
    if (!item) { menu.append(document.createElement("hr")); continue; }
    const button = document.createElement("button");
    button.setAttribute("role", item.checked === undefined ? "menuitem" : "menuitemcheckbox");
    if (item.checked !== undefined) button.setAttribute("aria-checked", String(item.checked));
    button.disabled = item.enabled === false;
    const label = document.createElement("span");
    label.textContent = item.label;
    button.append(label);
    if (item.items) {
      button.insertAdjacentHTML("beforeend", `<span class="more">▸</span>`);
      button.setAttribute("aria-haspopup", "menu");
      const openInner = () => { if (button.getAttribute("aria-expanded") !== "true") openMenu(button, item.items, true); };
      button.addEventListener("click", openInner);
      button.addEventListener("mouseenter", () => { closeMenus(level + 1); openInner(); });
    } else {
      if (item.keys) button.insertAdjacentHTML("beforeend", `<span class="keys">${item.keys}</span>`);
      button.addEventListener("mouseenter", () => closeMenus(level + 1));
      button.addEventListener("click", () => { closeMenus(); item.run(); });
    }
    menu.append(button);
  }
  document.body.append(menu);
  const at = owner.getBoundingClientRect();
  const left = beside ? at.right - 2 : at.left;
  const top = beside ? at.top - 4 : at.bottom + 2;
  menu.style.left = `${Math.max(4, Math.min(left, innerWidth - menu.offsetWidth - 4))}px`;
  menu.style.top = `${Math.max(4, Math.min(top, innerHeight - menu.offsetHeight - 4))}px`;
  owner.setAttribute("aria-expanded", "true");
  openMenus.push({ element: menu, owner });
  return menu;
}

// Open a menu at a point, as a right-click does.
function openMenuAt(x, y, items) {
  const point = { getBoundingClientRect: () => ({ left: x, right: x, top: y, bottom: y - 2 }), setAttribute() {}, contains: () => false, parentElement: null };
  return openMenu(point, items);
}

// Items of a menu-bar menu by their labels, so another menu offers the very same actions.
function menuItems(menu, ...labels) {
  const items = menuList().find(item => item.label === menu).items;
  return labels.map(label => items.find(item => item?.label === label));
}

function addItems() {
  const target = Boolean(actionTarget());
  return [
    { label: "Notes", items: [
      { label: "Note input", keys: "N", checked: editor.on, run: toggleInput },
      { label: "Acciaccatura", keys: "/", enabled: target, run: addGrace },
    ] },
    { label: "Tuplets", enabled: target, items: [
      { label: "Triplet", keys: "Ctrl+3", run: () => toggleTuplet(3) },
      { label: "Quintuplet", keys: "Ctrl+5", run: () => toggleTuplet(5) },
      { label: "Sextuplet", keys: "Ctrl+6", run: () => toggleTuplet(6) },
      { label: "Septuplet", keys: "Ctrl+7", run: () => toggleTuplet(7) },
    ] },
    { label: "Measures", items: [
      { label: "Insert one measure before selection", keys: "Ins", enabled: target, run: () => barAction("insert") },
      { label: "Insert one measure at end of score", keys: "Ctrl+B", run: appendBar },
    ] },
    { label: "Text", enabled: target, items: [
      { label: "Staff text", keys: "Ctrl+T", run: () => askForText(EXPRESSIONS.staff, { action: "text", name: "staff" }) },
      { label: "Expression text", keys: "Ctrl+E", run: () => askForText(EXPRESSIONS.expression, { action: "text", name: "expression" }) },
      { label: "Rehearsal mark", keys: "Ctrl+M", run: () => markAction("rehearsal") },
      { label: "Tempo marking", keys: "Alt+Shift+T", run: askForTempo },
      { label: "Chord symbol", keys: "Ctrl+K", run: addChordHere },
      { label: "Lyrics", keys: "Ctrl+L", run: startLyrics },
    ] },
    { label: "Lines", enabled: target, items: [
      { label: "Slur", keys: "S", run: () => markAction("slur") },
      { label: "Crescendo", keys: "<", run: () => markAction("hairpin", { name: "crescendo" }) },
      { label: "Diminuendo", keys: ">", run: () => markAction("hairpin", { name: "diminuendo" }) },
      null,
      { label: "Ottava 8va alta", run: () => markAction("ottava", { name: "8va" }) },
      { label: "Ottava 8va bassa", run: () => markAction("ottava", { name: "8vb" }) },
      { label: "Ottava 15ma alta", run: () => markAction("ottava", { name: "15ma" }) },
      { label: "Ottava 15ma bassa", run: () => markAction("ottava", { name: "15mb" }) },
      null,
      { label: "Pedal", run: () => markAction("pedal", { name: "pedstar" }) },
      { label: "Pedal (straight hooks)", run: () => markAction("pedal", { name: "line" }) },
    ] },
  ];
}

function menuList() {
  const open = Boolean(score) && !$("workspace").hidden;
  const target = open && Boolean(actionTarget());
  const theme = readSetting("theme", "system");
  const sideShown = name => open && !document.body.classList.contains("sideHidden") && sidePanel === name;
  return [
    { label: "File", items: [
      { label: "New…", run: openNewScore },
      { label: "Open…", run: () => $("file").click() },
      { label: "Close", enabled: open, run: closeScore },
      null,
      { label: "Project properties…", enabled: open, run: openProject },
      { label: "Parts…", enabled: open, run: openParts },
      null,
      { label: "Export", enabled: open, items: [
        { label: "PDF", run: () => $("pdfSave").click() },
        { label: "MusicXML", run: () => $("xml").click() },
        { label: "MIDI", run: () => $("midi").click() },
        { label: "Audio (WAV)", run: saveAudio },
      ] },
      { label: "Print…", enabled: open, run: () => window.open($("pdf").href, "_blank") },
    ] },
    { label: "Edit", items: [
      { label: "Undo", keys: "Ctrl+Z", enabled: open && score.canUndo, run: undoEdit },
      { label: "Redo", keys: "Ctrl+Shift+Z", enabled: open && score.canRedo, run: redoEdit },
      null,
      { label: "Cut", keys: "Ctrl+X", enabled: target, run: cutSelection },
      { label: "Copy", keys: "Ctrl+C", enabled: target, run: copySelection },
      { label: "Paste", keys: "Ctrl+V", enabled: open && canPaste(), run: pasteHere },
      null,
      { label: "Select all", keys: "Ctrl+A", enabled: open, run: selectEverything },
      { label: "Delete", keys: "Del", enabled: target, run: () => noteAction("delete") },
      { label: "Delete selected measures", enabled: target, run: deleteBars },
    ] },
    { label: "View", items: [
      { label: "Palettes", keys: "F9", checked: sideShown("palettes"), enabled: open, run: () => toggleSide("palettes") },
      { label: "Instruments", keys: "F7", checked: sideShown("instruments"), enabled: open, run: () => toggleSide("instruments") },
      { label: "Properties", keys: "F8", checked: sideShown("properties"), enabled: open, run: () => toggleSide("properties") },
      { label: "Original sheet", checked: open && !$("original").hidden, enabled: open && !score.blank, run: () => showOriginal($("original").hidden) },
      null,
      { label: "Concert pitch", checked: open && Boolean(score.concertPitch), enabled: open, run: toggleConcertPitch },
      null,
      { label: "Zoom in", keys: "Ctrl+=", enabled: open, run: () => stepZoom(1) },
      { label: "Zoom out", keys: "Ctrl+-", enabled: open, run: () => stepZoom(-1) },
      null,
      { label: "Theme", items: [
        { label: "Follow the system", checked: theme === "system", run: () => applyTheme("system") },
        { label: "Light", checked: theme === "light", run: () => applyTheme("light") },
        { label: "Dark", checked: theme === "dark", run: () => applyTheme("dark") },
      ] },
    ] },
    { label: "Add", enabled: open, items: open ? addItems() : [] },
    { label: "Format", enabled: open, items: [
      { label: "Add/remove system breaks…", run: openBreaks },
    ] },
    { label: "Tools", enabled: open, items: [
      { label: "Transpose…", run: openTranspose },
      null,
      { label: "Voices", enabled: target, items: [1, 2, 3, 4].map(voice => ({ label: `Move to voice ${voice}`, keys: `Ctrl+Alt+${voice}`, run: () => chooseVoice(voice) })) },
      { label: "Respell pitches", keys: "J", enabled: target, run: () => noteAction("respell") },
      { label: "Repeat selection", keys: "R", enabled: target, run: repeatSelection },
      null,
      { label: "Remove selected range", keys: "Ctrl+Del", enabled: target, run: removeRange },
      { label: "Insert a beat here", enabled: target, run: () => noteAction("insert") },
      { label: "Remove articulations and ornaments", enabled: target, run: () => noteAction("clear") },
    ] },
    { label: "Help", items: [
      { label: "Keyboard shortcuts…", run: () => $("keysDialog").showModal() },
      { label: "About xrnsheet…", run: () => $("aboutDialog").showModal() },
    ] },
  ];
}

function setUpMenus() {
  const bar = $("menuBar");
  bar.replaceChildren(...menuList().map((menu, index) => {
    const button = document.createElement("button");
    button.textContent = menu.label;
    button.setAttribute("role", "menuitem");
    button.setAttribute("aria-haspopup", "menu");
    button.dataset.menu = menu.label;
    const open = () => openMenu(button, menuList()[index].items);
    button.addEventListener("click", () => {
      if (button.getAttribute("aria-expanded") === "true") closeMenus();
      else if (menuList()[index].enabled !== false) open();
    });
    button.addEventListener("mouseenter", () => { if (openMenus.length && openMenus[0].owner.parentElement === bar && openMenus[0].owner !== button && menuList()[index].enabled !== false) open(); });
    return button;
  }));
  document.addEventListener("pointerdown", event => {
    if (!openMenus.length || event.target.closest(".menu")) return;
    if (openMenus.some(open => open.owner.contains(event.target))) return;
    closeMenus();
  });
  window.addEventListener("blur", () => closeMenus());
  $("addButton").addEventListener("click", event => {
    if (event.currentTarget.getAttribute("aria-expanded") === "true") closeMenus(); else openMenu(event.currentTarget, addItems());
  });
}

// Transposing instruments are shown as they sound, or as their players read them. The score keeps
// the choice, and it can be undone, as MuseScore does with its Concert pitch button.
function toggleConcertPitch() {
  return enqueue(() => send("concert", { on: !score.concertPitch }));
}

function appendBar() {
  return enqueue(() => send("bars", { action: "insert", note: null }));
}

function askForTempo() {
  return askForText("Tempo, e.g. Allegro 132", { action: "tempo" });
}

function canPaste() {
  return Boolean(clipboard) && Boolean(editor.on ? editor.cursor : selected);
}

// The panel on the left.
function showSide(name) {
  sidePanel = name;
  document.body.classList.remove("sideHidden");
  document.body.classList.add("sideOpen");
  $("side").hidden = false;
  for (const tab of document.querySelectorAll("#sideTabs button")) tab.setAttribute("aria-selected", String(tab.dataset.panel === name));
  for (const panel of ["palettes", "instruments", "properties"]) $(`${panel}Panel`).hidden = panel !== name;
  drawCursor();
}

// What F9, F7 and F8 do: show that panel, or put the whole side away if it's the one showing.
function toggleSide(name) {
  const showing = !$("side").hidden && sidePanel === name && (innerWidth > 860 || document.body.classList.contains("sideOpen"));
  if (!showing) { showSide(name); return; }
  document.body.classList.add("sideHidden");
  document.body.classList.remove("sideOpen");
  $("side").hidden = true;
  drawCursor();
}

// Zoom. 100% draws a page at its printed size, as MuseScore does.
function fitZoom(kind) {
  const desk = $("desk");
  const wide = (desk.clientWidth - DESK_PADDING_PX) / (PAGE_WIDTH_MM * MM_TO_PX);
  const tall = (desk.clientHeight - DESK_PADDING_PX) / (PAGE_HEIGHT_MM * MM_TO_PX);
  return Math.max(0.1, kind === "width" ? wide : Math.min(wide, tall));
}

function setZoom(value) {
  zoom = String(value);
  keepSetting("zoom", zoom);
  const factor = zoom === "width" || zoom === "page" ? fitZoom(zoom) : Number(zoom) / 100;
  document.documentElement.style.setProperty("--zoom", String(factor));
  const choice = $("zoomChoice");
  if (![...choice.options].some(option => option.value === zoom)) choice.append(new Option(`${zoom}%`, zoom));
  choice.value = zoom;
  drawCursor();
}

function stepZoom(by) {
  const now = Number(getComputedStyle(document.documentElement).getPropertyValue("--zoom")) * 100;
  const next = by > 0 ? ZOOMS.find(level => level > now + 0.5) : ZOOMS.findLast(level => level < now - 0.5);
  if (next) setZoom(next);
}

function setUpZoom() {
  $("zoomChoice").replaceChildren(...ZOOMS.map(level => new Option(`${level}%`, level)), new Option("Page width", "width"), new Option("Whole page", "page"));
  $("zoomChoice").addEventListener("change", event => { setZoom(event.target.value); event.target.blur(); });
  $("zoomIn").addEventListener("click", () => stepZoom(1));
  $("zoomOut").addEventListener("click", () => stepZoom(-1));
  window.addEventListener("resize", () => { if (zoom === "width" || zoom === "page") setZoom(zoom); });
  setZoom(zoom);
}

// Choosing instruments: families, what's in the chosen family, and what has been picked so far.
function makeChooser(holder, onChange) {
  const state = { family: FAMILIES[0], picked: [], chosen: null, wanted: "" };
  holder.innerHTML = `<div><h3>Family</h3><ul class="pickList" data-list="families" role="listbox" aria-label="Family"></ul></div>
    <div><h3>Instruments</h3><input type="search" placeholder="Search" aria-label="Search instruments" style="margin-bottom: 8px"><ul class="pickList" data-list="instruments" role="listbox" aria-label="Instruments"></ul></div>
    <div><h3>Your score</h3><ul class="pickList" data-list="picked" aria-label="Instruments chosen"></ul><div class="note" data-empty>Choose your instruments by adding them to this list.</div></div>`;
  const list = name => holder.querySelector(`[data-list="${name}"]`);
  const add = id => { state.picked.push(id); draw(); onChange(state.picked); };
  function draw() {
    list("families").replaceChildren(...FAMILIES.filter(family => instruments.some(item => item.family === family)).map(family => {
      const row = document.createElement("li");
      row.textContent = family;
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", String(!state.wanted && family === state.family));
      row.addEventListener("click", () => { state.family = family; state.wanted = holder.querySelector("input").value = ""; draw(); });
      return row;
    }));
    const offered = instruments.filter(item => (state.wanted ? item.label.toLowerCase().includes(state.wanted) : item.family === state.family));
    list("instruments").replaceChildren(...offered.map(item => {
      const row = document.createElement("li");
      row.dataset.instrument = item.id;
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", String(state.chosen === item.id));
      row.title = "Double-click to add";
      row.innerHTML = `<span></span><button type="button" class="flat" aria-label="Add ${item.label}" title="Add to the score">${icon("arrow")}</button>`;
      row.firstChild.textContent = item.label;
      row.addEventListener("click", () => { state.chosen = item.id; draw(); });
      row.addEventListener("dblclick", () => add(item.id));
      row.querySelector("button").addEventListener("click", event => { event.stopPropagation(); add(item.id); });
      return row;
    }));
    list("picked").replaceChildren(...state.picked.map((id, index) => {
      const row = document.createElement("li");
      row.innerHTML = `<span></span><button type="button" class="flat" aria-label="Remove" title="Remove from the score">${icon("trash")}</button>`;
      row.firstChild.textContent = instruments.find(item => item.id === id)?.label ?? id;
      row.querySelector("button").addEventListener("click", () => { state.picked.splice(index, 1); draw(); onChange(state.picked); });
      return row;
    }));
    holder.querySelector("[data-empty]").hidden = state.picked.length > 0;
  }
  holder.querySelector("input").addEventListener("input", event => { state.wanted = event.target.value.trim().toLowerCase(); draw(); });
  // Enter in the search box would otherwise press the dialog's main button.
  holder.querySelector("input").addEventListener("keydown", event => { if (event.key === "Enter") event.preventDefault(); });
  state.reset = () => { state.picked = []; state.chosen = null; state.wanted = holder.querySelector("input").value = ""; state.family = FAMILIES[0]; draw(); onChange(state.picked); };
  draw();
  return state;
}

async function loadInstruments() {
  if (instruments.length) return;
  const response = await fetch("/api/instruments");
  if (response.ok) instruments = await response.json();
}

let newChooser = null, partChooser = null;

function showNewPage(details) {
  $("newInstruments").hidden = details;
  $("newDetails").hidden = !details;
  $("blankBack").hidden = !details;
  $("blankNext").hidden = details;
}

async function openNewScore() {
  await loadInstruments();
  newChooser ??= makeChooser($("newChooser"), picked => { $("blankNext").disabled = $("blankDone").disabled = picked.length === 0; });
  newChooser.reset();
  $("blankError").hidden = true;
  for (const id of ["blankTitle", "blankComposer", "blankTempo"]) $(id).value = "";
  showNewPage(false);
  $("newDialog").showModal();
}

async function createScore() {
  const [beats, beat_type, symbol = ""] = $("blankTime").value.split("/");
  const [first, ...more] = newChooser.picked;
  const body = {
    fifths: Number($("blankKey").value), beats: Number(beats), beat_type: Number(beat_type), symbol, clef: first, more, measures: Number($("blankBars").value),
    title: $("blankTitle").value, composer: $("blankComposer").value, tempo: $("blankTempo").value, pickup: Number($("blankPickup").value),
  };
  const response = await fetch("/api/scores/blank", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) {
    showNewPage(true);
    $("blankError").textContent = typeof data.detail === "string" ? data.detail : "Those settings didn't work.";
    $("blankError").hidden = false;
    return;
  }
  $("newDialog").close();
  await openScore(data.id);
  setInput(true);
}

function setUpNewScore() {
  $("blankOpen").addEventListener("click", openNewScore);
  $("blankCancel").addEventListener("click", () => $("newDialog").close());
  $("blankNext").addEventListener("click", () => showNewPage(true));
  $("blankBack").addEventListener("click", () => showNewPage(false));
  $("blankForm").addEventListener("submit", event => { event.preventDefault(); createScore(); });
  $("blankKey").append(...KEY_NAMES.map((name, index) => new Option(keyName(index - 7), index - 7)));
  $("blankKey").value = "0";
}

// The Instruments panel lists what's in the score. Selecting a note selects its instrument here.
function partOfStaff(staff) {
  let seen = 0;
  const counts = score?.partStaves ?? [];
  for (let part = 0; part < counts.length; part++) {
    seen += counts[part];
    if (staff < seen) return part;
  }
  return null;
}

function drawParts() {
  const names = score?.parts ?? [];
  if (chosenPart !== null && chosenPart >= names.length) chosenPart = null;
  $("partList").replaceChildren(...names.map((name, index) => {
    const row = document.createElement("li");
    row.setAttribute("role", "option");
    row.setAttribute("aria-selected", String(index === chosenPart));
    row.innerHTML = "<span></span>";
    row.firstChild.textContent = name || `Instrument ${index + 1}`;
    row.addEventListener("click", () => { chosenPart = index; drawParts(); });
    return row;
  }));
  $("partRemove").disabled = chosenPart === null || names.length < 2;
}

async function openAddPart() {
  await loadInstruments();
  partChooser ??= makeChooser($("partChooser"), picked => { $("partOk").disabled = picked.length === 0; });
  partChooser.reset();
  $("partDialog").showModal();
}

function openParts() {
  const names = score.parts ?? [];
  let chosen = 0;
  const draw = () => $("partsList").replaceChildren(...names.map((name, index) => {
    const row = document.createElement("li");
    row.setAttribute("role", "option");
    row.setAttribute("aria-selected", String(index === chosen));
    row.textContent = name || `Instrument ${index + 1}`;
    row.addEventListener("click", () => { chosen = index; draw(); });
    row.addEventListener("dblclick", () => { chosen = index; $("partsOpen").click(); });
    return row;
  }));
  draw();
  $("partsOpen").onclick = event => {
    event.preventDefault();
    window.open(`/api/scores/${scoreId}/score.pdf?part=${chosen}`, "_blank");
    $("partsDialog").close();
  };
  $("partsDialog").showModal();
}

function openProject() {
  $("titleInput").value = score.title ?? "";
  $("composerInput").value = score.composer ?? "";
  $("projectDialog").showModal();
  $("titleInput").select();
}

function openTranspose() {
  const first = range?.first ?? (selected && !editor.on ? selected : null);
  $("transposeWhat").textContent = first ? "The selected notes are transposed." : "Nothing is selected, so the whole score is transposed, with its key signatures.";
  $("transposeError").hidden = true;
  // A stretch can only move by an interval: a key belongs to the whole score.
  const toKey = document.querySelector('input[name="transposeMode"][value="key"]');
  toKey.disabled = $("transposeKey").disabled = Boolean(first);
  if (first) document.querySelector('input[name="transposeMode"][value="interval"]').checked = true;
  $("transposeDialog").showModal();
}

function openBreaks() {
  const count = score.barsPerLine ?? 0;
  document.querySelector(`input[name="breaksMode"][value="${count ? "every" : "fit"}"]`).checked = true;
  if (count) $("breaksCount").value = count;
  $("breaksDialog").showModal();
}

// Which sections of Properties apply depends on what's selected, as in MuseScore.
function drawProperties() {
  const target = score ? actionTarget() : null;
  const element = target && document.getElementById(target);
  const mark = selectedMark && score?.marks.find(item => item.id === selectedMark);
  $("propMark").hidden = !mark;
  if (mark) {
    $("propMarkKind").textContent = MARK_NAMES[mark.kind] ?? "Mark";
    $("markEdit").hidden = !markEditable(mark);
  }
  $("propScore").hidden = Boolean(element) || Boolean(mark);
  $("propNote").hidden = $("propBeats").hidden = !element;
  if (element) $("propKind").textContent = range ? "Range" : element.matches("g.note") ? "Note" : "Rest";
  if (!editor.on && element) {
    const part = partOfStaff(staffNumber(element));
    if (part !== null && part !== chosenPart) { chosenPart = part; drawParts(); }
  }
}

// "Ctrl+Shift+Z, Ctrl+Y" becomes keys drawn as keys, with what joins them left as words.
function keysHtml(keys) {
  const cap = key => `<kbd>${key.replace("<", "&lt;").replace(">", "&gt;")}</kbd>`;
  return keys.split(/(, | {2}| to )/).map((piece, index) => {
    if (index % 2) return piece === "  " ? " " : piece;
    return piece.length > 1 && piece.includes("+") ? piece.split("+").map(cap).join("+") : cap(piece);
  }).join("");
}

function setUpShell() {
  document.querySelectorAll("[data-icon]").forEach(holder => { holder.outerHTML = icon(holder.dataset.icon); });
  document.querySelectorAll("[data-glyph]").forEach(holder => { holder.outerHTML = glyphIcon(holder.dataset.glyph, Number(holder.dataset.space)); });
  applyTheme(readSetting("theme", "system"));
  setUpMenus();
  setUpZoom();
  setUpNewScore();
  $("homeTab").addEventListener("click", () => show("start"));
  $("scoreTab").addEventListener("click", () => { publishing = false; show("workspace"); });
  $("publishTab").addEventListener("click", () => { publishing = true; show("workspace"); });
  $("closeScore").addEventListener("click", closeScore);
  $("originalClose").addEventListener("click", () => showOriginal(false));
  for (const tab of document.querySelectorAll("#sideTabs button")) tab.addEventListener("click", () => showSide(tab.dataset.panel));
  $("partsButton").addEventListener("click", openParts);
  $("concertPitch").addEventListener("click", toggleConcertPitch);
  $("partAdd").addEventListener("click", openAddPart);
  $("partRemove").addEventListener("click", () => enqueue(async () => {
    if (chosenPart === null) return;
    const index = chosenPart;
    chosenPart = null;
    await send("parts", { action: "remove", index });
  }));
  $("partDialog").querySelector("form").addEventListener("submit", () => {
    const names = [...partChooser.picked];
    enqueue(() => send("parts", { action: "add", names }));
  });
  // A dialog opened from a menu has nothing to hand the focus back to, since the menu has gone. Its
  // field would keep it after closing and swallow every key meant for the score.
  document.querySelectorAll("dialog").forEach(dialog => dialog.addEventListener("close", () => {
    if (dialog.contains(document.activeElement)) document.activeElement.blur();
  }));
  $("projectOpen").addEventListener("click", openProject);
  $("markEdit").addEventListener("click", openSelectedMark);
  $("markDelete").addEventListener("click", removeMark);
  $("projectDialog").querySelector("form").addEventListener("submit", () => {
    // Read now, because a reply arriving first redraws these fields with what the server knew.
    const body = { title: $("titleInput").value, composer: $("composerInput").value };
    enqueue(() => send("title", body));
  });
  $("breaksOpen").addEventListener("click", openBreaks);
  $("breaksDialog").querySelector("form").addEventListener("submit", () => {
    const every = document.querySelector('input[name="breaksMode"]:checked').value === "every";
    barAction("barsPerLine", { count: every ? Number($("breaksCount").value) : 0 });
  });
  $("transposeDialog").querySelector("form").addEventListener("submit", () => {
    const mode = document.querySelector('input[name="transposeMode"]:checked').value;
    transposeBy(mode === "key" ? `key:${$("transposeKey").value}` : `${$("transposeInterval").value}:${$("transposeDirection").value}`);
  });
  $("transposeKey").append(...KEY_NAMES.map((name, index) => new Option(keyName(index - 7), index - 7)));
  $("transposeKey").value = "0";
  $("transposeInterval").addEventListener("change", () => { document.querySelector('input[name="transposeMode"][value="interval"]').checked = true; });
  $("transposeKey").addEventListener("change", () => { document.querySelector('input[name="transposeMode"][value="key"]').checked = true; });
  $("breaksCount").addEventListener("input", () => { document.querySelector('input[name="breaksMode"][value="every"]').checked = true; });
  document.querySelectorAll("dialog [data-close]").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
  $("keysTable").innerHTML = SHORTCUTS.map(([keys, what]) => `<tr><td>${keysHtml(keys)}</td><td>${what}</td></tr>`).join("");
  $("playSettings").addEventListener("click", event => {
    const popup = $("playPopup");
    popup.hidden = !popup.hidden;
    const at = event.currentTarget.getBoundingClientRect();
    popup.style.top = `${at.bottom + 4}px`;
    popup.style.left = `${Math.max(4, Math.min(at.left, innerWidth - popup.offsetWidth - 4))}px`;
  });
  document.addEventListener("pointerdown", event => {
    if (!$("playPopup").hidden && !event.target.closest("#playPopup, #playSettings")) $("playPopup").hidden = true;
  });
  $("pickupChoice").addEventListener("change", event => {
    if (event.target.value !== "") barAction("pickup", { count: Number(event.target.value) });
    event.target.value = "";
    event.target.blur();
  });
  setUpPalettes();
}

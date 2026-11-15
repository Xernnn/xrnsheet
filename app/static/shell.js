// The window around the score: its menu, dialogs, zoom and theme, and the icons everything draws.
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
  library: `<path d="M4 4h4v16H4zM10 4h4v16h-4zM16.5 4.5l3.6 1-3.9 14.8-3.6-1"/>`,
  inspector: `<rect x="3.5" y="4" width="17" height="16" rx="2.5"/><path d="M14.5 4v16M17 8h1.5M17 11h1.5"/>`,
  collapse: `<path d="M14 6l-6 6 6 6M19 6v12"/>`,
  expand: `<path d="M10 6l6 6-6 6M5 6v12"/>`,
  dots: `<circle cx="5.5" cy="12" r="1.3" fill="currentColor"/><circle cx="12" cy="12" r="1.3" fill="currentColor"/><circle cx="18.5" cy="12" r="1.3" fill="currentColor"/>`,
  read: `<path d="M3 6.5c3-1.5 6-1.5 9 .5 3-2 6-2 9-.5V19c-3-1.5-6-1.5-9 .5-3-2-6-2-9-.5zM12 7v12.5"/>`,
  target: `<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r=".8" fill="currentColor"/>`,
  folder: `<path d="M3.5 7.5a2 2 0 012-2h4l2 2h7a2 2 0 012 2v8a2 2 0 01-2 2h-13a2 2 0 01-2-2z"/>`,
  camera: `<path d="M4 8.5a2 2 0 012-2h2l1.5-2h5L16 6.5h2a2 2 0 012 2v9a2 2 0 01-2 2H6a2 2 0 01-2-2z"/><circle cx="12" cy="13" r="3.5"/>`,
  up: `<path d="M6 15l6-6 6 6"/>`,
  down: `<path d="M6 9l6 6 6-6"/>`,
  piano: `<rect x="3.5" y="5" width="17" height="14" rx="1.5"/><path d="M8 5v14M12 5v14M16 5v14"/><path d="M6.5 5v7h3V5M14.5 5v7h3V5" fill="currentColor"/>`,
  guitar: `<path d="M14 10l6.5-6.5M18.5 3.5l2 2"/><path d="M14 10c-1.5-1.5-4-1.2-5 .4-.5.8-1.6 1-2.6 1.1-2.5.3-3.6 3.6-1.4 5.8 2.2 2.2 5.5 1.1 5.8-1.4.1-1 .3-2.1 1.1-2.6 1.6-1 1.9-3.5.4-5z"/><circle cx="9.5" cy="14.5" r="1.3"/>`,
  violin: `<path d="M12 2.5v4M10.5 6.5h3"/><path d="M12 6.5c-2.5 0-3.5 1.6-3.2 3.3.2 1-.8 1.4-1.3 2.1-1.2 1.7-.6 4.4 1 5.6 1.7 1.3 5.3 1.3 7 0 1.6-1.2 2.2-3.9 1-5.6-.5-.7-1.5-1.1-1.3-2.1.3-1.7-.7-3.3-3.2-3.3z"/><path d="M10.5 12.5c.6.4.6 1.1 0 1.5M13.5 12.5c-.6.4-.6 1.1 0 1.5M12 9v11"/>`,
  sound: `<path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z"/><path d="M15.5 9a4 4 0 010 6M18 6.5a7.5 7.5 0 010 11"/>`,
  voice: `<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M6 11a6 6 0 0012 0M12 17v4M9 21h6"/>`,
  clock: `<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>`,
  enlarge: `<path d="M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7"/>`,
  alert: `<path d="M12 4l9 16H3z"/><path d="M12 10v4.5M12 17.2v.3"/>`,
};
const ZOOMS = [25, 50, 75, 100, 125, 150, 200, 300, 400];
const PAGE_WIDTH_MM = 210, PAGE_HEIGHT_MM = 297, MM_TO_PX = 96 / 25.4;
const VIEW_NAMES = { page: "Pages", horizontal: "One line", vertical: "One long page" };
const DESK_PADDING_PX = 40; // The room round the pages on the desk, both sides together (#pagesFrame's padding).
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
  ["Ctrl+Shift+↑  ↓", "Draw the note on the staff above or below, for a pianist's other hand"],
  ["0 to 9, on the tablature", "Type a fret for the selected note, or while entering notes there, write it on the cursor's string. Two digits typed together are one fret"],
  ["Shift+0 to 7, on the tablature", "A rest, or a duration, as the digits are frets there"],
  ["Ctrl+↑  ↓, on the tablature", "Move the note to the string above or below, keeping its sound. While entering notes there, ↑ ↓ move the cursor"],
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
  ["[  ]", "Down-bow, up-bow"],
  ["/", "Acciaccatura before this note"],
  ["J", "Respell: the same note under its other name"],
  ["X", "Flip the stem"],
  ["Ctrl+Alt+1 to 4", "Write in that voice, or move the selection to it"],
  ["Enter", "System break at this measure"],
  ["Ctrl+L", "Lyrics. Space moves on, - joins syllables, ↓ ↑ change verse, Enter finishes"],
  ["Ctrl+K", "Chord symbol"],
  ["Ctrl+T, Ctrl+E, Ctrl+M", "Staff text, expression text, rehearsal mark"],
  ["Ctrl+F", "Type fingers for the selected notes: one for them all, or one each in turn, as 135"],
  ["Alt+Shift+T", "Tempo marking"],
  ["Space", "Play or stop, from the selected note. In Practice with a loop set, from its start, A"],
  ["Ctrl+Shift+P", "Find any action by name, with its key. / also opens it when no note is chosen"],
  ["Alt+R, Alt+W, Alt+P", "Read, Write or Practice"],
  ["F6", "Show or hide the library"],
  ["F8, F9", "Show or hide the inspector, on what's selected"],
  ["F7", "The inspector, on the score: its title, instruments and pages"],
  ["F10", "Show or hide the mixer"],
  ["F12", "Show or hide the timeline"],
  ["P", "Show or hide the instrument: keyboard, fretboard or fingerboard. In note input it writes the note played, and Shift adds it to the chord"],
  ["Space, Page Down, →", "In Read, the next page. Shift+Space, Page Up and ← go back"],
  ["Ctrl+=, Ctrl+-, Ctrl+0", "Zoom in, zoom out, 100%"],
  ["Drag a note", "Move it up or down the staff, its new name shown as it goes. Dragging the paper moves the pages"],
  ["Click a dynamic, slur or text", "Select it. Del removes it, and a double-click or F2 changes it"],
  ["Double-click the title", "Change the title and composer"],
  ["Ctrl+wheel, drag the desk", "Zoom, and move the pages about"],
  ["Right-click", "A menu for the note, measure or mark under the pointer"],
  ["Click the staff", "In note input, write the note the ghost shows there. Shift+click adds it to a chord. A click on a note already written selects it"],
  ["A key, fret or string below", "In note input, write that note. Otherwise make the selected note that note, or with Shift add it to its chord"],
];
let openMenus = [];
let instruments = [];
let zoom = "width";
let chosenPart = null; // The instrument being worked with: the panel below shows it, and practice follows it.

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
  // A menu beside another opens to its left when there's no room on its right.
  const roomRight = at.right + menu.offsetWidth < innerWidth - 4;
  const left = beside ? (roomRight ? at.right - 2 : at.left - menu.offsetWidth + 2) : Math.min(at.left, at.right - menu.offsetWidth);
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
  return [
    { label: "File", items: [
      { label: "New…", run: openNewScore },
      { label: "Open…", run: () => $("file").click() },
      { label: "Close", enabled: open, run: closeScore },
      { label: "Delete", enabled: open, run: () => removeSheet(scoreId) },
      null,
      { label: "Title and composer…", enabled: open, run: openProject },
      { label: "Checked, for retraining", checked: open && Boolean(score.checked), enabled: open && !score.blank, run: markChecked },
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
      ...Object.entries(MODES).map(([name, mode]) => ({ label: `${mode.label} mode`, keys: mode.keys, checked: open && currentMode() === name, enabled: open, run: () => setMode(name) })),
      null,
      { label: "Library", keys: "F6", checked: libraryShown(), run: toggleLibrary },
      { label: "Inspector", keys: "F8", checked: open && inspectorShown(), enabled: open, run: () => toggleInspector() },
      { label: "Original sheet", checked: open && !$("original").hidden, enabled: open && !score.blank, run: () => showOriginal($("original").hidden) },
      { label: instrumentLabel(), keys: "P", checked: open && dockOpen("instrument"), enabled: open, run: () => toggleDock("instrument") },
      { label: "Mixer", keys: "F10", checked: open && dockOpen("mixer"), enabled: open, run: () => toggleDock("mixer") },
      { label: "Timeline", keys: "F12", checked: open && dockOpen("timeline"), enabled: open, run: () => toggleDock("timeline") },
      null,
      { label: "Concert pitch", checked: open && Boolean(score.concertPitch), enabled: open, run: toggleConcertPitch },
      null,
      ...Object.entries(VIEW_NAMES).map(([view, label]) => ({ label, checked: open && (score.view ?? "page") === view, enabled: open, run: () => setView(view) })),
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
      { label: "Page settings…", run: openPageSettings },
      { label: "Spacing…", run: openStyle },
      null,
      { label: "Measures per line…", run: openBreaks },
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
      { label: "Find a command…", keys: "Ctrl+Shift+P", run: () => openCommands() },
      { label: "Keyboard shortcuts…", run: () => $("keysDialog").showModal() },
      { label: "About xrnsheet…", run: () => $("aboutDialog").showModal() },
    ] },
  ];
}

function setUpMenus() {
  // The whole menu, from one button: each of its menus opens beside it.
  $("mainMenu").addEventListener("click", event => {
    const owner = event.currentTarget;
    if (owner.getAttribute("aria-expanded") === "true") { closeMenus(); return; }
    openMenu(owner, menuList().map(item => (item.enabled === false ? { ...item, items: undefined, run: () => {} } : item)));
  });
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

// Pages as pages, or one long system across, or one long page down, as MuseScore's views are.
function setView(view) {
  return enqueue(() => send("view", { view }));
}

// Zoom. 100% draws a page at its printed size, as MuseScore does, which on a laptop leaves a staff
// space of six or seven pixels: neighbouring notes three pixels apart, too close to aim a click
// at. So a score opens fitted to the width it has: its music as wide as the desk, with a little of
// the paper either side, and the pages one below the other. That's a staff space of ten pixels or
// so in Write beside the inspector. In Read and Practice nothing sits beside the music, and fitted
// to a laptop's whole window it would be well over twice its printed size, a system or two to a
// screen, so there it's fitted no larger than one and a half times its printed size, in the middle
// of the window, and more of it is in sight at once. Write keeps its larger notes to aim at.
// The one-line view's music is as large as it would be on its page, as a line that long can't be
// fitted across. "Whole page" fits a page into the window, margins and all, and two side by side
// when the window has room for both.
const PAPER_BESIDE_MM = 5; // The paper left in sight either side of the music, fitted to the width.
const MOST_READING_ZOOM = 1.5; // The largest Read and Practice are fitted to the width, times the printed size.

function fitZoom(kind) {
  const desk = $("desk");
  const [drawnWidth, height] = score?.pageSizes?.[0] ?? [PAGE_WIDTH_MM, PAGE_HEIGHT_MM];
  const page = score?.layout ?? { width: drawnWidth, left: 15, right: 15 };
  const music = page.width - page.left - page.right + 2 * PAPER_BESIDE_MM;
  const room = desk.clientWidth - DESK_PADDING_PX;
  if (kind === "width") return Math.max(0.1, Math.min(room / (music * MM_TO_PX), writing() ? Infinity : MOST_READING_ZOOM));
  const tall = (desk.clientHeight - DESK_PADDING_PX) / (height * MM_TO_PX);
  if (score?.view === "horizontal") return Math.max(0.1, tall);
  return Math.max(0.1, Math.min(room / (drawnWidth * MM_TO_PX), score?.view === "vertical" ? Infinity : tall));
}

// Fitted to the width, pages go one below the other, read down as on paper, and the desk is
// centred on the music, with the edges of the paper beyond it.
function fittedToWidth() {
  return zoom === "width" && (score?.view ?? "page") !== "horizontal";
}

// Whole page shows the pages a row at a time, one below the other: two side by side, as an open
// book, when both fit the desk's width whole, or else one. Reading on turns a row at a time.
function wholePages() {
  return zoom === "page" && (score?.view ?? "page") === "page";
}

function pagesAcross(factor) {
  if (!wholePages() || (score?.pages?.length ?? 0) < 2) return 1;
  const widest = Math.max(...score.pageSizes.map(([width]) => width));
  const gap = parseFloat(getComputedStyle($("pages")).columnGap) || 0;
  return 2 * widest * MM_TO_PX * factor + gap <= $("desk").clientWidth - DESK_PADDING_PX ? 2 : 1;
}

function centreDesk() {
  if (!fittedToWidth()) return;
  const desk = $("desk");
  desk.scrollLeft = Math.max(0, (desk.scrollWidth - desk.clientWidth) / 2);
}

// A new zoom keeps the music that was at the top of the window at the top. Left alone, the desk
// would keep its scroll in pixels while the pages changed size under it, and show other music: at
// Fit width with bar 10's system at the top of page 1, Whole page went to pages 3 and 4. What's kept
// is the first system any of which is in sight, or in the one-line view the first bar, and the top
// of its page too when that's in sight, as it is at the start of the score. Pages side by side may
// show the edge of the page before the one being read, so a system whose middle is in sight across
// the window comes first. Zoomed again before anything has scrolled, the same music is kept, even
// where the last zoom couldn't put it quite where it was, as near the end of the score: otherwise
// zooming in and back out could wander to other music.
let keptMusic = null;

function musicAtTop() {
  const desk = $("desk");
  if (keptMusic?.element.isConnected && keptMusic.scrolled === `${desk.scrollLeft} ${desk.scrollTop}`) return { ...keptMusic, drawn: drawnSize() };
  const room = desk.getBoundingClientRect();
  const inSight = box => box.bottom > room.top + 1 && box.top < room.bottom - 1 && box.right > room.left + 1 && box.left < room.right - 1;
  const across = box => (box.left + box.right) / 2 > room.left && (box.left + box.right) / 2 < room.right;
  const kind = (score?.view ?? "page") === "horizontal" ? "g.measure" : "g.system";
  const shown = [...document.querySelectorAll(`#pages ${kind}`)].filter(found => inSight(found.getBoundingClientRect()));
  const element = shown.find(found => across(found.getBoundingClientRect())) ?? shown[0];
  if (!element) return null;
  const box = element.getBoundingClientRect(), page = element.closest(".page");
  const pageTop = page.getBoundingClientRect().top - room.top;
  return { element, page, top: box.top - room.top, left: box.left - room.left, pageTop: pageTop >= 0 ? pageTop : null, zoom: drawnZoom(), drawn: drawnSize() };
}

function drawnZoom() {
  return Number(document.documentElement.style.getPropertyValue("--zoom")) || 1;
}

// How the pages are drawn: their size, and how they're laid out on the desk.
function drawnSize() {
  const style = document.documentElement.style, classes = document.body.classList;
  return [style.getPropertyValue("--zoom"), style.getPropertyValue("--across"), classes.contains("fitWidth"), classes.contains("wholePages")].join(" ");
}

// The music kept goes back where it was in the window. Whole page turns to the row with its page,
// as a page turn there would, and pages side by side bring its page to the left, unless the page is
// wider than the window: then the music keeps its place across, as it does in the one-line view.
// Of a system or bar the window's edge cut through, as much is cut off as before, at the new size.
function keepInSight(kept) {
  if (!kept?.element.isConnected || drawnSize() === kept.drawn) return;
  const desk = $("desk"), room = desk.getBoundingClientRect(), box = kept.element.getBoundingClientRect();
  const frame = getComputedStyle($("pagesFrame"));
  const at = offset => (offset < 0 ? (offset * drawnZoom()) / kept.zoom : offset);
  if (wholePages()) desk.scrollTop = kept.page.offsetTop - parseFloat(frame.paddingTop);
  else if (kept.pageTop !== null) desk.scrollTop += kept.page.getBoundingClientRect().top - room.top - kept.pageTop;
  else desk.scrollTop += box.top - room.top - at(kept.top);
  if (!fittedToWidth() && !wholePages()) { // Those are centred across the desk.
    const turned = (score?.view ?? "page") === "page" && kept.page.offsetWidth <= desk.clientWidth;
    desk.scrollLeft = turned ? kept.page.offsetLeft - parseFloat(frame.paddingLeft) : desk.scrollLeft + box.left - room.left - at(kept.left);
  }
  keptMusic = { ...kept, scrolled: `${desk.scrollLeft} ${desk.scrollTop}` };
}

// Each score keeps a zoom for each mode, as it was last left.
function zoomSetting() {
  return `zoom:${scoreId ?? ""}:${currentMode()}`;
}

// On a phone, fitting a page's width would make the notes too small to tap while writing, so Write
// and Practice open at the printed size there, and Read fitted to the width.
function usualZoom() {
  return innerWidth < 760 && !reading() ? "100" : "width";
}

// The zoom this score was last left at in this mode, shown again.
function restoreZoom(kept = musicAtTop()) {
  zoom = readSetting(zoomSetting(), usualZoom());
  setZoom(zoom, kept);
}

function fittedZoom() {
  return zoom === "width" || zoom === "page";
}

function setZoom(value, kept = musicAtTop()) {
  zoom = String(value);
  if (scoreId) keepSetting(zoomSetting(), zoom);
  // The pages are laid out first, as that decides whether the desk has a scroll bar to fit beside.
  document.body.classList.toggle("fitWidth", fittedToWidth());
  document.body.classList.toggle("wholePages", wholePages());
  const factor = fittedZoom() ? fitZoom(zoom) : Number(zoom) / 100;
  document.documentElement.style.setProperty("--zoom", String(factor));
  document.documentElement.style.setProperty("--across", String(pagesAcross(factor)));
  const choice = $("zoomChoice");
  if (![...choice.options].some(option => option.value === zoom)) choice.append(new Option(`${zoom}%`, zoom));
  choice.value = zoom;
  centreDesk();
  keepInSight(kept);
  drawCursor();
  redrawGhost();
  drawPageCount(); // A new size shows other pages, or more of them.
  if (score) drawStickyStart();
}

function stepZoom(by) {
  const now = Number(getComputedStyle(document.documentElement).getPropertyValue("--zoom")) * 100;
  const next = by > 0 ? ZOOMS.find(level => level > now + 0.5) : ZOOMS.findLast(level => level < now - 0.5);
  if (next) setZoom(next);
}

function setUpZoom() {
  $("zoomChoice").replaceChildren(...ZOOMS.map(level => new Option(`${level}%`, level)), new Option("Fit width", "width"), new Option("Whole page", "page"));
  $("zoomChoice").addEventListener("change", event => { setZoom(event.target.value); event.target.blur(); });
  $("zoomIn").addEventListener("click", () => stepZoom(1));
  $("zoomOut").addEventListener("click", () => stepZoom(-1));
  window.addEventListener("resize", () => { if (fittedZoom()) setZoom(zoom); });
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
  state.add = add;
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
  showMoreInstruments(false);
  const time = $("blankTime").selectedOptions[0]?.textContent ?? "4/4";
  $("quickHint").textContent = `One click starts writing for it, in ${KEY_NAMES[Number($("blankKey").value) + 7]} major and ${time.toLowerCase()}. Its + puts several instruments together.`;
  $("newDialog").showModal();
}

// The instruments people play most are cards a click away. Every other instrument, several at
// once, and the key, time and the rest are behind More instruments.
function showMoreInstruments(on) {
  $("newChooser").hidden = !on;
  $("newMore").setAttribute("aria-expanded", String(on));
  $("newMore").hidden = on;
  $("blankNext").hidden = $("blankDone").hidden = !on;
}

// The instruments listed, or one chosen at the top of the dialog, which starts writing at once.
async function createScore(picked = newChooser.picked) {
  const [beats, beat_type, symbol = ""] = $("blankTime").value.split("/");
  const [first, ...more] = picked;
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

// The instruments xrnsheet is made for, a click away at the top of the New score dialog: a click starts
// a score for one, and its + adds it to the list to choose more.
const QUICK_INSTRUMENTS = [
  ["piano", "Piano", "piano", "Two staves, for both hands"],
  ["guitar-tab", "Guitar", "guitar", "Staff and tablature, six strings"],
  ["violin", "Violin", "violin", "Bowings, strings, positions"],
  ["bass-guitar-tab", "Bass guitar", "guitar", "Staff and tablature, four strings"],
  ["cello", "Cello", "violin", "Bass clef, four strings"],
  ["viola", "Viola", "violin", "Alto clef, four strings"],
  ["ukulele", "Ukulele", "guitar", "Four strings, G C E A"],
  ["voice", "Voice", "voice", "A melody to sing, with lyrics"],
];

function setUpNewScore() {
  $("blankOpen").addEventListener("click", openNewScore);
  $("quickStart").replaceChildren(...QUICK_INSTRUMENTS.map(([id, label, drawn, what]) => {
    const holder = document.createElement("div");
    holder.className = "quick";
    holder.dataset.quick = id;
    holder.innerHTML = `<button type="button" class="start">${icon(drawn)}<span class="name"></span><span class="what"></span></button><button type="button" class="add flat">${icon("plus")}</button>`;
    const [start, add] = holder.querySelectorAll("button");
    start.querySelector(".name").textContent = label;
    start.querySelector(".what").textContent = what;
    start.title = `Start writing for ${label.toLowerCase()} at once`;
    add.title = `Add ${label.toLowerCase()} to the list, to choose more instruments`;
    add.setAttribute("aria-label", add.title);
    start.addEventListener("click", () => createScore([id]));
    add.addEventListener("click", () => { showMoreInstruments(true); newChooser?.add(id); });
    return holder;
  }));
  $("newMore").addEventListener("click", () => showMoreInstruments(true));
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

// MuseScore's page sizes, in millimetres across and down, as the server knows them too.
const PAGE_SIZES = { A4: [210, 297], Letter: [215.9, 279.4], Legal: [215.9, 355.6], Tabloid: [279.4, 431.8], A3: [297, 420], A5: [148, 210], B4: [250, 353], B5: [176, 250], Executive: [184.2, 266.7], Concert: [228.6, 304.8] };
const USUAL_LAYOUT = { width: 210, height: 297, top: 15, bottom: 15, left: 15, right: 15, space: 1.75, staves: 6, systems: 6 };

function pageSizeName(width, height) {
  const near = (first, second) => Math.abs(first - second) < 0.5;
  return Object.entries(PAGE_SIZES).find(([, [across, down]]) => (near(width, across) && near(height, down)) || (near(width, down) && near(height, across)))?.[0] ?? "custom";
}

function showPageSize() {
  const width = Number($("pageWidth").value), height = Number($("pageHeight").value);
  $("pageSize").value = pageSizeName(width, height);
  document.querySelector(`input[name="orientation"][value="${width > height ? "landscape" : "portrait"}"]`).checked = true;
}

function fillPageSettings(layout) {
  $("pageWidth").value = layout.width;
  $("pageHeight").value = layout.height;
  $("marginTop").value = layout.top;
  $("marginBottom").value = layout.bottom;
  $("marginLeft").value = layout.left;
  $("marginRight").value = layout.right;
  $("staffSpace").value = layout.space;
  showPageSize();
}

function openPageSettings() {
  fillPageSettings({ ...USUAL_LAYOUT, ...score.layout });
  $("pageDialog").showModal();
}

function openStyle() {
  const layout = { ...USUAL_LAYOUT, ...score.layout };
  $("staffDistance").value = layout.staves;
  $("systemDistance").value = layout.systems;
  $("styleDialog").showModal();
}

// Read now, as a reply arriving first redraws the score and its layout.
function sendLayout(changes) {
  const body = { ...USUAL_LAYOUT, ...score.layout, ...changes };
  enqueue(() => send("layout", body));
}

function setUpPageSettings() {
  $("pageSize").append(...Object.keys(PAGE_SIZES).map(name => new Option(name, name)), new Option("Custom", "custom"));
  $("pageSize").addEventListener("change", event => {
    const size = PAGE_SIZES[event.target.value];
    if (!size) return;
    const landscape = document.querySelector('input[name="orientation"]:checked')?.value === "landscape";
    [$("pageWidth").value, $("pageHeight").value] = landscape ? [size[1], size[0]] : size;
  });
  document.querySelectorAll('input[name="orientation"]').forEach(choice => choice.addEventListener("change", () => {
    const width = Number($("pageWidth").value), height = Number($("pageHeight").value);
    if ((choice.value === "landscape") !== (width > height)) [$("pageWidth").value, $("pageHeight").value] = [height, width];
  }));
  for (const id of ["pageWidth", "pageHeight"]) $(id).addEventListener("input", showPageSize);
  $("pageReset").addEventListener("click", () => fillPageSettings(USUAL_LAYOUT));
  $("pageDialog").querySelector("form").addEventListener("submit", () => sendLayout({
    width: Number($("pageWidth").value), height: Number($("pageHeight").value),
    top: Number($("marginTop").value), bottom: Number($("marginBottom").value), left: Number($("marginLeft").value), right: Number($("marginRight").value),
    space: Number($("staffSpace").value),
  }));
  $("styleReset").addEventListener("click", () => { $("staffDistance").value = USUAL_LAYOUT.staves; $("systemDistance").value = USUAL_LAYOUT.systems; });
  $("styleDialog").querySelector("form").addEventListener("submit", () => sendLayout({ staves: Number($("staffDistance").value), systems: Number($("systemDistance").value) }));
}

function openBreaks() {
  const count = score.barsPerLine ?? 0;
  document.querySelector(`input[name="breaksMode"][value="${count ? "every" : "fit"}"]`).checked = true;
  if (count) $("breaksCount").value = count;
  $("breaksDialog").showModal();
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
  $("originalClose").addEventListener("click", () => showOriginal(false));
  $("originalShow").addEventListener("click", () => showOriginal($("original").hidden));
  $("partsButton").addEventListener("click", openParts);
  $("scoreName").addEventListener("click", openProject);
  $("pageOpen").addEventListener("click", openPageSettings);
  $("styleOpen").addEventListener("click", openStyle);
  $("transposeOpen").addEventListener("click", openTranspose);
  $("concertPitch").addEventListener("click", toggleConcertPitch);
  $("viewMode").addEventListener("change", event => { setView(event.target.value); event.target.blur(); });
  $("partAdd").addEventListener("click", openAddPart);
  $("partRemove").addEventListener("click", () => enqueue(async () => {
    if (chosenPart === null) return;
    const index = chosenPart;
    chosenPart = null;
    // Practice's choices name parts by their place, which moves up for those below, so they're
    // moved before the score comes back without the part.
    const moved = (choice, none) => {
      const [kind, part, ...rest] = choice.split(":");
      if (kind !== "part" && kind !== "staff") return choice;
      if (Number(part) === index) return none;
      return Number(part) > index ? [kind, Number(part) - 1, ...rest].join(":") : choice;
    };
    const before = { hear: practice.hear, waitFor: practice.waitFor };
    Object.assign(practice, { hear: moved(practice.hear, "all"), waitFor: moved(practice.waitFor, "off") });
    try {
      await send("parts", { action: "remove", index });
    } catch (error) {
      Object.assign(practice, before);
      throw error;
    }
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
  $("pickupChoice").addEventListener("change", event => {
    if (event.target.value !== "") barAction("pickup", { count: Number(event.target.value) });
    event.target.value = "";
    event.target.blur();
  });
  setUpPageSettings();
}

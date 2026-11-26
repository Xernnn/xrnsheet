'use strict'
// What the desktop app needs to run xrnsheet's server, kept apart from Electron so that
// `node --test` can check it.

const fs = require('node:fs')
const net = require('node:net')
const path = require('node:path')
const { fileURLToPath } = require('node:url')

// The same as the server's ALLOWED_TYPES: what it takes as a new score.
const SCORE_SUFFIXES = new Set(['.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff', '.pdf', '.musicxml', '.xml', '.mxl'])

// The page keeps its panels and palettes in the browser's storage, which belongs to the address it
// was loaded from, port and all. So the app asks for the same port each time, and only moves to
// the next if something else has it.
const PORTS = [47360, 47361, 47362, 47363, 47364, 47365, 47366, 47367, 47368, 47369]

// Variables that would point the server's Python at something other than its own environment.
const PYTHON_VARIABLES = ['PYTHONHOME', 'PYTHONPATH', 'PYTHONSTARTUP', 'PYTHONEXECUTABLE', 'VIRTUAL_ENV']
// Lists of folders an AppImage puts its own folders in front of, for the app it carries. The server
// and what it starts need the system's, or its Python would load the AppImage's libraries.
const FOLDER_LISTS = ['PATH', 'LD_LIBRARY_PATH', 'XDG_DATA_DIRS', 'GSETTINGS_SCHEMA_DIR', 'PERLLIB', 'QT_PLUGIN_PATH']

/** Where the Python is that has xrnsheet's packages, or why there isn't one. */
function findPython({ env, packaged, resourcesPath, repo }) {
  let python = env.XRNSHEET_PYTHON
  let from = 'XRNSHEET_PYTHON'
  if (!python && packaged) {
    const config = path.join(resourcesPath, 'desktop.json')
    try {
      python = JSON.parse(fs.readFileSync(config, 'utf8')).python
      from = config
    } catch {
      return { problem: `This copy of xrnsheet doesn't say where its Python is (${config} is missing or unreadable).` }
    }
  }
  if (!python) {
    python = path.join(repo, '.venv', 'bin', 'python')
    from = 'this checkout'
  }
  try {
    fs.accessSync(python, fs.constants.X_OK)
  } catch {
    return { python, problem: `xrnsheet runs on the Python environment at ${python} (from ${from}), and it isn't there. Set it up as the README's Setup says, then install the app again.` }
  }
  return { python }
}

function dataHome(env, home) {
  return env.XDG_DATA_HOME || path.join(home, '.local', 'share')
}

/** The folder the scores are kept in: the user's own, not inside the program. */
function jobsDir(env, home) {
  return env.XRNSHEET_JOBS || path.join(dataHome(env, home), 'xrnsheet', 'jobs')
}

function logFile(env, home) {
  return path.join(env.XDG_STATE_HOME || path.join(home, '.local', 'state'), 'xrnsheet', 'server.log')
}

/**
 * The folder chords.corrections gathers checked sheets into for retraining, which un-marking or deleting a
 * sheet takes it out of: the checkout's data/corrections. Packaged, the server runs from the app's own copy
 * of the code, where data/corrections is never gathered into, so the app says where the checkout's is.
 */
function correctionsDir({ packaged, resourcesPath, repo }) {
  if (!packaged) return path.join(repo, 'data', 'corrections')
  try {
    return JSON.parse(fs.readFileSync(path.join(resourcesPath, 'desktop.json'), 'utf8')).corrections || null
  } catch {
    return null
  }
}

function serverArguments(port, jobs, corrections) {
  const args = ['-m', 'app.server', '--port', String(port), '--jobs', jobs]
  if (corrections) args.push('--corrections', corrections)
  return [...args, '--stop-when-stdin-closes']
}

/** The environment the server runs in: this one, less what the AppImage set for itself. */
function childEnv(env) {
  const result = { ...env, PYTHONUNBUFFERED: '1', PYTHONDONTWRITEBYTECODE: '1' }
  for (const name of PYTHON_VARIABLES) delete result[name]
  delete result.ELECTRON_RUN_AS_NODE
  const appDir = env.APPDIR
  if (appDir) {
    const inside = folder => folder === appDir || folder.startsWith(appDir.endsWith('/') ? appDir : `${appDir}/`)
    for (const name of FOLDER_LISTS) {
      if (result[name] === undefined) continue
      const kept = result[name].split(':').filter(folder => folder && !inside(folder))
      if (kept.length) result[name] = kept.join(':')
      else delete result[name]
    }
  }
  return result
}

function portIsFree(port) {
  return new Promise(resolve => {
    const probe = net.createServer()
    probe.once('error', () => resolve(false))
    probe.listen(port, '127.0.0.1', () => probe.close(() => resolve(true)))
  })
}

/** A port nothing has now, chosen by the system. */
function anyFreePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer()
    probe.once('error', reject)
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address()
      probe.close(() => resolve(port))
    })
  })
}

/** The first of the app's usual ports that nothing has, or any free one if they're all taken. */
async function choosePort(ports = PORTS) {
  for (const port of ports) {
    if (port > 0 && (await portIsFree(port))) return port
  }
  return anyFreePort()
}

/** Wait until the server answers, for as long as it's still running and the time allows. */
async function waitForServer(url, { timeoutMs, alive, intervalMs = 200 }) {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    if (!alive()) throw new Error('The server stopped while starting.')
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(2000) })
      if (response.ok) return
    } catch {
      // Not listening yet.
    }
    if (Date.now() > deadline) throw new Error(`The server didn't answer within ${Math.round(timeoutMs / 1000)} seconds.`)
    await new Promise(resolve => setTimeout(resolve, intervalMs))
  }
}

function sameOrigin(url, base) {
  try {
    return new URL(url).origin === new URL(base).origin
  } catch {
    return false
  }
}

/** The score the app's page shows at this address, by the id after its #, or null for the library. */
function scoreInAddress(url) {
  try {
    const id = new URL(url).hash.slice(1)
    return /^[0-9a-f]{32}$/.test(id) ? id : null
  } catch {
    return null
  }
}

/**
 * Whether a window may go to this address: the app's own pages, and of files only the loading page.
 * Any file: address was let through, so a file dropped on the loading page, or a link to one, was
 * shown in the window.
 */
function staysInApp(url, base, loadingPage) {
  if (base && sameOrigin(url, base)) return true
  try {
    const target = new URL(url)
    return target.protocol === 'file:' && fileURLToPath(target) === loadingPage
  } catch {
    return false
  }
}

function opensOutside(url) {
  try {
    return ['http:', 'https:', 'mailto:'].includes(new URL(url).protocol)
  } catch {
    return false
  }
}

/**
 * What the page may use. A MIDI keyboard for entering notes, but not System Exclusive, which can
 * rewrite a device's settings; writing what it copies; and full screen. Nothing else: no camera,
 * microphone, location or notifications.
 */
function allowedPermission(permission, details = {}) {
  if (permission === 'midi') return !details.sysex
  return permission === 'clipboard-sanitized-write' || permission === 'fullscreen'
}

/** The files named on a command line that xrnsheet can open, as absolute paths. */
function scoreFiles(args, cwd) {
  const found = []
  for (const arg of args) {
    if (!arg || arg.startsWith('-')) continue
    let file = arg
    if (arg.startsWith('file://')) {
      try {
        file = fileURLToPath(arg)
      } catch {
        continue
      }
    }
    file = path.resolve(cwd, file)
    if (!SCORE_SUFFIXES.has(path.extname(file).toLowerCase())) continue
    try {
      if (!fs.statSync(file).isFile()) continue
    } catch {
      continue
    }
    if (!found.includes(file)) found.push(file)
  }
  return found
}

/** Send a file to the server as a new score, the way the page's "Open other…" does, and return its id. */
async function uploadScore(base, file) {
  const form = new FormData()
  form.append('file', new Blob([await fs.promises.readFile(file)]), path.basename(file))
  const response = await fetch(`${base}/api/scores`, { method: 'POST', body: form })
  let answer = {}
  try {
    answer = await response.json()
  } catch {
    // The reason is in the status alone.
  }
  if (!response.ok) throw new Error(typeof answer.detail === 'string' ? answer.detail : `The app answered ${response.status}.`)
  return answer.id
}

/** The end of the server's log, for saying why it stopped. */
function logTail(file, lines = 25) {
  try {
    return fs.readFileSync(file, 'utf8').trimEnd().split('\n').slice(-lines).join('\n')
  } catch {
    return ''
  }
}

module.exports = {
  PORTS,
  SCORE_SUFFIXES,
  allowedPermission,
  anyFreePort,
  childEnv,
  choosePort,
  correctionsDir,
  findPython,
  jobsDir,
  logFile,
  logTail,
  opensOutside,
  portIsFree,
  sameOrigin,
  scoreFiles,
  scoreInAddress,
  serverArguments,
  staysInApp,
  uploadScore,
  waitForServer
}

'use strict'
// xrnsheet as a desktop app: starts the app's own server on this computer, shows its page in a
// window of its own, and stops the server when the window closes.

const { app, BrowserWindow, Menu, dialog, nativeTheme, session, shell } = require('electron')
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const desktop = require('./server')

const HOME = os.homedir()
const LOADING_PAGE = path.join(__dirname, 'loading.html')
const START_TIMEOUT_MS = 120_000
const STOP_TIMEOUT_MS = 15_000
const RESTARTS_PER_MINUTE = 3
// The page's own colours (app.css --bg), so the window doesn't flash white before it loads.
const BACKGROUND = { light: '#f5f5f6', dark: '#2d2d30' }

let win = null
let server = null // { child, base, log, ready, exited }
let quitting = false
let waitingFiles = []
const restarts = []

// The page has its own menu bar and keys. Electron's default menu would take Ctrl+= and Ctrl+-
// (the score's zoom), Ctrl+R and Alt.
Menu.setApplicationMenu(null)

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', (_event, argv, cwd) => {
    if (win) {
      if (win.isMinimized()) win.restore()
      win.focus()
    }
    openFiles(desktop.scoreFiles(argv.slice(1), cwd))
  })
  app.whenReady().then(ready)
}

app.on('window-all-closed', () => app.quit())
app.on('before-quit', () => {
  quitting = true
})
app.on('will-quit', event => {
  if (!server) return
  event.preventDefault()
  stopServer(server).then(() => app.quit())
})

function ready() {
  const ses = session.defaultSession
  ses.setPermissionRequestHandler((_contents, permission, callback, details) => callback(desktop.allowedPermission(permission, details)))
  ses.setPermissionCheckHandler((_contents, permission, _origin, details) => desktop.allowedPermission(permission, details))
  ses.on('will-download', (_event, item) => {
    item.setSaveDialogOptions({ defaultPath: path.join(app.getPath('downloads'), item.getFilename()) })
  })
  waitingFiles = desktop.scoreFiles(process.argv.slice(1), process.cwd())
  createWindow()
  start()
}

function createWindow() {
  win = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 720,
    minHeight: 480,
    title: 'xrnsheet',
    show: false,
    backgroundColor: nativeTheme.shouldUseDarkColors ? BACKGROUND.dark : BACKGROUND.light,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true, spellcheck: false }
  })
  win.once('ready-to-show', () => win?.show())
  win.on('closed', () => {
    win = null
  })
  guard(win.webContents)
  win.webContents.on('did-create-window', child => guard(child.webContents))
  win.webContents.on('before-input-event', (event, input) => {
    if (input.type !== 'keyDown' || !input.control || input.alt || input.meta) return
    const key = input.key.toLowerCase()
    if (key === 'q' && !input.shift) {
      event.preventDefault()
      app.quit()
    } else if (key === 'i' && input.shift) {
      event.preventDefault()
      win.webContents.toggleDevTools()
    }
  })
  win.loadFile(LOADING_PAGE).catch(() => {})
}

/**
 * Keep a window on the app's own pages. Its own new windows (a printable PDF, one part) open as
 * windows here; a link anywhere else opens in the usual browser.
 */
function guard(contents) {
  contents.setWindowOpenHandler(({ url }) => {
    if (server && desktop.sameOrigin(url, server.base)) {
      return { action: 'allow', overrideBrowserWindowOptions: { width: 960, height: 1100, backgroundColor: '#ffffff' } }
    }
    if (desktop.opensOutside(url)) shell.openExternal(url)
    return { action: 'deny' }
  })
  contents.on('will-navigate', (event, url) => {
    if (desktop.staysInApp(url, server?.base, LOADING_PAGE)) return
    event.preventDefault()
    if (desktop.opensOutside(url)) shell.openExternal(url)
  })
}

function showLoading(query) {
  if (win && !win.isDestroyed()) win.loadFile(LOADING_PAGE, query ? { query } : undefined).catch(() => {})
}

function showProblem(problem, detail = '') {
  showLoading({ problem, detail })
}

async function start() {
  const found = desktop.findPython({ env: process.env, packaged: app.isPackaged, resourcesPath: process.resourcesPath, repo: path.join(__dirname, '..') })
  if (found.problem) return showProblem(found.problem)
  // Packaged, the server is the copy inside the app, so changing the checkout can't change it.
  const source = app.isPackaged ? path.join(process.resourcesPath, 'xrnsheet') : path.join(__dirname, '..')
  const jobs = desktop.jobsDir(process.env, HOME)
  const log = desktop.logFile(process.env, HOME)
  let port
  let output
  try {
    fs.mkdirSync(jobs, { recursive: true })
    fs.mkdirSync(path.dirname(log), { recursive: true })
    if (fs.existsSync(log)) fs.renameSync(log, `${log}.1`)
    output = fs.openSync(log, 'a')
    port = await desktop.choosePort()
  } catch (error) {
    return showProblem("xrnsheet couldn't get ready to start.", error.message)
  }
  // stdin stays an open pipe, never written to: when this app ends, however it ends, the server
  // sees it close and stops (--stop-when-stdin-closes).
  const child = spawn(found.python, desktop.serverArguments(port, jobs), {
    cwd: source,
    env: desktop.childEnv(process.env),
    stdio: ['pipe', output, output]
  })
  fs.closeSync(output)
  child.stdin.on('error', () => {})
  const current = { child, base: `http://127.0.0.1:${port}`, log, ready: false }
  current.exited = new Promise(resolve => {
    child.once('exit', (code, signal) => resolve({ code, signal }))
    child.once('error', error => resolve({ error }))
  })
  server = current
  current.exited.then(result => serverStopped(current, result))

  try {
    await desktop.waitForServer(`${current.base}/api/scores`, {
      timeoutMs: START_TIMEOUT_MS,
      alive: () => server === current && child.exitCode === null && child.signalCode === null
    })
  } catch (error) {
    if (server === current) {
      showProblem("xrnsheet's server didn't start.", `${error.message}\n\n${desktop.logTail(log)}\n\nThe whole log is in ${log}.`)
      stopServer(current)
    }
    return
  }
  if (server !== current) return
  current.ready = true
  const files = waitingFiles
  waitingFiles = []
  const opened = await upload(files)
  if (win && !win.isDestroyed() && server === current) {
    await win.loadURL(opened ? scoreUrl(current.base, opened) : `${current.base}/`).catch(() => {})
  }
}

function serverStopped(current, result) {
  if (server !== current) return
  server = null
  if (quitting) return
  if (!current.ready) {
    // start() reports a server that never answered.
    if (result.error) showProblem("xrnsheet's server couldn't be started.", result.error.message)
    return
  }
  const now = Date.now()
  while (restarts.length && now - restarts[0] > 60_000) restarts.shift()
  if (restarts.length >= RESTARTS_PER_MINUTE) {
    return showProblem("xrnsheet's server keeps stopping.", `${desktop.logTail(current.log)}\n\nThe whole log is in ${current.log}.`)
  }
  restarts.push(now)
  // Every edit is saved as it's made, so starting again loses nothing.
  showLoading({ state: 'restarting' })
  start()
}

/** Ask the server to stop, as Ctrl+C would, and make sure it has after a while. */
async function stopServer(current) {
  const { child } = current
  if (child.exitCode === null && child.signalCode === null) {
    child.kill('SIGTERM')
    const timer = setTimeout(() => child.kill('SIGKILL'), STOP_TIMEOUT_MS)
    await current.exited
    clearTimeout(timer)
  }
  if (server === current) server = null
}

function scoreUrl(base, id) {
  // A different query makes this a new page, which the page needs to open the score in its address.
  return `${base}/?opened=${Date.now()}#${encodeURIComponent(id)}`
}

async function upload(files) {
  let opened = null
  for (const file of files) {
    try {
      opened = await desktop.uploadScore(server.base, file)
    } catch (error) {
      const options = { type: 'warning', message: `${path.basename(file)} couldn't be opened.`, detail: error.message }
      if (win && !win.isDestroyed()) dialog.showMessageBox(win, options)
      else dialog.showMessageBox(options)
    }
  }
  return opened
}

async function openFiles(files) {
  if (!files.length) return
  if (!server?.ready) {
    waitingFiles.push(...files.filter(file => !waitingFiles.includes(file)))
    return
  }
  const current = server
  const opened = await upload(files)
  if (opened && win && !win.isDestroyed() && server === current) await win.loadURL(scoreUrl(current.base, opened)).catch(() => {})
}

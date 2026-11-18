'use strict'
// Starts the desktop app for real, headless, and checks it through Chromium's debugging port:
// the window shows the app, a file named on a second start opens there, its own windows stay
// its own, and quitting, or being killed, stops the server. It never opens anything in the
// desktop's own browser.
//
//   npm run test:window                       the app from this folder (needs ../.venv)
//   npm run test:window -- <app.AppImage>     a built AppImage
//
// Everything it makes goes in a throwaway folder; it never sees your own scores.

const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { spawn } = require('node:child_process')
const desktop = require('../server')

const REPO = path.join(__dirname, '..', '..')
const appImage = process.argv[2]
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'xrnsheet-window-'))
const env = {
  ...process.env,
  XDG_CONFIG_HOME: path.join(scratch, 'config'),
  XDG_DATA_HOME: path.join(scratch, 'data'),
  XDG_STATE_HOME: path.join(scratch, 'state'),
  NO_CLEANUP: '1'
}
const jobs = path.join(scratch, 'data', 'xrnsheet', 'jobs')
const log = path.join(scratch, 'state', 'xrnsheet', 'server.log')
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const launched = []

function launch(debugPort, extra = []) {
  // Headless, so no window appears on this desktop. The app shows no native dialog on these paths.
  const flags = ['--ozone-platform=headless', '--ozone-override-screen-size=1440,920', `--remote-debugging-port=${debugPort}`, ...extra]
  const [command, args] = appImage
    ? [path.resolve(appImage), ['--appimage-extract-and-run', ...flags]]
    : [require(path.join(__dirname, '..', 'node_modules', 'electron')), [path.join(__dirname, '..'), ...flags]]
  const child = spawn(command, args, { env, stdio: ['ignore', 'pipe', 'pipe'] })
  launched.push(child)
  let output = ''
  child.stdout.on('data', chunk => { output += chunk })
  child.stderr.on('data', chunk => { output += chunk })
  child.output = () => output
  child.exited = new Promise(resolve => child.once('exit', (code, signal) => resolve({ code, signal })))
  return child
}

async function until(what, check, timeoutMs = 60_000) {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    try {
      const value = await check()
      if (value) return value
    } catch {
      // Not yet.
    }
    if (Date.now() > deadline) throw new Error(`Timed out waiting for ${what}`)
    await sleep(250)
  }
}

async function targets(debugPort) {
  return (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json()
}

/** Run an expression in a page, through the debugging port. */
async function evaluate(target, expression) {
  const socket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    socket.onopen = resolve
    socket.onerror = reject
  })
  try {
    const reply = new Promise((resolve, reject) => {
      socket.onmessage = event => {
        const message = JSON.parse(event.data)
        if (message.id !== 1) return
        if (message.error || message.result.exceptionDetails) reject(new Error(JSON.stringify(message.error || message.result.exceptionDetails)))
        else resolve(message.result.result.value)
      }
    })
    socket.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: { expression, awaitPromise: true, returnByValue: true } }))
    return await reply
  } finally {
    socket.close()
  }
}

async function appPage(debugPort) {
  return until('the app page', async () => (await targets(debugPort)).find(target => target.type === 'page' && /^http:\/\/127\.0\.0\.1:\d+\//.test(target.url)))
}

async function serverPid(base) {
  // The server is the process listening on the page's port.
  const port = new URL(base).port
  const { execFileSync } = require('node:child_process')
  const listing = execFileSync('ss', ['-Hltnp', `sport = :${port}`], { encoding: 'utf8' })
  const found = /pid=(\d+)/.exec(listing)
  return found ? Number(found[1]) : null
}

function running(pid) {
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

async function main() {
  const checks = []
  const ok = name => {
    checks.push(name)
    console.log(`ok - ${name}`)
  }

  const debugPort = await desktop.anyFreePort()
  const first = launch(debugPort)
  let page = await appPage(debugPort)
  const base = new URL(page.url).origin
  ok(`the window shows the app at ${base}`)
  assert.ok(desktop.PORTS.map(port => `http://127.0.0.1:${port}`).includes(base), 'one of the usual ports, so the page keeps its settings')
  await until('the start page', () => evaluate(page, 'document.readyState === "complete" && !!document.getElementById("historyList")'))
  assert.equal(await evaluate(page, 'fetch("/api/scores").then(r => r.json()).then(list => list.length)'), 0)
  ok('the start page loads, with no scores yet')

  // A second start with a file hands it to the first, which opens it.
  const tune = path.join(REPO, 'data', 'truth', 'chart_00.musicxml')
  const second = launch(await desktop.anyFreePort(), [tune])
  const secondExit = await Promise.race([second.exited, sleep(30_000).then(() => 'still running')])
  assert.notEqual(secondExit, 'still running', 'the second start hands over and ends')
  const opened = await until('the file to open', async () => {
    page = await appPage(debugPort)
    const hash = await evaluate(page, 'location.hash.slice(1)')
    return /^[0-9a-f]{32}$/.test(hash) ? hash : null
  })
  assert.ok(fs.existsSync(path.join(jobs, opened)), 'the score is kept in the user\'s folder')
  await until('the score to show', () => evaluate(page, 'document.querySelectorAll("svg").length > 0'))
  ok('a file named on a second start opens in the first window, and is kept in the scores folder')

  // The server stopping, as when a crash takes it down mid-edit, starts it again, and the window
  // comes back to the score that was open rather than to the library.
  const crashed = await serverPid(base)
  process.kill(crashed, 'SIGKILL')
  await until('the window to say it starts again', async () => (await targets(debugPort)).some(target => target.url.startsWith('file:') && target.url.includes('restarting')))
  page = await until('the score to open again', async () => {
    const found = await appPage(debugPort)
    return found.url.endsWith(`#${opened}`) && (await evaluate(found, 'document.querySelectorAll("#pages svg").length > 0')) ? found : null
  }, 90_000)
  ok('a server that stops is started again, with the score that was open')

  // The app's own new windows open as windows; other sites don't.
  const before = (await targets(debugPort)).filter(target => target.type === 'page').length
  // Not an http address: one would really be handed to the desktop's browser. Other addresses are
  // refused the same way (see allowedPermission and opensOutside in server.test.js).
  await evaluate(page, `window.open("/api/scores/${opened}/score.pdf", "_blank"); window.open("smb://example.invalid/share", "_blank"); true`)
  await until('the PDF window', async () => (await targets(debugPort)).some(target => target.url.includes(`/api/scores/${opened}/score.pdf`)))
  await sleep(1000)
  const pages = (await targets(debugPort)).filter(target => target.type === 'page')
  assert.equal(pages.filter(target => target.url.startsWith('smb:')).length, 0, 'another address never opens inside the app')
  assert.ok(pages.length >= before + 1)
  ok('its own PDF opens in a window of its own, and another address doesn\'t open in the app')

  // Quitting stops the server.
  const pid = await serverPid(base)
  assert.ok(pid && running(pid), 'the server is running')
  await evaluate(page, 'window.close(); true').catch(() => {})
  // window.close() closes only the window the page opened itself in some cases, so close them all.
  for (const target of (await targets(debugPort).catch(() => []))) {
    await fetch(`http://127.0.0.1:${debugPort}/json/close/${target.id}`).catch(() => {})
  }
  const firstExit = await Promise.race([first.exited, sleep(30_000).then(() => 'still running')])
  assert.notEqual(firstExit, 'still running', 'the app ends when its windows close')
  await until('the server to stop', () => !running(pid), 20_000)
  ok('closing the window ends the app and its server')

  // Killed outright, the app can't stop the server, which stops itself.
  const debugPort2 = await desktop.anyFreePort()
  const third = launch(debugPort2)
  const page3 = await appPage(debugPort2)
  const base3 = new URL(page3.url).origin
  assert.equal(base3, base, 'the same port as last time, so the page keeps its settings')
  const pid3 = await until('the server', () => serverPid(base3))
  // The app's main process is the server's parent (an AppImage starts it from its own process).
  const main3 = Number(require('node:child_process').execFileSync('ps', ['-o', 'ppid=', '-p', String(pid3)], { encoding: 'utf8' }).trim())
  process.kill(main3, 'SIGKILL')
  await until('the app to end', () => !running(main3), 10_000)
  await until('the server to stop on its own', () => !running(pid3), 20_000)
  ok('killed outright, the app leaves no server behind')

  fs.rmSync(scratch, { recursive: true, force: true })
  console.log(`all passed (${checks.length})`)
  process.exit(0) // The app's helper processes can hold its output pipes open a while.
}

main().catch(error => {
  console.error(`FAIL - ${error.stack || error}`)
  try {
    console.error(`server log:\n${fs.readFileSync(log, 'utf8').slice(-3000)}`)
  } catch {
    // No log yet.
  }
  console.error(`left in ${scratch}`)
  for (const child of launched) if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM')
  setTimeout(() => process.exit(1), 5000)
})

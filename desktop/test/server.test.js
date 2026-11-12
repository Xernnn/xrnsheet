'use strict'
const assert = require('node:assert/strict')
const fs = require('node:fs')
const http = require('node:http')
const net = require('node:net')
const os = require('node:os')
const path = require('node:path')
const { spawn } = require('node:child_process')
const { pathToFileURL } = require('node:url')
const { test } = require('node:test')
const desktop = require('../server')

const REPO = path.join(__dirname, '..', '..')

function tempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'xrnsheet-desktop-'))
}

function executable(folder, name) {
  const file = path.join(folder, name)
  fs.writeFileSync(file, '#!/bin/sh\n')
  fs.chmodSync(file, 0o755)
  return file
}

function listen(handler) {
  return new Promise(resolve => {
    const server = http.createServer(handler)
    server.listen(0, '127.0.0.1', () => resolve(server))
  })
}

test('the Python comes from XRNSHEET_PYTHON, then the app, then the checkout', () => {
  const folder = tempDir()
  const own = executable(folder, 'own-python')
  assert.deepEqual(desktop.findPython({ env: { XRNSHEET_PYTHON: own }, packaged: true, resourcesPath: folder, repo: folder }), { python: own })

  const baked = executable(folder, 'baked-python')
  fs.writeFileSync(path.join(folder, 'desktop.json'), JSON.stringify({ python: baked }))
  assert.deepEqual(desktop.findPython({ env: {}, packaged: true, resourcesPath: folder, repo: folder }), { python: baked })

  fs.mkdirSync(path.join(folder, '.venv', 'bin'), { recursive: true })
  const checkout = executable(path.join(folder, '.venv', 'bin'), 'python')
  assert.deepEqual(desktop.findPython({ env: {}, packaged: false, resourcesPath: '', repo: folder }), { python: checkout })
})

test('a missing Python is explained rather than started', () => {
  const folder = tempDir()
  const gone = desktop.findPython({ env: { XRNSHEET_PYTHON: path.join(folder, 'nothing') }, packaged: false, resourcesPath: '', repo: folder })
  assert.match(gone.problem, /isn't there/)
  const unsaid = desktop.findPython({ env: {}, packaged: true, resourcesPath: folder, repo: folder })
  assert.match(unsaid.problem, /doesn't say where its Python is/)
  const notRunnable = path.join(folder, 'plain')
  fs.writeFileSync(notRunnable, '')
  assert.ok(desktop.findPython({ env: { XRNSHEET_PYTHON: notRunnable }, packaged: false, resourcesPath: '', repo: folder }).problem)
})

test('scores and the log live in the user\'s folders, not in the program', () => {
  assert.equal(desktop.jobsDir({}, '/home/a'), '/home/a/.local/share/xrnsheet/jobs')
  assert.equal(desktop.jobsDir({ XDG_DATA_HOME: '/data' }, '/home/a'), '/data/xrnsheet/jobs')
  assert.equal(desktop.jobsDir({ XRNSHEET_JOBS: '/somewhere', XDG_DATA_HOME: '/data' }, '/home/a'), '/somewhere')
  assert.equal(desktop.logFile({}, '/home/a'), '/home/a/.local/state/xrnsheet/server.log')
  assert.equal(desktop.logFile({ XDG_STATE_HOME: '/state' }, '/home/a'), '/state/xrnsheet/server.log')
})

test('the server is told where the scores are and to stop with the app', () => {
  assert.deepEqual(desktop.serverArguments(47360, '/j'), ['-m', 'app.server', '--port', '47360', '--jobs', '/j', '--stop-when-stdin-closes'])
})

test('the server runs on the system\'s folders, not the AppImage\'s', () => {
  const env = desktop.childEnv({
    APPDIR: '/tmp/.mount_xrnsheetAb12',
    PATH: '/tmp/.mount_xrnsheetAb12:/tmp/.mount_xrnsheetAb12/usr/sbin:/usr/local/bin:/usr/bin',
    LD_LIBRARY_PATH: '/tmp/.mount_xrnsheetAb12/usr/lib',
    XDG_DATA_DIRS: '/tmp/.mount_xrnsheetAb12/usr/share/:/usr/local/share:/usr/share',
    PYTHONPATH: '/elsewhere',
    PYTHONHOME: '/elsewhere',
    VIRTUAL_ENV: '/other/venv',
    ELECTRON_RUN_AS_NODE: '1',
    HOME: '/home/a',
    WAYLAND_DISPLAY: 'wayland-1'
  })
  assert.equal(env.PATH, '/usr/local/bin:/usr/bin')
  assert.equal(env.LD_LIBRARY_PATH, undefined)
  assert.equal(env.XDG_DATA_DIRS, '/usr/local/share:/usr/share')
  for (const name of ['PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'ELECTRON_RUN_AS_NODE']) assert.equal(env[name], undefined)
  assert.equal(env.HOME, '/home/a')
  assert.equal(env.WAYLAND_DISPLAY, 'wayland-1')
  assert.equal(env.PYTHONUNBUFFERED, '1')
  // A folder that only starts with the same letters isn't the AppImage's.
  assert.equal(desktop.childEnv({ APPDIR: '/opt/app', PATH: '/opt/application/bin:/usr/bin' }).PATH, '/opt/application/bin:/usr/bin')
  // Outside an AppImage nothing is taken out.
  assert.equal(desktop.childEnv({ PATH: '/a:/b' }).PATH, '/a:/b')
})

test('only files xrnsheet can open are taken from a command line', () => {
  const folder = tempDir()
  for (const name of ['song.musicxml', 'song.MXL', 'scan.pdf', 'photo.jpg', 'notes.txt']) fs.writeFileSync(path.join(folder, name), 'x')
  fs.mkdirSync(path.join(folder, 'folder.pdf'))
  const args = ['--ozone-platform=wayland', 'song.musicxml', pathToFileURL(path.join(folder, 'scan.pdf')).href, 'notes.txt', 'folder.pdf', 'missing.pdf', path.join(folder, 'song.MXL'), 'photo.jpg', 'song.musicxml', '.']
  assert.deepEqual(desktop.scoreFiles(args, folder), [
    path.join(folder, 'song.musicxml'),
    path.join(folder, 'scan.pdf'),
    path.join(folder, 'song.MXL'),
    path.join(folder, 'photo.jpg')
  ])
})

test('the page gets a MIDI keyboard, and nothing it has no use for', () => {
  assert.equal(desktop.allowedPermission('midi', {}), true)
  assert.equal(desktop.allowedPermission('midi', { sysex: true }), false)
  assert.equal(desktop.allowedPermission('midiSysex', {}), false)
  assert.equal(desktop.allowedPermission('clipboard-sanitized-write'), true)
  for (const permission of ['media', 'geolocation', 'notifications', 'clipboard-read', 'openExternal', 'hid', 'serial', 'usb']) {
    assert.equal(desktop.allowedPermission(permission, {}), false, permission)
  }
})

test('the app\'s own pages stay in its windows and other sites go to the browser', () => {
  const base = 'http://127.0.0.1:47360'
  assert.equal(desktop.sameOrigin('http://127.0.0.1:47360/api/scores/x/score.pdf?part=1', base), true)
  assert.equal(desktop.sameOrigin('http://127.0.0.1:47361/', base), false)
  assert.equal(desktop.sameOrigin('http://localhost:47360/', base), false)
  assert.equal(desktop.sameOrigin('not a url', base), false)
  assert.equal(desktop.opensOutside('https://musescore.org/en/handbook'), true)
  assert.equal(desktop.opensOutside('mailto:someone@example.com'), true)
  for (const url of ['file:///etc/passwd', 'javascript:alert(1)', 'smb://server/share', 'nonsense']) assert.equal(desktop.opensOutside(url), false, url)
})

test('the usual port is taken first, and the next one when something else has it', async () => {
  const blocker = net.createServer()
  await new Promise(resolve => blocker.listen(0, '127.0.0.1', resolve))
  const taken = blocker.address().port
  const free = await desktop.anyFreePort()
  try {
    assert.ok(free > 0 && free !== taken)
    assert.equal(await desktop.portIsFree(taken), false)
    assert.equal(await desktop.portIsFree(free), true)
    assert.equal(await desktop.choosePort([free, taken]), free)
    assert.equal(await desktop.choosePort([taken, free]), free)
    const fallback = await desktop.choosePort([taken])
    assert.ok(fallback > 0 && fallback !== taken)
    assert.ok((await desktop.choosePort([0])) > 0, 'never port 0, which would mean any port to the server')
  } finally {
    blocker.close()
  }
})

test('waiting for the server ends when it answers, when it stops, or when time runs out', async () => {
  let calls = 0
  const server = await listen((_request, response) => {
    calls += 1
    response.statusCode = calls < 3 ? 503 : 200
    response.end('[]')
  })
  const url = `http://127.0.0.1:${server.address().port}/api/scores`
  try {
    await desktop.waitForServer(url, { timeoutMs: 5000, alive: () => true, intervalMs: 10 })
    assert.equal(calls, 3)
    await assert.rejects(desktop.waitForServer(url, { timeoutMs: 5000, alive: () => false }), /stopped while starting/)
  } finally {
    server.close()
  }
  const nobody = await desktop.anyFreePort()
  await assert.rejects(desktop.waitForServer(`http://127.0.0.1:${nobody}/`, { timeoutMs: 100, alive: () => true, intervalMs: 10 }), /didn't answer/)
})

test('a file is sent as the page sends it, and a refusal says why', async () => {
  const folder = tempDir()
  const file = path.join(folder, 'Tune.musicxml')
  fs.writeFileSync(file, '<score-partwise/>')
  let received = null
  const server = await listen((request, response) => {
    let body = ''
    request.on('data', chunk => { body += chunk })
    request.on('end', () => {
      received = { method: request.method, url: request.url, type: request.headers['content-type'], origin: request.headers.origin, body }
      response.setHeader('content-type', 'application/json')
      if (body.includes('<score-partwise/>')) response.end(JSON.stringify({ id: 'abc' }))
      else {
        response.statusCode = 415
        response.end(JSON.stringify({ detail: 'That score couldn\'t be opened.' }))
      }
    })
  })
  const base = `http://127.0.0.1:${server.address().port}`
  try {
    assert.equal(await desktop.uploadScore(base, file), 'abc')
    assert.equal(received.method, 'POST')
    assert.equal(received.url, '/api/scores')
    assert.match(received.type, /^multipart\/form-data; boundary=/)
    assert.match(received.body, /name="file"; filename="Tune.musicxml"/)
    assert.equal(received.origin, undefined) // The server lets requests from no page through.
    fs.writeFileSync(file, 'something else')
    await assert.rejects(desktop.uploadScore(base, file), /That score couldn't be opened\./)
  } finally {
    server.close()
  }
})

test('the end of the log is kept for saying what went wrong', () => {
  const folder = tempDir()
  const log = path.join(folder, 'server.log')
  fs.writeFileSync(log, Array.from({ length: 40 }, (_, n) => `line ${n}`).join('\n') + '\n')
  assert.equal(desktop.logTail(log, 2), 'line 38\nline 39')
  assert.equal(desktop.logTail(path.join(folder, 'none')), '')
})

// The real server, started the way the app starts it.
const python = path.join(REPO, '.venv', 'bin', 'python')
test('the real server keeps an opened score in the given folder, and stops once the app has gone', { skip: !fs.existsSync(python) && 'no .venv here' }, async () => {
  const jobs = tempDir()
  const port = await desktop.anyFreePort()
  const child = spawn(python, desktop.serverArguments(port, jobs), { cwd: REPO, env: desktop.childEnv(process.env), stdio: ['pipe', 'ignore', 'pipe'] })
  let errors = ''
  child.stderr.on('data', chunk => { errors += chunk })
  const exited = new Promise(resolve => child.once('exit', (code, signal) => resolve({ code, signal })))
  try {
    const base = `http://127.0.0.1:${port}`
    await desktop.waitForServer(`${base}/api/scores`, { timeoutMs: 60_000, alive: () => child.exitCode === null })
    const id = await desktop.uploadScore(base, path.join(REPO, 'data', 'truth', 'chart_00.musicxml'))
    assert.match(id, /^[0-9a-f]{32}$/)
    assert.ok(fs.existsSync(path.join(jobs, id)), 'the score is in the folder the app chose')
    const listed = await (await fetch(`${base}/api/scores`)).json()
    assert.deepEqual(listed.map(score => score.id), [id])
    child.stdin.end() // As when the app ends: its end of the pipe closes.
    const result = await Promise.race([exited, new Promise(resolve => setTimeout(() => resolve('still running'), 20_000))])
    assert.notEqual(result, 'still running', errors)
  } finally {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL')
  }
})

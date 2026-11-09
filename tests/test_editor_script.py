"""The page's own script, run in Node against a stand-in server.

Only its functions are loaded, not the page, so each test sets up what the code it calls reads.
These are skipped where Node isn't installed. The browser tests in e2e_editor.py cover the rest.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node isn't installed")

SETUP = """
let queue = Promise.resolve();
let scoreId = "s", score = null, selected = "note1", editing = null, hint = "";
let range = { first: "note1", last: "note2", ids: ["note1", "note2"] };
let clipboard = [{ type: "whole" }];
const AFTER = "after:";
const GROUP_ACTIONS = new Set(["delete"]);
const editor = { on: false, type: "quarter", dots: 0, voice: 1, cursor: null, last: null, trail: [], future: [] };
const sessionStorage = { setItem() {} };
const asked = [], shown = [];
"""

# These come after the page's own functions, so they take the place of those that draw.
STAND_INS = """
function message(text) { if (text) shown.push(text); }
function updateTools() {}
function receive() {}
async function fetch(url) {
  asked.push(url.split("/").pop());
  const failed = COPY_FAILS && url.endsWith("/copy");
  return { ok: !failed, json: async () => (failed ? { detail: "The copy didn't work." } : { events: [{ type: "quarter" }] }) };
}
"""


def page_functions() -> str:
    """Return every top-level function of the page, in the order the page loads them."""
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    source = "\n".join(re.findall(r"<script>(.*?)</script>", page, re.S)) + "\n" + (STATIC / "editor.js").read_text(encoding="utf-8")
    return "\n\n".join(re.findall(r"^(?:async )?function \w+\(.*?^\}$", source, re.S | re.M))


def run(action: str, copy_fails: bool) -> dict:
    program = (
        SETUP + page_functions() + STAND_INS + f"const COPY_FAILS = {json.dumps(copy_fails)};\n"
        + f"{action}();\nqueue.then(() => console.log(JSON.stringify({{ asked, shown, clipboard }})));\n"
    )
    done = subprocess.run([NODE, "-"], input=program, capture_output=True, text=True, timeout=30, check=True)
    return json.loads(done.stdout)


def test_a_cut_whose_copy_fails_deletes_nothing():
    """Otherwise the notes are gone, and pasting brings back whatever was copied before."""
    done = run("cutSelection", copy_fails=True)
    assert done == {"asked": ["copy"], "shown": ["The copy didn't work."], "clipboard": [{"type": "whole"}]}


def test_a_cut_copies_and_then_deletes():
    done = run("cutSelection", copy_fails=False)
    assert done == {"asked": ["copy", "group"], "shown": [], "clipboard": [{"type": "quarter"}]}


def test_a_copy_still_copies_on_its_own():
    assert run("copySelection", copy_fails=False) == {"asked": ["copy"], "shown": [], "clipboard": [{"type": "quarter"}]}

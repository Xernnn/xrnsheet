"""Measure how quickly the editor opens a score and redraws it after an edit, and what the page loads.

Start the server, open the scores to measure in it (any MusicXML file), then run this with a Python
that has Playwright:

    .venv/bin/python -m app.server --port 8012
    python bench/editor_speed.py --port 8012 SCORE_ID [SCORE_ID ...]

Set XRNSHEET_BROWSER to use a Chromium that's already installed. Each score is opened several
times and edited several times, and the median is printed. An edit moves a note up a semitone, so each one is a score the
server hasn't drawn before, and they're all undone at the end, so the score ends as it started.

- **load**: the bytes the start page fetches before anything is opened, as sent and once unpacked.
- **open**: from asking for a score until its pages are drawn and laid out, the first time and
  then again, as when going back to it. Restart the server before measuring the first time.
- **edit**: from an edit being asked for until the page is redrawn and laid out, split into the
  server's reply (the request and its answer arriving) and the page's own work.
"""

import argparse
import json
import os
import statistics

from playwright.sync_api import sync_playwright

# Two frames, so the time includes the browser laying the new pages out and painting them.
SETTLED = "() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))"


def load_size(page, base: str) -> dict:
    page.goto(base)
    page.wait_for_load_state("networkidle")
    entries = page.evaluate("""() => [...performance.getEntriesByType("navigation"), ...performance.getEntriesByType("resource")]
        .map(entry => ({ name: entry.name, sent: entry.transferSize, size: entry.decodedBodySize }))""")
    # Thumbnails are the scores themselves, which differ from one person to the next.
    counted = [entry for entry in entries if "/thumbnail.svg" not in entry["name"] and "/api/" not in entry["name"]]
    return {
        "files": len(counted),
        "sent_kb": round(sum(entry["sent"] for entry in counted) / 1024, 1),
        "unpacked_kb": round(sum(entry["size"] for entry in counted) / 1024, 1),
    }


def open_time(page, base: str, score_id: str, times: int) -> dict:
    found = []
    for _ in range(times):
        page.goto(base)
        page.wait_for_load_state("networkidle")
        found.append(page.evaluate(f"""async id => {{
            const start = performance.now();
            await openScore(id);
            await ({SETTLED})();
            return performance.now() - start;
        }}""", score_id))
    # The first time a score is drawn after the server starts, and again, as when going back to it.
    return {"open_ms": round(found[0]), "reopen_ms": round(statistics.median(found[1:]))}


def edit_time(page, score_id: str, times: int) -> dict:
    # Wrapping fetch shows when the server's answer has arrived and been read, so the rest is the page.
    page.evaluate("""() => {
        if (window.timedFetch) return;
        const plain = window.fetch;
        window.timedFetch = true;
        window.fetch = async (...args) => {
            const response = await plain(...args);
            const json = response.json.bind(response);
            response.json = async () => { const data = await json(); window.answered = performance.now(); return data; };
            return response;
        };
    }""")
    page.evaluate("id => { const note = document.querySelector('#pages g.note'); select(note.id); }", score_id)
    total, server = [], []
    for _ in range(times):
        action = "semitoneUp"
        took = page.evaluate(f"""async action => {{
            const start = performance.now();
            await noteAction(action);
            await queue;
            await ({SETTLED})();
            return [performance.now() - start, window.answered - start];
        }}""", action)
        total.append(took[0])
        server.append(took[1])
    page.evaluate(f"async () => {{ for (let step = 0; step < {times}; step++) await undoEdit(); await queue; }}")
    whole, reply = statistics.median(total), statistics.median(server)
    return {"edit_ms": round(whole), "edit_reply_ms": round(reply), "edit_page_ms": round(whole - reply)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scores", nargs="+", help="ids of scores already in the app")
    parser.add_argument("--port", type=int, default=8012)
    parser.add_argument("--times", type=int, default=5)
    arguments = parser.parse_args()
    base = f"http://127.0.0.1:{arguments.port}"
    with sync_playwright() as driver:
        browser = driver.chromium.launch(executable_path=os.environ.get("XRNSHEET_BROWSER"))
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        print(json.dumps({"load": load_size(page, base)}))
        for score_id in arguments.scores:
            pages = json.loads(page.evaluate("async id => JSON.stringify((await (await fetch(`/api/scores/${id}`)).json()).pages.length)", score_id))
            result = {"score": score_id[:8], "pages": pages} | open_time(page, base, score_id, arguments.times) | edit_time(page, score_id, arguments.times * 2)
            print(json.dumps(result), flush=True)
        browser.close()


if __name__ == "__main__":
    main()

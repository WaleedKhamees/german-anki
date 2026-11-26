"""Screenshots of verbformen.com's conjugation/declension tables.

One headless Chromium serves the whole run (starting it costs 1–2 s per word). Playwright's sync
API only works on the thread that started it, so every page load runs on one worker thread;
callers on other threads just wait for it. Call close() when the run is done.
"""
import concurrent.futures
import threading
import time
import urllib.parse

from playwright.sync_api import sync_playwright

MIN_INTERVAL = 1.0  # seconds between verbformen page loads (politeness; the only rate-limited site)
_last_load = 0.0

_lock = threading.Lock()
_executor = None             # the single thread that owns Playwright
_playwright = _browser = None


def _route(route):
    """Load only verbformen's own pages/scripts/styles: images, media and fonts are not needed for the
    table, and third-party requests (consent manager, ads) only slow the page down (~1.5s -> ~0.9s)."""
    req = route.request
    host = urllib.parse.urlparse(req.url).hostname or ""
    if req.resource_type in ("image", "media", "font") or not (host == "verbformen.com" or host.endswith(".verbformen.com")):
        return route.abort()
    return route.continue_()


def _wait_for_slot():
    """Keep at least MIN_INTERVAL between page loads (all loads run on the browser thread)."""
    global _last_load
    wait = MIN_INTERVAL - (time.monotonic() - _last_load)
    if wait > 0:
        time.sleep(wait)
    _last_load = time.monotonic()


def urls(word, is_noun=False):
    """Pages to try for the table. The search URL (/conjugation/?w=) prefers the verb when a word is
    both (Schaden -> 'schaden'), so for nouns go to the noun declension page first."""
    search = f"https://www.verbformen.com/conjugation/?w={urllib.parse.quote(word)}"
    if not is_noun:
        return [search]
    noun = word.strip()
    noun = noun[:1].upper() + noun[1:]
    return [f"https://www.verbformen.com/declension/nouns/{urllib.parse.quote(noun)}.htm", search]


def _get_browser():
    """On the browser thread: the shared Chromium, started (or restarted after a crash) on demand."""
    global _playwright, _browser
    if _browser is None or not _browser.is_connected():
        if _playwright is None:
            _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(headless=True)
    return _browser


def _capture(word, output_path, is_noun):
    context = _get_browser().new_context(color_scheme="dark")
    try:
        page = context.new_page()
        page.route("**/*", _route)
        problem = "no page tried"
        for url in urls(word, is_noun):
            _wait_for_slot()
            response = page.goto(url)
            if response is not None and response.status >= 400:
                problem = f"HTTP {response.status} for {url}"
                continue
            if is_noun and not page.title().startswith("Declension"):
                problem = f"{url} is not a noun declension page"
                continue
            try:
                page.evaluate("""
                    const banner = document.querySelector('.cmpwrapper');
                    if (banner) { banner.remove(); }
                """)
                page.wait_for_timeout(200)
            except Exception:
                pass

            locator = page.locator("section > .rAufZu")
            count = locator.count()
            if count < 2:
                problem = f"Only found {count} matching elements on {url}"
                continue

            table_locator = locator.nth(1)
            table_locator.wait_for(state="visible", timeout=10000)
            table_locator.screenshot(path=output_path)
            return url
        raise Exception(problem)
    finally:
        context.close()


def capture(word, output_path, is_noun=False):
    """Screenshot verbformen's conjugation/declension table for `word` into output_path.
    For a noun only a declension page is accepted, never a verb's conjugation table."""
    global _executor
    t0 = time.time()
    with _lock:
        if _executor is None:
            _executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="verbformen")
        executor = _executor
    try:
        url = executor.submit(_capture, word, output_path, is_noun).result()
    except Exception as e:
        print(f"[⏱️] Screenshot failed/skipped after {time.time() - t0:.2f} seconds: {e}")
        raise
    print(f"[⏱️] Screenshot capture took: {time.time() - t0:.2f} seconds ({url})")


def _shutdown():
    global _playwright, _browser
    try:
        if _browser is not None:
            _browser.close()
        if _playwright is not None:
            _playwright.stop()
    finally:
        _playwright = _browser = None


def close():
    """Close the shared browser, if one was started."""
    global _executor
    with _lock:
        executor, _executor = _executor, None
    if executor is not None:
        try:
            executor.submit(_shutdown).result()
        except Exception:
            pass
        executor.shutdown()

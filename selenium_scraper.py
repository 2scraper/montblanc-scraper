#!/usr/bin/env python3
"""
montblanc-scraper — Selenium edition (secondary engine)
=======================================================

The same scrape as playwright_scraper.py, driven through Selenium. It must
agree with its twins on exit codes, run status, and whether a run crashes or
spends money — the decisions that determine all three live in page_flow.py
and output_writer.finish_run(), so this file is browser plumbing and nothing
else.

    --mode category   (default)  /{locale}/{category-path}?start=N&sz=24
    --mode search                /{locale}/search?q=…&start=N&sz=24
    --mode product               one /{locale}/{slug}-MB{id}.html page,
                                 emitted as one row PER VARIANT

See playwright_scraper.py's header for what is different about this site.

Two limits of this engine, stated here rather than left to be discovered.
Neither is a bug in this code and neither can be fixed from here:

  * **Selenium cannot use an authenticated remote CDP endpoint.** Playwright's
    `connect_over_cdp` and pyppeteer's `browserWSEndpoint` take a full
    `ws://user:pass@host:port` and authenticate on the WebSocket upgrade.
    chromedriver's `debuggerAddress` takes a bare `host:port` and has nowhere
    to put a password. So --cdp-endpoint here works only for an endpoint that
    needs no credentials; a credentialed one is refused with exit 2 rather
    than connected to and silently failing.
  * **Selenium cannot authenticate a proxy at all.** `--proxy-server=` accepts
    no credentials, and there is no equivalent of pyppeteer's
    `page.authenticate`. Credentials are stripped and a warning says so, so
    nobody believes a `user:pass` URL is doing something.

On Montblanc those two limits bite less than they would elsewhere, and it is
worth saying why rather than leaving it to be assumed: the site served an
ordinary datacenter address on every mode measured (2026-09-17), with no
key, no proxy and no challenge. So this engine reads all three modes as well
as its twins do, and the limits matter only if you need a SPECIFIC MARKET's
exit or the volume that a pool of exits buys — which, on a site that sets
its prices per market, is a real reason to want one.

There is no --concurrency here either: parallel page fetching lives in the
Playwright engine.

Usage
-----
    python selenium_scraper.py \\
        --url "https://www.montblanc.com/en-us/writing-instruments" --pages 3

Requires: pip install -r requirements.txt -r requirements-selenium.txt
"""

import argparse
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse, urlsplit

from selenium import webdriver
from selenium.common.exceptions import (TimeoutException, WebDriverException,
                                        JavascriptException)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from captcha_solver import (detect_recaptcha_v3, detect_recaptcha_in_page,
                            reconcile_detections, solve_recaptcha,
                            CaptchaUnsolvable, INJECT_TOKEN_JS)
from product_parser import (DEFAULT_LOCALE, DEFAULT_SORT, PAGE_SIZE, SORTS,
                            category_from_url, category_url,
                            detect_bot_challenge, is_product_url,
                            is_supported_url, locale_from_url,
                            locale_is_known, page_url, pages_available,
                            parse_listing, parse_product_detail,
                            parse_products, product_link_count,
                            references_own_assets, search_url,
                            total_results)
from output_writer import (dedupe_by_key, finish_run, EXIT_API_ERROR,
                           SOURCE_DEFAULT)
import page_flow
from page_flow import MIN_CARD_MATCHES
from proxy_pool import (from_args as proxy_pool_from_args, mask, ROTATE_MODES,
                        ProxyError, split_credentials)
import env_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("selenium_scraper")

ITEM_LINK_SELECTOR = page_flow.READY_SELECTOR_LISTING

# The share of rows that must carry the columns Montblanc populates on every
# listing row. Measured over 192 listing rows on four categories
# (2026-09-17): title, url, sku, currency and image_url on 192 of 192.
#
# Deliberately NOT here: `price`, genuinely absent from the JSON-LD on 14 of
# those rows (the range-priced variant groups, recovered from the DOM), and
# `size`, a watch/belt attribute present on 12%.
CORE_FIELD_FLOOR = 99
CORE_FIELDS = ("title", "url", "sku", "currency")

# `image_url` is REPORTED but deliberately NOT floored, and that is a
# measurement rather than a shrug. Montblanc's ItemList can name a product
# whose grid tile the page does not render at all, and such an entry carries
# `"image": null` and `"brand": null` in the JSON-LD too — so there is
# nothing anywhere on the page to read. Measured on a live 4-page run:
# 1 row of 92 (MB127852M), which is under a 99% floor and is CORRECT DATA.
# A floor that fires on healthy pages teaches the reader to ignore floors.

# The share of rows that must carry a usable price. Both `jsonld` and
# `dom_range` count, so this fires on a parsing break rather than on the
# site's own variety.
PRICE_COVERAGE_FLOOR = 95

# A page holding less than this share of the page size is reported as thin.
# The size is the site's own `sz`, so the only legitimately short page is the
# last one of a listing.
THIN_PAGE_SHARE = 0.6


PAGE_LOAD_TIMEOUT = 60
SCRIPT_TIMEOUT = 30

# Chromium's own names for "the proxy is the problem, not the site". A dead
# proxy and a slow page want opposite responses — a different exit versus
# another try at the same one — so they are told apart by the error text.
_PROXY_ERROR_MARKERS = (
    "ERR_PROXY_CONNECTION_FAILED", "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_PROXY_AUTH_UNSUPPORTED", "ERR_PROXY_AUTH_REQUESTED",
    "ERR_UNEXPECTED_PROXY_AUTH", "ERR_PROXY_CERTIFICATE_INVALID",
)


@dataclass
class PageOutcome:
    """What one page produced. Mirrors playwright_scraper.PageOutcome."""
    page_num: int
    url: str
    final_url: Optional[str] = None
    products: List = field(default_factory=list)
    blocked_by: Optional[str] = None
    load_failed: bool = False
    state: Optional[str] = None
    # Montblanc's OWN arithmetic: the `result-count` printed above the grid
    # and how many pages the site will serve. Real numbers here, unlike the
    # sibling repos' result headers — they are what pages are planned from.
    total_available: Optional[int] = None
    pages_available: Optional[int] = None
    # The CATEGORY'S OWN DEFAULT ordering, read off the page — NOT what was
    # than echoed from the request.
    sort_applied: Optional[str] = None

    @property
    def ok(self) -> bool:
        return not self.load_failed and self.blocked_by is None


# Every `scheme://user:pass@` in a string, however many times it occurs.
# Matching globally rather than once is the point: a driver's connection
# error can repeat the endpoint several times (the message plus a call log),
# so a masker that handled only the first occurrence would print the password
# the other times and look like it was working.
_CREDENTIALS_IN_URL_RE = re.compile(r"([a-z][a-z0-9+.\-]*://)[^\s/@]+:[^\s/@]+@",
                                    re.IGNORECASE)


def _mask_credentials(text: str) -> str:
    """`text` with any username:password in an embedded URL replaced.

    Takes arbitrary text, not just a URL, because the strings that most need
    this are exception messages with a URL inside them. The host and port are
    KEPT — which endpoint or exit a run used is the useful half of the line
    and is not the secret.
    """
    return _CREDENTIALS_IN_URL_RE.sub(r"\1***:***@", text or "")


def _chrome_ua(version: str) -> str:
    """A desktop-Chrome UA naming the browser's OWN real version.

    `driver.capabilities["browserVersion"]` is the installed Chrome's version,
    so the claim matches what the JS engine and the TLS handshake report. A
    hardcoded number drifts the moment Chrome updates, and claiming an older
    Chrome than everything else reports is itself a signal.
    """
    return (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{version} Safari/537.36")


def _cdp_host_port(endpoint: str) -> str:
    """`host:port` for chromedriver's debuggerAddress, or exit 2 with a reason.

    chromedriver takes a bare address here and cannot send credentials, so an
    endpoint that carries them cannot work through this engine. Refused up
    front: connecting anyway would fail somewhere further in with an error
    that names none of this.
    """
    parts = urlsplit(endpoint if "//" in endpoint else f"//{endpoint}")
    if parts.username or parts.password:
        logger.error(
            "This --cdp-endpoint carries credentials (%s), and Selenium cannot "
            "send them: chromedriver's debuggerAddress is a bare host:port. "
            "Use playwright_scraper.py or puppeteer_scraper.py for a "
            "credentialed endpoint such as the Scraping Browser API — both "
            "authenticate on the WebSocket upgrade.",
            _mask_credentials(endpoint))
        sys.exit(2)
    host = parts.hostname or endpoint
    port = f":{parts.port}" if parts.port else ""
    return f"{host}{port}"


class _Session:
    """One Chrome driver, relaunchable onto a different exit.

    Same contract as the Playwright engine's _BrowserSession, including the
    rule that a rotation means a genuinely FRESH browser — and a
    fresh browser is also the only thing that re-rolls the served page
    fresh cookie jar is what an ordinary user on another network looks like.
    """

    def __init__(self, args, pool):
        self.args, self.pool = args, pool
        self.remote = bool(args.cdp_endpoint)
        self.driver = None

    def open(self):
        options = Options()
        if self.remote:
            options.debugger_address = _cdp_host_port(self.args.cdp_endpoint)
            logger.info("Attaching to an existing browser at %s.",
                        options.debugger_address)
            # No UA, no proxy, no fingerprint on this path: the remote browser
            # brings its own, and stacking a second creates a contradiction
            # rather than better cover.
            self.driver = webdriver.Chrome(options=options)
            self._apply_timeouts()
            return self

        if self.args.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1600,1000")
        # Not a fingerprint measure, a correctness one: without it Chrome
        # advertises "HeadlessChrome", which is a giveaway on any site with
        # a bot manager in front of it.
        options.add_argument("--disable-blink-features=AutomationControlled")
        # Flag parity with the Playwright engine, and really applied rather
        # than accepted and ignored: Chrome takes the locale as --lang. It
        # does NOT decide which market is read — that is the locale in
        # find_country in the URL — so this only affects what the browser
        # claims about itself.
        options.add_argument(f"--lang={self.args.locale}")

        if self.pool:
            scrubbed, credentials = split_credentials(self.pool.current)
            options.add_argument(f"--proxy-server={scrubbed}")
            logger.info("Using proxy exit %s", mask(self.pool.current))
            if credentials:
                logger.warning(
                    "This proxy has credentials and SELENIUM CANNOT SEND "
                    "THEM: --proxy-server accepts an address only, and there "
                    "is no Selenium equivalent of pyppeteer's "
                    "page.authenticate. They have been stripped, so requests "
                    "will go out unauthenticated and the exit will most "
                    "likely refuse them. Use playwright_scraper.py or "
                    "puppeteer_scraper.py for an authenticated proxy.")

        self.driver = webdriver.Chrome(options=options)
        self._apply_timeouts()

        version = self.driver.capabilities.get("browserVersion", "")
        if version:
            # Set over CDP rather than as a launch switch, so it can use the
            # version the driver actually reports.
            try:
                self.driver.execute_cdp_cmd(
                    "Network.setUserAgentOverride",
                    {"userAgent": _chrome_ua(version)})
            except WebDriverException as e:
                logger.debug("Could not override the user agent: %s", e)

        if self.args.fingerprint:
            self._apply_fingerprint()
        return self

    def _apply_timeouts(self):
        # Explicit, because a driver that stops answering otherwise hangs the
        # run: "every remote call is bounded" applies to this engine too.
        self.driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        self.driver.set_script_timeout(SCRIPT_TIMEOUT)

    def _apply_fingerprint(self):
        from fingerprint_client import get_fingerprint, playwright_init_script
        fp = get_fingerprint(self.args.twocaptcha_key, tags=self.args.fp_tags,
                             country=self.args.fp_country)
        ua = (fp.get("userAgent") or {}).get("value")
        script = playwright_init_script(fp)
        try:
            if ua:
                self.driver.execute_cdp_cmd("Network.setUserAgentOverride",
                                            {"userAgent": ua})
            # The same patch script the Playwright engine installs on its
            # context. Shared deliberately: two engines applying different
            # halves of one fingerprint would be a contradiction of exactly
            # the kind a fingerprint is meant to avoid.
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument", {"source": script})
            logger.info("Using 2captcha fingerprint %s (%s)", fp.get("id"),
                        fp.get("country"))
        except WebDriverException as e:
            logger.warning("Could not apply the fingerprint over CDP (%s) — "
                           "continuing without it.", e)

    def relaunch(self):
        if self.remote:
            return
        self.close()
        self.open()

    def close(self):
        try:
            if self.driver is not None:
                # quit(), not close(): close() ends one window and leaves the
                # driver process running, which on a per-page rotation would
                # leak a chromedriver per page.
                self.driver.quit()
        except Exception as e:  # noqa: BLE001 — teardown must not mask the reason we're here
            logger.debug("Ignoring error during driver teardown: %s", e)


# ---------------------------------------------------------------------------
# page_flow, bound to Selenium
# ---------------------------------------------------------------------------
# Only "how to ask this driver" lives here. Note the JS dialect: Selenium's
# execute_script runs a function BODY and needs an explicit `return`, unlike
# the `() => expr` both other engines take — which is why page_flow names
# operations instead of passing JavaScript.
def _driver(session):
    driver = session.driver

    def count(selector):
        try:
            return len(driver.find_elements(By.CSS_SELECTOR, selector))
        except WebDriverException as e:
            logger.debug("count(%s) failed: %s", selector, e)
            return 0

    def sleep(ms):
        time.sleep(ms / 1000.0)

    def content():
        try:
            return driver.page_source
        except WebDriverException as e:
            # A URL canonicalisation can navigate, so a snapshot can land on
            # the document swap. None tells the caller to skip a check rather
            # than fail the run.
            logger.debug("page_source unavailable (page navigating?): %s", e)
            return None

    def current_url():
        try:
            return driver.current_url
        except WebDriverException:
            return ""

    # No scroll primitive, and its absence is measured rather than
    # forgotten: Montblanc serves its whole result set in the first response, so
    # there is nothing to scroll into view. Mirrors the Playwright engine.
    return {"count": count, "sleep": sleep, "content": content,
            "current_url": current_url}


def _parse_for_mode(html: str, url: str, args, page_num: int = 1):
    """(rows, listing) for this mode. `listing` is None in --mode product.

    `parse_product_detail` returns one row PER VARIANT — sixteen for a
    sixteen-nib pen — so the two modes already agree on shape and neither
    needs wrapping. What differs is that a product page has no listing-level
    arithmetic to report, hence the None.

    The two entry points are separate on purpose and it is not tidiness.
    A product page publishes `ProductGroup` and no `ItemList`, so calling
    the listing parser on one finds no structured data and then falls
    through to its tile path — which on a real product page returns the
    "you may also like" carousel as eighteen products, measured, in
    silence. `parse_products` refuses a detail page for that reason and
    `smoke_test.py` pins both directions (§20).

    `page_num` is threaded through rather than defaulted, because `position`
    restarts at 1 on every page: without the page number beside it, a row
    from page 2 claims the same position as one from page 1 and the two are
    indistinguishable in the output. `smoke_test.py` asserts page+position
    is unique across a multi-page run for exactly that reason.
    """
    if args.mode == "product":
        rows = parse_product_detail(html, url, mode=args.mode, sort=args.sort)
        if args.category:
            for row in rows:
                row.category = args.category
        return rows, None
    listing = parse_listing(html, url, page=page_num, mode=args.mode,
                            sort=args.sort, page_size=args.page_size)
    if args.category:
        for row in listing.rows:
            row.category = args.category
    return listing.rows, listing


def _is_endpoint(url: str) -> bool:
    """Whether this address answers with JSON rather than a page.

    Always False on Montblanc, and the function is kept rather than deleted
    because `_snapshot` branches on it and a sibling repo's version of this
    file does have a JSON route. Returning a constant with the reason beside
    it is honest; silently dropping the branch would make the two engines
    diverge in shape for no reason.

    Montblanc's own front end does call a fragment endpoint
    (`…/Search-UpdateGrid?cgid=…&start=…&sz=…`) — checked, per CLAUDE.md §21,
    before assuming a browser was needed — but it answers with HTML tiles and
    NO JSON-LD, so it is read exactly like a page and the full-page route is
    preferred anyway for keeping structured data on every page.
    """
    return False


def _snapshot(session, url: str):
    """What the parser is given for this address.

    A PAGE is read with `page_source`. The ENDPOINT answers with JSON, which
    Chromium wraps in its own JSON-viewer markup — so `page_source` there
    returns the viewer's HTML and the payload would be unreachable. Reading
    the body text gives back exactly what the server sent.

    Note the JS dialect: a function BODY with an explicit `return`, not the
    arrow expression the other two engines pass. That difference is exactly
    why no JavaScript crosses the page_flow boundary.
    """
    if _is_endpoint(url):
        try:
            return session.driver.execute_script(
                "return document.body.innerText;") or ""
        except WebDriverException as e:
            logger.warning("Could not read the endpoint response: %s", e)
            return None
    return _driver(session)["content"]()


def handle_captcha_if_present(session, args) -> bool:
    """Detect and solve a challenge. True if something was solved.

    Same detectors, same reconciliation and the same "detected is not
    blocking" rule as the Playwright engine — the three must agree about
    when a run spends money.

    NOTE what this cannot help with: Montblanc's refusal is not an HTTP
    403 carrying its own error page with no challenge on it, so no solve
    applies there and none is attempted. See product_parser.detect_page_state.
    """
    driver = session.driver
    d = _driver(session)
    html = d["content"]()
    if html is None:
        return False

    selector = page_flow.ready_selector(args.mode)
    already_rendered = d["count"](selector)
    when_blocked = getattr(args, "solve_captcha", "when-blocked") == "when-blocked"

    html_challenge = detect_recaptcha_v3(html, d["current_url"]())
    runtime_challenge = detect_recaptcha_in_page(
        lambda js: driver.execute_script(f"return ({js})();"),
        page_url=d["current_url"]())
    challenge = reconcile_detections(html_challenge, runtime_challenge)
    if not challenge:
        return False
    if when_blocked and already_rendered > MIN_CARD_MATCHES:
        logger.info("%s detected via %s, but %d anchors are already on the "
                    "page — not solving it.", challenge.kind, challenge.source,
                    already_rendered)
        return False
    logger.warning("%s detected via %s (sitekey=%s) — attempting to solve.",
                   challenge.kind, challenge.source, challenge.sitekey)
    if not args.twocaptcha_key:
        logger.warning("No 2captcha API key, so this challenge cannot be solved.")
        return False
    try:
        token = solve_recaptcha(challenge, args.twocaptcha_key,
                                api_version=args.captcha_api,
                                min_score=args.min_score)
    except Exception as e:  # noqa: BLE001
        logger.error("Solving the challenge failed (%s).", e)
        return False
    try:
        driver.execute_script(f"return ({INJECT_TOKEN_JS})(arguments[0]);", token)
    except WebDriverException as e:
        logger.error("Could not inject the token (%s).", e)
        return False
    logger.info("Token injected. Reloading page to continue.")
    time.sleep(1.5)
    driver.refresh()
    return True


def _target_url(args) -> str:
    """The address this run actually fetches.

    Montblanc has no JSON endpoint to prefer over the rendered page — the
    structured data IS in the page, as a JSON-LD `ItemList` on a listing and
    a `ProductGroup` on a product — so this is the URL the user gave, with
    the run's page size and ordering applied to it.

    Applying them HERE rather than only to pages 2..N matters: page 1 under
    the site's own default ordering is a different set of products from page
    1 under `--sort price-asc` (0 of 24 in common, measured), so a run whose
    first page skipped the sort would hold one page of one sample and N-1
    pages of another.
    """
    if args.mode == "product":
        return args.url
    return page_url(args.url, 1, args.page_size, args.sort)


def _fetch_one_page(session, args, pool, page_num: int, url: str) -> PageOutcome:
    """Fetch and parse one page. Mirrors playwright_scraper._fetch_one_page.

    Kept structurally parallel to its twins on purpose — "all three engines
    agree" is checked by reading them side by side as well as by the smoke
    suite.
    """
    outcome = PageOutcome(page_num=page_num, url=url)
    d = _driver(session)
    html, state, load_failed = None, "ok", False

    # See the Playwright engine for the measurement: without a pool there is
    # no exit to rotate to, but a plain re-fetch is what clears a block on a
    # Scraping Browser profile, so the budget is not zero.
    has_pool = bool(pool and len(pool) > 1)
    # `RETRY_ON_BLOCKED` is CONSULTED, not just documented. It was a
    # constant with a paragraph of justification that no engine read — a
    # policy statement nothing enforced, which is the same defect as dead
    # code that looks load-bearing. Setting it False now really does stop
    # the retry loop.
    block_retries = 0 if not page_flow.RETRY_ON_BLOCKED else (
        args.proxy_block_retries if has_pool
        else page_flow.BLOCK_RETRIES_WITHOUT_POOL)
    # Counted across the whole block-retry loop, not per attempt: a page that
    # keeps coming back as a challenge would otherwise buy one solve per
    # rotation, which is how a run quietly turns into a bill.
    solves_bought = 0

    for block_attempt in range(block_retries + 1):
        logger.info("Fetching page %d/%d: %s", page_num, args.pages, url)
        load_failed, exit_failed = False, None
        for attempt in range(1, args.retries + 1):
            try:
                session.driver.get(url)
                load_failed = False
                break
            except (TimeoutException, WebDriverException) as e:
                text = str(e)
                reason = next((m for m in _PROXY_ERROR_MARKERS if m in text), "")
                load_failed = True
                if reason:
                    exit_failed = reason
                    break  # a different exit is the only thing that helps
                if attempt < args.retries:
                    pause = args.retry_delay * (2 ** (attempt - 1))
                    logger.warning("Failed to load %s (attempt %d/%d: %s) — "
                                   "retrying in %.1fs.", url, attempt,
                                   args.retries, text[:120], pause)
                    time.sleep(pause)

        if exit_failed and has_pool and block_attempt < block_retries:
            logger.warning("Exit %s is unusable (%s) — rotating to another "
                           "one (%d/%d).", mask(pool.current), exit_failed,
                           block_attempt + 1, block_retries)
            pool.advance(f"unusable exit: {exit_failed}")
            session.relaunch()
            d = _driver(session)
            continue
        if load_failed:
            break


        if handle_captcha_if_present(session, args):
            time.sleep(1)

        html = _snapshot(session, url) or ""
        state = page_flow.classify(html, None, d["current_url"]())

        # Montblanc server-renders its data, so a listing is parseable in the
        # FIRST response and there is nothing to wait for on a healthy page.
        # Measured with no pause at all after the load. The wait below is
        # only for the state that says Montblanc served SOMETHING that is not a
        # payload. Mirrors playwright_scraper exactly.
        if state == "unknown":
            wait_ms = page_flow.content_timeout_ms(args.mode)
            sel = page_flow.ready_selector(args.mode)
            need = page_flow.min_matches(args.mode)
            logger.info("Page %d is something Montblanc served (%d bytes, its own "
                        "assets referenced %d time(s)) but carries no listing "
                        "payload — waiting up to %.0fs rather than spending a "
                        "retry.", page_num, len(html),
                        references_own_assets(html), wait_ms / 1000.0)
            found = page_flow.wait_for_count(d["count"], sel, need, wait_ms,
                                             d["sleep"])
            if found < need:
                logger.info("Still nothing after %.0fs (%d match(es) for %s).",
                            wait_ms / 1000.0, found, sel)
            html = _snapshot(session, url) or html
            state = page_flow.classify(html, None, d["current_url"]())

        # The paid path is reached only for state "captcha" — a rendered
        # widget, which IS a test. It is NOT reached for "blocked": an edge
        # refusal carries no widget and no sitekey, so a solve there would be
        # a charge for nothing. Bounded by SOLVES_PER_PAGE. Mirrors
        # playwright_scraper.
        if (page_flow.should_solve(state)
                and solves_bought < page_flow.SOLVES_PER_PAGE):
            solves_bought += 1
            if handle_captcha_if_present(session, args):
                time.sleep(1)
                html = d["content"]() or html
                state = page_flow.classify(html, url=d["current_url"]())
                if state == "content":
                    logger.info("The solve was accepted — page %d is content "
                                "now.", page_num)
                else:
                    logger.warning("The solve was NOT accepted: page %d is "
                                   "still %s. The purchase is spent.",
                                   page_num, state)

        if not page_flow.should_retry(state):
            # "content" and "empty" are both final answers. An empty page is
            # a CORRECT one — a hub category has no grid — so retrying it
            # would re-confirm the same right answer, and rotating the exit
            # would blame an address for the URL it was given.
            break

        # Blocked or challenged. The ADDRESS is what was scored, not the URL,
        # so a different exit is the only thing that plausibly changes the
        # outcome.
        if block_attempt < block_retries:
            logger.warning("Page %d came back as %s from %s — retrying from "
                           "another exit (%d/%d).", page_num, state,
                           mask(pool.current), block_attempt + 1, block_retries)
            pool.advance(f"{state} on page {page_num}")
            session.relaunch()
            d = _driver(session)

    if load_failed:
        logger.error("Gave up loading %s after %d attempt(s).", url, args.retries)
        outcome.load_failed = True
        return outcome

    outcome.state = state

    if state == "blocked":
        # Mirrors playwright_scraper's twin of this block: an edge refusal
        # offers no widget and no sitekey, so what a reader needs is which
        # refusal arrived and what to try, not a captcha hint.
        debug_html = f"{args.out}_page{page_num}_debug.html"
        with open(debug_html, "w", encoding="utf-8") as f:
            f.write(html or "")
        assets = references_own_assets(html or "")
        logger.error(
            "Montblanc did not serve this request — %d bytes, its own asset "
            "host referenced %d time(s), saved to %s. There is no widget on "
            "it, so no key would help. Note what this is NOT: an ordinary "
            "datacenter address is served normally by this site (measured "
            "2026-09-17 from a Hetzner IP — listings, search and product "
            "pages all HTTP 200, no proxy, no key), so a refusal here is "
            "unusual rather than expected. Check the User-Agent first — the "
            "edge refuses `curl`, `python-requests` and friends outright — "
            "then try a different exit with --proxy (this engine cannot "
            "authenticate one; see the README's engine limits). This "
            "is exit 3, distinct from a genuinely empty result (exit 4).%s",
            len(html or ""), assets, debug_html,
            (f" Tried {block_retries + 1} exit(s)." if has_pool
             else f" Re-fetched {block_retries + 1} time(s)."))
        outcome.blocked_by = "edge refusal" if html else "no-response"
        outcome.final_url = d["current_url"]()
        return outcome

    # No readiness wait and no scroll on the content path, and their absence
    # is MEASURED rather than forgotten — see the "unknown" branch above.
    # Mirrors playwright_scraper.

    if args.dump_html:
        dump_path = (args.dump_html if args.pages == 1
                     else f"{args.dump_html}.page{page_num}")
        with open(dump_path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("Saved the snapshot the parser sees to %s (%d bytes).",
                    dump_path, len(html))

    # Only when the page is NOT already content. A challenge marker on a
    # page whose products have rendered guards nothing — and over
    # --cdp-endpoint the Scraping Browser's own auto-solve extension injects
    # such markers into every page it loads.
    # Only for a state page_flow already counts as BLOCKED. An EMPTY page is
    # a correct answer, and a live run of a /p/<slug> hub reported exit 3 on
    # a page the site had plainly served because the hub's own performance
    # script names `akamaihd.net`. Mirrors playwright_scraper exactly.
    vendor = (detect_bot_challenge(html, url=d["current_url"]())
              if page_flow.counts_as_blocked(state) else None)
    if vendor:
        debug_html = f"{args.out}_page{page_num}_debug.html"
        with open(debug_html, "w", encoding="utf-8") as f:
            f.write(html)
        try:
            session.driver.save_screenshot(f"{args.out}_page{page_num}_debug.png")
        except WebDriverException as e:
            logger.warning("Could not capture screenshot: %s", e)
        logger.error("Blocked by %s before parsing (%d bytes) — saved to %s. "
                     "This is exit 3, distinct from a genuinely empty result "
                     "(exit 4).", vendor, len(html), debug_html)
        outcome.blocked_by = vendor
        return outcome

    final_url = d["current_url"]() or url
    if not page_flow.should_parse(state):
        logger.info("Page %d came back as %s; nothing to parse.", page_num,
                    state)
        outcome.final_url = final_url
        return outcome

    products, listing = _parse_for_mode(html, final_url, args, page_num)
    logger.info("Parsed %d row(s) from page %d.", len(products), page_num)

    if listing is not None:
        # Montblanc's own arithmetic, recorded on every page. Mirrors
        # playwright_scraper — the two engines must not disagree about what a
        # run holds.
        outcome.total_available = listing.total_results
        outcome.pages_available = listing.pages_available
        outcome.sort_applied = listing.site_default_sort
        if page_num == 1:
            logger.info("Montblanc reports %s result(s) across %s page(s) of "
                        "%s. Asked for ordering %r; the category's own "
                        "default is %r.", listing.total_results,
                        listing.pages_available, listing.page_size,
                        listing.sort, listing.site_default_sort)
            if (listing.site_default_sort and listing.sort
                    and listing.sort != listing.site_default_sort):
                # Not a disagreement: Montblanc honours the `srule`, it just
                # never states which one it used (see
                # product_parser.default_sort_rule), so there is nothing to
                # compare against. This reports the OVERRIDE, because the
                # ordering decides which products are in the file at all.
                logger.info("That is an override: a visitor with no --sort "
                            "would get %r and a different set of products.",
                            listing.site_default_sort)

    # §20: tell a BROKEN PARSER apart from an EMPTY CATEGORY before anything
    # downstream reports "0 products" and sends the reader to check the URL.
    if not products and page_flow.looks_like_a_parse_failure(
            state, len(products), product_link_count(html or "")):
        links = product_link_count(html or "")
        dump = f"{args.out}_page{page_num}_debug.html"
        with open(dump, "w", encoding="utf-8") as f:
            f.write(html or "")
        logger.error(
            "Page %d links to %d product(s) and parsed to ZERO rows. "
            "Montblanc served this page — this is a failure in THIS parser, "
            "not an empty category and not a block. Saved to %s; the first "
            "thing to check is the JSON-LD (an ItemList that was renamed or "
            "dropped), then the tile markup. Reported as stop_reason "
            "'parser_found_nothing' so it cannot be read as a complete run.",
            page_num, links, dump)
        outcome.state = "parse_failed"

    if products:
        images = sum(1 for row in products if row.image_url)
        if images < len(products):
            logger.info("Page %d: %d/%d rows carry an image. Montblanc's own "
                        "ItemList sometimes names a product it renders no "
                        "tile for, and those entries have no image in the "
                        "JSON-LD either — so this is reported, not floored.",
                        page_num, images, len(products))

        for field_name in CORE_FIELDS:
            filled = sum(1 for row in products
                         if getattr(row, field_name, None) not in (None, "", []))
            share = 100.0 * filled / len(products)
            if share < CORE_FIELD_FLOOR:
                logger.warning(
                    "Only %.0f%% of page %d carries `%s`, against a measured "
                    "floor of %d%%. All 192 listing rows across four captured "
                    "categories had one, so this is the page shape moving "
                    "rather than the products being unusual.",
                    share, page_num, field_name, CORE_FIELD_FLOOR)

        priced = sum(1 for row in products if row.price is not None)
        ranged = sum(1 for row in products if row.price_source == "dom_range")
        price_share = 100.0 * priced / len(products)
        if price_share < PRICE_COVERAGE_FLOOR:
            logger.warning(
                "Only %.0f%% of page %d carries a price, against a measured "
                "floor of %d%%. Both the JSON-LD price and the DOM range "
                "count toward that, so a shortfall is a parsing break rather "
                "than the site's own variety.", price_share, page_num,
                PRICE_COVERAGE_FLOOR)

        if args.mode == "product":
            group = {row.variant_of for row in products if row.variant_of}
            logger.info("Product: %d variant(s) of %s, %d priced.",
                        len(products), next(iter(group), products[0].sku),
                        priced)
        else:
            in_stock = sum(1 for row in products if row.in_stock is True)
            logger.info("Page %d: %d row(s), %d priced (%d from a 'From' "
                        "range, a MINIMUM over a variant group rather than a "
                        "price), %d in stock.",
                        page_num, len(products), priced, ranged, in_stock)

    if not products:
        debug_html = f"{args.out}_page{page_num}_debug.html"
        with open(debug_html, "w", encoding="utf-8") as f:
            f.write(html)
        try:
            session.driver.save_screenshot(f"{args.out}_page{page_num}_debug.png")
        except WebDriverException as e:
            logger.warning("Could not capture screenshot: %s", e)
        logger.warning("0 rows parsed — saved what the browser actually saw to "
                       "%s.", debug_html)

    outcome.products = products
    outcome.final_url = final_url
    return outcome


def scrape(args) -> int:
    outcomes: List[PageOutcome] = []
    seen_keys = set()
    blocked = False
    # All three modes are one row per product-at-a-location, so `sku` is the
    # key for all of them.
    dedupe_key = "sku"
    # Only --mode product is single-page. Both listing modes paginate
    # identically, so neither may be treated as single-page — that is the
    # silent-success failure this family exists to avoid.
    stop_reason = "single_page_mode" if args.mode == "product" else "completed"

    pool = proxy_pool_from_args(args)
    if pool and args.cdp_endpoint:
        logger.warning("Ignoring --proxy/--proxy-file: with --cdp-endpoint the "
                       "remote browser has its own exit, and layering a second "
                       "proxy on top would contradict it.")
        pool = None
    if args.concurrency > 1:
        logger.warning("--concurrency is ignored in this engine: parallel page "
                       "fetching is implemented in playwright_scraper.py, "
                       "which is the primary engine. Running one page at a "
                       "time.")

    session = None
    try:
        session = _Session(args, pool).open()

        target = _target_url(args)
        if target != args.url:
            logger.info("Applying --sort %s and --page-size %d to page 1 too: "
                        "%s", args.sort, args.page_size, target)

        first = _fetch_one_page(session, args, pool, 1, target)
        outcomes.append(first)

        if not first.ok:
            stop_reason = ("page_load_timeout" if first.load_failed
                           else f"blocked_{first.blocked_by}")
            blocked = first.blocked_by is not None
        elif first.state == "parse_failed":
            # Served, linked to products, parsed to nothing: OUR bug, and it
            # must not reach the sidecar as a complete run (§20).
            stop_reason = "parser_found_nothing"
        elif args.mode != "product":
            seen_keys.update(p.sku for p in first.products if p.sku is not None)

            # Planned from Montblanc's OWN result count rather than chased
            # through next-links: the site prints it above the grid on page
            # 1, and its own sort links publish the `start`/`sz` convention
            # this rebuilds. Mirrors playwright_scraper._plan_page_urls.
            page_one = first.final_url or target
            wanted = page_flow.pages_to_plan(args.pages, first.pages_available)
            if wanted < args.pages:
                logger.info("Montblanc reports %s page(s) for this listing; "
                            "%d were asked for. Fetching %d.",
                            first.pages_available, args.pages, wanted)
                stop_reason = "page_cap_reached"
            planned = [page_url(page_one, n, args.page_size, args.sort)
                       for n in range(2, wanted + 1)]

            for index, url in enumerate(planned):
                page_num = index + 2
                if pool and pool.rotates_per_page():
                    pool.advance(f"per-page rotation, page {page_num}")
                    session.relaunch()

                outcome = _fetch_one_page(session, args, pool, page_num, url)
                outcomes.append(outcome)
                if not outcome.ok:
                    stop_reason = ("page_load_timeout" if outcome.load_failed
                                   else f"blocked_{outcome.blocked_by}")
                    blocked = outcome.blocked_by is not None
                    break

                fresh_count = sum(1 for p in outcome.products
                                  if p.sku is None or p.sku not in seen_keys)
                seen_keys.update(p.sku for p in outcome.products
                                 if p.sku is not None)
                if not fresh_count:
                    logger.info("Page %d added no rows not already seen — "
                                "treating that as the end of the listing.",
                                page_num)
                    stop_reason = "no_new_products"
                    break

                if index + 1 < len(planned):
                    time.sleep(args.delay)
    finally:
        if session is not None:
            session.close()

    all_rows = []
    merged_seen = set()
    for oc in sorted(outcomes, key=lambda o: o.page_num):
        fresh = dedupe_by_key(oc.products, merged_seen, key=dedupe_key)
        if len(fresh) < len(oc.products):
            logger.info("Page %d: dropped %d duplicate row(s).",
                        oc.page_num, len(oc.products) - len(fresh))
        all_rows.extend(fresh)

    # Completeness, checked over the MERGED result rather than per page — a
    # per-page check cannot see a gap BETWEEN two pages, which is exactly
    # where a short page hides.
    #
    # NOT "pages x rows-per-page" as a hard expectation, even though the
    # page size is fixed at 15: the LAST page of a listing is legitimately
    # short. Mirrors playwright_scraper.
    total_available = next((o.total_available for o in outcomes
                            if o.total_available is not None), None)
    pages_available = next((o.pages_available for o in outcomes
                            if o.pages_available is not None), None)
    if args.mode != "product" and all_rows:
        counts = [(o.page_num, len(o.products)) for o in outcomes if o.ok]
        fullest = max((n for _, n in counts), default=0)
        thin = [(p, n) for p, n in counts
                if fullest and n < THIN_PAGE_SHARE * fullest]
        last_page = max((p for p, _ in counts), default=0)
        thin = [(p, n) for p, n in thin if p != last_page]
        if thin:
            logger.warning(
                "Page(s) %s came back much thinner than the fullest page "
                "(%d rows): %s. Montblanc serves a FIXED 15 per page, so a short "
                "page that is not the last one is a truncated response.",
                ", ".join(str(p) for p, _ in thin), fullest,
                ", ".join("page %d: %d" % (p, n) for p, n in thin))
        if total_available:
            logger.info("Montblanc reports %d product(s) for this listing; this run "
                        "holds %d (%.1f%%).", total_available, len(all_rows),
                        100.0 * len(all_rows) / total_available)

    ok_pages = [o for o in outcomes if o.ok]
    failed_pages = [o.page_num for o in outcomes if not o.ok]
    final_url = (max(ok_pages, key=lambda o: o.page_num).final_url
                 if ok_pages else args.url)

    # One-per-run context, in the sidecar rather than repeated down a column.
    # Byte-identical in shape to the other two engines: Montblanc's own arithmetic,
    # which is what lets a consumer tell a complete-but-capped run from one
    # that covered the whole result set.
    extra = None
    if args.mode != "product":
        sorts_applied = sorted({o.sort_applied for o in outcomes
                                if o.sort_applied})
        extra = {"total_results": total_available,
                 "pages_available": pages_available,
                 "page_size": args.page_size,
                 "locale": locale_from_url(final_url or args.url),
                 "sort_requested": args.sort,
                 # What a visitor with no --sort would have got. NOT "what
                 # the site applied": Montblanc does not publish that.
                 "site_default_sort": sorts_applied[0] if len(sorts_applied) == 1
                                      else sorts_applied}
        # No `capped_by_site` / `reachable_max`: Montblanc imposes no page
        # cap (measured — 280 results, 240 + 24 + 16 = 280), so
        # `pages_available` already says everything they would, and
        # pages x page_size would OVERSTATE a short last page.

    return finish_run(all_rows, args.out, args.format, args.allow_empty,
                      blocked=blocked, stop_reason=stop_reason,
                      pages_requested=args.pages, pages_completed=len(ok_pages),
                      pages_failed=failed_pages, mode=args.mode,
                      source=SOURCE_DEFAULT,
                      start_url=args.url, final_url=final_url,
                      extra=extra)


def parse_args():
    p = argparse.ArgumentParser(
        description="Montblanc scraper (Playwright edition)")
    p.add_argument("--url", default=None,
                   help="A montblanc.com URL: a category listing "
                        "(/{locale}/bags/backpacks), a search "
                        "(/{locale}/search?q=…) or one product page "
                        "(/{locale}/{slug}-MB{id}.html) with --mode product. "
                        "Montblanc serves all 72 markets from ONE host with "
                        "the locale in the PATH, so there is no per-country "
                        "hostname. Optional: --query or --category build the "
                        "URL instead. Also read from MONTBLANC_URL in the "
                        "environment or in .env.")
    p.add_argument("--query", default=None, metavar="TEXT",
                   help="What to search for, e.g. 'fountain pen'. Builds a "
                        "/{locale}/search URL together with --locale, so a "
                        "run needs no hand-assembled URL. Ignored when --url "
                        "is given.")
    p.add_argument("--locale", default=None, metavar="LOCALE",
                   help="Which market to read, as Montblanc spells it in its "
                        "own paths: en-us, de-de, ja-jp, fr-fr (default "
                        "%s). This is NOT cosmetic and it is not the browser's "
                        "locale: it selects the storefront, and Montblanc SETS "
                        "its prices per market rather than converting them — "
                        "one backpack was EUR 2000 on en-fi, EUR 1900 on "
                        "de-de, GBP 1700 on en-gb and USD 1990 on en-us "
                        "(2026-09-17). REFUSED together with --url, because a "
                        "URL already carries its locale and a flag that "
                        "disagreed with it would silently read a different "
                        "market than the address names."
                        % DEFAULT_LOCALE)
    p.add_argument("--sort", choices=sorted(SORTS), default=DEFAULT_SORT,
                   help="Which ordering to ask Montblanc for (default "
                        "%(default)s). This is not cosmetic: it decides WHICH "
                        "products are in the file. Measured 2026-09-17 on "
                        "/en-fi/writing-instruments — page 1 under "
                        "'recommended' (the site's own default, a 30-day "
                        "sales-velocity rule) and page 1 under 'price-asc' "
                        "shared 0 of 24 products. price-asc is also the only "
                        "ordering that is STABLE between runs, which is what "
                        "a multi-page run and a price diff both need. Pass "
                        "'recommended' to reproduce what a visitor sees. The "
                        "site's own answer is read back from the page and "
                        "recorded on every row, so an ordering it ignored "
                        "shows up rather than being assumed.")
    p.add_argument("--page-size", type=int, default=PAGE_SIZE, metavar="N",
                   help="Products per page (default %(default)s, which is "
                        "Montblanc's own default and the value its sort links "
                        "are built with). Larger values are honoured — sz=48 "
                        "and sz=96 both work — at the cost of looking less "
                        "like a visitor.")
    p.add_argument("--mode", choices=["category", "search", "product"],
                   default="category",
                   help="category (default): a category listing page. "
                        "search: /{locale}/search?q=… — the same markup, "
                        "selected by keyword instead of by path. product: one "
                        "product page, which emits ONE ROW PER VARIANT out of "
                        "the page's ProductGroup — sixteen nib widths of a "
                        "Meisterstuck are sixteen rows, each with its own sku "
                        "and price, which is the question a detail page "
                        "exists to answer. --pages applies to the two listing "
                        "modes; there is one page to read in product mode.")
    p.add_argument("--category", default=None,
                   help="Either a category PATH to build a URL from "
                        "('bags/backpacks'), used when no --url is given, or "
                        "a label to tag output rows with. Rows otherwise "
                        "carry Montblanc's own `item_category` ('Fountain "
                        "Pens'), populated on 97%% of tiles measured, so the "
                        "column is filled without the flag.\n"
                        "NOTE the path is LOCALE-SPECIFIC and cannot be "
                        "ported between markets: /en-fi/bags/backpacks is a "
                        "real page and /de-de/bags/backpacks is an HTTP 404, "
                        "because German addresses it as /de-de/lederwaren/… "
                        "and Japanese percent-encodes its slugs.")
    p.add_argument("--pages", type=int, default=1,
                   help="Number of listing pages to fetch. Applies to --mode "
                        "category and --mode search; ignored in --mode "
                        "product. Page 1 prints the catalogue's own result "
                        "count, so a run PLANS against the site's arithmetic "
                        "rather than walking off the end. There is no page "
                        "cap on this site — asking past the last page is a "
                        "served, empty grid rather than an error — so a "
                        "request is limited only by what the category holds, "
                        "and the sidecar records both numbers.")
    p.add_argument("--delay", type=float, default=2.0, help="Delay between pages, seconds")
    p.add_argument("--concurrency", type=int, default=1, metavar="N",
                   help="Fetch pages through N parallel workers (default 1 — "
                        "unchanged sequential behaviour). Each worker runs its "
                        "own browser and holds its own proxy exit, so N>1 "
                        "without --proxy-file just sends N times the traffic "
                        "from one address. Ignored with --cdp-endpoint.")
    p.add_argument("--retries", type=int, default=3,
                   help="Attempts per page load before giving up (default 3). "
                        "The pause between attempts doubles each time. A page "
                        "that comes back EMPTY is not retried — see "
                        "page_flow.STATE_POLICY — because a hub page with no "
                        "products on it is a correct answer, not a fault.")
    p.add_argument("--retry-delay", type=float, default=2.0,
                   help="Seconds before the first page-load retry, doubling "
                        "thereafter (default 2.0)")
    p.add_argument("--format", choices=["json", "csv", "both"], default="both")
    p.add_argument("--out", default="montblanc_products", help="Output file prefix")
    p.add_argument("--proxy", default=None,
                   help="Proxy URL, e.g. http://ACCOUNT:PASSWORD@HOST:9999 "
                        "(2captcha.com/proxy)")
    p.add_argument("--proxy-file", default=None,
                   help="File with one proxy URL per line (# comments and blank "
                        "lines skipped) to rotate across. Wins over --proxy.")
    p.add_argument("--proxy-rotate", choices=list(ROTATE_MODES), default="per-run",
                   help="per-run (default): one exit for the whole run. per-page: "
                        "a new exit for every page — this is what spreads volume, "
                        "and it relaunches the browser each time so the session "
                        "does not follow the IP around.")
    p.add_argument("--proxy-shuffle", action="store_true",
                   help="Shuffle the pool at startup, so concurrent runs do not "
                        "all begin on the first exit in the file.")
    p.add_argument("--proxy-block-retries", type=int, default=2,
                   help="When a page comes back refused or behind a captcha, "
                        "retry it from this many OTHER exits before giving up "
                        "(default 2). Needs a pool of more than one; ignored "
                        "otherwise. No refusal has been observed on this site "
                        "from an ordinary datacenter address, so this is "
                        "insurance rather than a setting most runs need.")
    p.add_argument("--twocaptcha-key", default=None, help="2captcha.com API key")
    p.add_argument("--allow-empty", action="store_true",
                   help="Write output files even when 0 rows were found. Off by "
                        "default so a failed run can't overwrite a good result "
                        "with an empty one; exit code is 4 either way.")
    p.add_argument("--fingerprint", action="store_true",
                   help="Fetch a browser fingerprint from 2captcha's Fingerprint "
                        "API and apply it to the launched browser. Needs "
                        "--twocaptcha-key. Ignored with --cdp-endpoint, where the "
                        "Scraping Browser supplies its own.")
    # ONE OS-family tag, not a list — and the default is what makes
    # --fingerprint work at all. It shipped as "Windows,Chrome,Desktop" in
    # this family, which the API rejects with HTTP 400 ("Request parameters
    # are invalid"), so --fingerprint failed on every invocation. Measured
    # 2026-09-10: `Windows` succeeds, and `Windows,Chrome,Desktop`, `Chrome`
    # and `Desktop` each 400. fingerprint_client.py's own --tags help has
    # said so all along; the engines' default contradicted it.
    p.add_argument("--fp-tags", default="Windows",
                   help="ONE OS-family tag for the fingerprint filter: "
                        "Windows, Microsoft Windows or Android. NOT a list — "
                        "Chrome, Desktop and Mobile are each rejected by the "
                        "API with 400, and no combination is accepted. Use "
                        "--fp-country to narrow further. (default: Windows)")
    p.add_argument("--fp-country", default=None,
                   help="Fingerprint country, ISO 3166-1 alpha-2. Match it to "
                        "your proxy's exit country — a US fingerprint on a "
                        "German IP is a contradiction.")
    p.add_argument("--captcha-api", choices=["v2", "v1"], default="v2",
                   help="Which 2captcha solver API to use. v2 is the current "
                        "JSON API (api.2captcha.com/createTask); v1 is the "
                        "legacy in.php/res.php pair. Applies to both the image "
                        "captcha and reCAPTCHA.")
    p.add_argument("--solve-captcha", choices=["when-blocked", "always"],
                   default="when-blocked",
                   help="when-blocked (default): only pay to solve a "
                        "reCAPTCHA if the content is not already readable. "
                        "always: solve whenever one is detected. NO challenge "
                        "of any kind has been observed on this site — zero "
                        "reCAPTCHA, Turnstile, DataDome or PerimeterX markers "
                        "across every capture — so this path is wired up "
                        "because a bot manager can be switched on between "
                        "deploys, not because one is in the way today. It "
                        "also cannot touch the edge's own refusal, which "
                        "resets the connection rather than serving a page.")
    p.add_argument("--min-score", type=float, default=0.7,
                   help="reCAPTCHA v3 minimum score to request (0.3, 0.7 or 0.9 "
                        "— the API only accepts these three). Ignored for v2 "
                        "widgets.")
    p.add_argument("--cdp-endpoint", default=None,
                   help="Connect to an already-running browser over CDP instead "
                        "of launching Playwright's bundled Chromium, e.g. "
                        "ws://user:pass@host:port — the Scraping Browser API "
                        "endpoint, or any browser that exposes a CDP URL. "
                        "--proxy and --headless/--headful are ignored when this "
                        "is set.")
    p.add_argument("--dump-html", default=None, metavar="PATH",
                   help="Save the exact HTML the parser is given, on success as "
                        "well as failure. Useful when the row count is right but "
                        "a column comes back empty — see the README's \"Traps that look like bugs\".")
    p.add_argument("--headless", action="store_true", default=True)
    p.add_argument("--headful", dest="headless", action="store_false")
    args = p.parse_args()
    # Fill --twocaptcha-key / --cdp-endpoint / --proxy / --url from the
    # environment or .env when the flag was not given. An explicit flag wins.
    env_config.apply(args)

    # --url and the query flags are two ways to say the same thing, and only
    # one of them may win. Refused rather than merged: a --locale that
    # disagreed with the locale already in a URL would silently read a
    # different MARKET than the address names — and on this site that is a
    # different price, not just a different language. This is the family's
    # --country ban (CLAUDE.md §10) applied where it actually bites: there is
    # one host for all 72 markets, so the flag cannot contradict a HOSTNAME,
    # only a path segment, and this is where that is caught.
    if args.url and args.locale:
        p.error("--url already carries its locale (%r) and --locale would "
                "have to agree with it; nothing here checks that they do, and "
                "on this site disagreeing means a different price. Pass "
                "either a URL or --locale, not both."
                % (locale_from_url(args.url) or "none"))
    if args.url and args.query:
        p.error("--url already names what to fetch; --query would have to "
                "agree with it. Pass either a URL or --query, not both.")

    locale = args.locale or DEFAULT_LOCALE
    if not locale_is_known(locale):
        # A WARNING, not an error. The captured locale set omits whichever
        # market the capture itself was served in, so it is incomplete by
        # exactly the entry you are most likely to be using — an allowlist
        # here refused `en-fi`, the locale this site geo-redirects a Finnish
        # visitor to. See product_parser.KNOWN_LOCALES.
        logger.warning("%r was not in the captured country-selector set. That "
                       "set is known to omit the locale it was captured from, "
                       "so this may well be real — continuing. If the market "
                       "does not exist, Montblanc answers HTTP 404 and the "
                       "run will report that rather than guessing.", locale)

    if not args.url and args.query:
        if args.mode == "product":
            p.error("--mode product needs a --url: a product is one page at "
                    "one address, and --query describes a search.")
        args.mode = "search"
        args.url = search_url(args.query, locale, sort=args.sort,
                              page_size=args.page_size)
        logger.info("Built the search URL from --query: %s", args.url)
    elif not args.url and args.category:
        # `--category` is doing double duty — a path to build a URL from when
        # there is no --url, and a label to tag rows with when there is. That
        # is the family's flag and this is the reading that makes it useful
        # on a site whose category paths ARE the addresses.
        args.url = category_url(args.category, locale, sort=args.sort,
                                page_size=args.page_size)
        logger.info("Built the listing URL from --category: %s", args.url)

    if not args.url:
        p.error("no --url given and no --query/--category: pass a "
                "montblanc.com URL, or --query 'fountain pen', or --category "
                "'bags/backpacks'. MONTBLANC_URL in the environment or in "
                ".env works too.")

    supported, why = is_supported_url(args.url)
    if not supported:
        # Refused rather than attempted. This parser reads Montblanc's own
        # JSON-LD and its `-MB{id}.html` URL shape; pointing it at another
        # site would not fail loudly, it would return zero rows and look like
        # an empty result (§8). The REASON is given, because "is not a
        # Montblanc site" about a host that plainly is one sends the reader
        # hunting a typo they did not make.
        p.error(f"{args.url!r} {why}.")

    if args.mode == "product" and not is_product_url(args.url):
        p.error(f"--mode product expects a product URL ending in "
                f"-MB{{id}}.html; {args.url!r} is not one. A category "
                f"listing is --mode category.")
    if args.mode != "product" and is_product_url(args.url):
        p.error(f"{args.url!r} is a single product page. Use --mode product "
                f"for it — which reads its whole variant table — or pass a "
                f"category or /search URL.")

    if args.mode == "product" and args.pages != 1:
        # Said out loud rather than silently ignored: a user who passed
        # --pages 5 expects five pages of something.
        logger.warning("--pages %d is ignored in --mode product: there is one "
                       "page to read. It still emits one row per variant, so "
                       "the output is not one row. The run status will say "
                       "single_page_mode.", args.pages)
        args.pages = 1
    if args.mode == "product" and args.sort != DEFAULT_SORT:
        logger.warning("--sort is ignored in --mode product: one product's "
                       "variant table has no ordering to ask for.")
    if args.page_size < 1:
        p.error("--page-size must be at least 1")
    return args


if __name__ == "__main__":
    args = parse_args()
    if args.fingerprint and not args.twocaptcha_key:
        logger.error("--fingerprint needs --twocaptcha-key.")
        sys.exit(2)
    if args.fingerprint and args.cdp_endpoint:
        logger.warning("--fingerprint is ignored with --cdp-endpoint: the "
                       "remote browser supplies its own.")
    try:
        sys.exit(scrape(args))
    except ProxyError as e:
        logger.error("%s", e)
        sys.exit(2)
    except Exception as e:
        # A remote browser that will not accept the connection is a REMOTE
        # API failure (exit 5), not a crash in this code (exit 1) and not bad
        # usage (exit 2). The distinction earns its keep on the commonest
        # one: `profile_locked` means another run still holds this `pid`, and
        # a harness that sees exit 1 goes looking for a bug in the scraper
        # instead of waiting or passing a different pid.
        text = _mask_credentials(str(e))
        if "profile_locked" in text or "connect to --cdp-endpoint" in text:
            logger.error("%s", text)
            sys.exit(EXIT_API_ERROR)
        raise

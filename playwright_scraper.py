#!/usr/bin/env python3
"""
montblanc-scraper — Playwright edition (primary engine)
=======================================================

Scrapes montblanc.com: category listings, keyword searches, and product
pages with their full variant table.

    --mode category   (default)  /{locale}/{category-path}?start=N&sz=24
    --mode search                /{locale}/search?q=…&start=N&sz=24
    --mode product               one /{locale}/{slug}-MB{id}.html page,
                                 emitted as ONE ROW PER VARIANT — sixteen
                                 nib widths of a Meisterstück are sixteen
                                 rows, each with its own sku and price

Three engines ship in this repo and they must agree on exit codes, run
status, and whether a run crashes or spends money; the shared decisions live
in output_writer.finish_run() and page_flow.py so they cannot drift apart.

What is different about Montblanc
----------------------------------
* **Nothing here is gated, from a datacenter address.** Measured 2026-09-17
  from a Hetzner IP in Helsinki: listings, search and product pages all
  answer HTTP 200 with a plain browser User-Agent, no key, no proxy, no
  challenge of any kind on any capture. Say so plainly rather than selling a
  product nobody needs here (§13). What the paid products buy on this site
  is a SPECIFIC MARKET — see the locale note below — and volume from many
  addresses, not access.
* **The refusal is not a page and not a status code.** The edge answers a
  request whose User-Agent names an HTTP client library by killing the
  connection: `curl` sees an HTTP/2 INTERNAL_ERROR, `requests` sees a
  ReadTimeout. There is no body to match a marker against. The browser
  engines never meet it, because they send a browser's own UA — see
  page_flow.classify_transport_error, which is where that lives so all three
  engines name it identically.
* **`akamai` is not a block marker on this site.** Montblanc is Akamai-
  fronted and its own performance script references `akamaihd.net` on every
  page it serves. Counted before writing the marker set (§18), which is the
  only reason it is not in it. Read product_parser's marker comment before
  adding anything.
* **A product page publishes `ProductGroup`, not `Product`.** The listing
  parser finds nothing on one — and then its tile fallback would happily
  return the "you may also like" carousel as eighteen products, measured.
  `parse_products` refuses a detail page on the page's own evidence for that
  reason, and `--mode product` is a separate entry point (§20).
* **Pagination is `start`/`sz` on the listing URL itself**, verified against
  the site's own next-link rather than assumed. Page 1 prints the catalogue's
  own `result-count`, so pages are PLANNED from the site's arithmetic (§7
  layer 2). Unlike bbb-scraper there is no page cap: a run that walks a
  category to the end holds the whole category.
* **The default ordering is a merchandised one.** Montblanc's own default is
  a 30-day sales-velocity recommendation, and page 1 under it shares **0 of
  24** products with page 1 under price-ascending. `--sort` defaults to
  `price-asc` here for that reason — this repo's one deliberate disagreement
  with the site — and `--sort recommended` reproduces what a visitor sees.
  The ordering is recorded on every ROW, because it decides which products
  are in the file rather than how they are arranged.
* **The market is in the path, and the price is set per market.** One
  backpack, 2026-09-17: EUR 2000 on en-fi, EUR 1900 on de-de, GBP 1700 on
  en-gb, USD 1990 on en-us. Two of those share a currency and still
  disagree, so a cross-market comparison joins on `sku` and reads `locale`.
  Category PATHS are localised too — `/de-de/bags/backpacks` is an HTTP 404
  — so a URL cannot be ported between markets by swapping the segment.

Usage
-----
    python playwright_scraper.py \\
        --url "https://www.montblanc.com/en-us/writing-instruments" \\
        --pages 3 --format both

    python playwright_scraper.py --mode search --query "fountain pen" \\
        --locale en-gb --pages 2

    python playwright_scraper.py --mode product \\
        --url "https://www.montblanc.com/en-fi/meisterstuck-gold-coated-legrand-fountain-pen-MB132460.html"

Requires: pip install -r requirements.txt -r requirements-playwright.txt
          then: playwright install chromium   (only if NOT using --cdp-endpoint)
"""

import argparse
import logging
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse, urljoin

from playwright.sync_api import (sync_playwright, Error as PWError,
                                 TimeoutError as PWTimeout)

from captcha_solver import (detect_recaptcha_v3, detect_recaptcha_in_page,
                            reconcile_detections, solve_recaptcha,
                            CaptchaUnsolvable, INJECT_TOKEN_JS,
                            RECAPTCHA_DISCOVERY_JS)
from product_parser import (DEFAULT_LOCALE, DEFAULT_SORT, PAGE_SIZE, SORTS,
                            category_from_url, locale_is_known,
                            category_url, detect_bot_challenge,
                            is_product_url, is_supported_url, locale_from_url,
                            page_url, pages_available, parse_listing,
                            parse_product_detail, parse_products,
                            product_link_count, references_own_assets, search_url,
                            total_results)
from output_writer import (dedupe_by_key, finish_run, EXIT_API_ERROR,
                           SOURCE_DEFAULT)
import page_flow
from page_flow import MIN_CARD_MATCHES
from proxy_pool import (from_args as proxy_pool_from_args, to_playwright, mask,
                        ROTATE_MODES, ProxyError, ProxyPool)
import env_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("playwright_scraper")


def _chrome_ua(chromium_version: str) -> str:
    """Build a desktop-Chrome UA naming the browser's OWN real version.

    Not a hardcoded version number: that drifts the moment a newer Chromium
    ships, and a UA claiming an older Chrome than what the JS engine, WebGL
    strings and TLS ClientHello all actually report is itself a mismatch a
    fingerprinter can key on.
    """
    return (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{chromium_version} Safari/537.36")


@dataclass
class PageOutcome:
    """What one page produced.

    Collected per page and merged afterwards rather than folded into shared
    state as the loop goes. Two reasons, and the second is the point:
    dedupe that mutates a running set inside the loop makes the OUTPUT depend
    on the order pages happen to arrive in — fine while that order is fixed,
    wrong the moment pages are fetched concurrently, because which page
    "claims" a duplicate sku (and so which `scraped_at` the row carries)
    would vary between runs of the same command. Merging afterwards in page
    order is deterministic regardless of arrival order.
    """
    page_num: int
    url: str
    final_url: Optional[str] = None
    products: List = field(default_factory=list)
    blocked_by: Optional[str] = None
    load_failed: bool = False
    # The page_flow state this page came back as ("content", "blocked",
    # "captcha", "empty", "unknown"). Carried so the caller can tell an
    # EMPTY page — a discover/hub page, a search that matched nothing, or one
    # `start=` past the end of a listing — from a page that FAILED. Both
    # produce zero rows and they mean opposite things.
    state: Optional[str] = None
    # What Montblanc itself said the result set was: the `result-count`
    # printed above the grid, and the page count that follows from it at the
    # run's page size. These are the site's own arithmetic and they are what
    # pages are planned from.
    #
    # They are populated from PAGE 1 ONLY, because that is the only page that
    # prints the count — a `start=24` slice does not repeat it. A later page
    # therefore reports None, and None means UNKNOWN rather than zero.
    total_available: Optional[int] = None
    pages_available: Optional[int] = None
    # The CATEGORY'S OWN DEFAULT ordering, read off the page — not the
    # ordering that was applied, which Montblanc does not publish anywhere
    # (see product_parser.default_sort_rule for the measurement that settled
    # that). Recorded so a reader can see what a run without --sort would
    # have got, since the ordering decides WHICH products are in the file
    # (0 of 24 in common between two orderings of one category) rather than
    # merely their order.
    sort_applied: Optional[str] = None

    @property
    def ok(self) -> bool:
        return not self.load_failed and self.blocked_by is None


ITEM_LINK_SELECTOR = page_flow.READY_SELECTOR_LISTING

# The share of rows that must carry the columns Montblanc populates on every
# listing row, below which the read has broken rather than the data being
# unusual.
#
# Measured over 192 listing rows on four categories (2026-09-17): `title`,
# `url`, `sku`, `currency` and `image_url` were populated on 192 of 192.
#
# Deliberately NOT in this check: `price`, which is genuinely absent from the
# JSON-LD on 14 of those 192 rows (the range-priced variant groups, recovered
# from the DOM instead), and `size`, which is a watch/belt attribute present
# on 12%. A floor on either would fire on healthy data.
CORE_FIELD_FLOOR = 99
CORE_FIELDS = ("title", "url", "sku", "currency")

# `image_url` is REPORTED but deliberately NOT floored, and that is a
# measurement rather than a shrug. Montblanc's ItemList can name a product
# whose grid tile the page does not render at all, and such an entry carries
# `"image": null` and `"brand": null` in the JSON-LD too — so there is
# nothing anywhere on the page to read. Measured on a live 4-page run:
# 1 row of 92 (MB127852M), which is under a 99% floor and is CORRECT DATA.
# A floor that fires on healthy pages teaches the reader to ignore floors.

# The share of rows that must carry a usable price before the run is worth
# trusting as a PRICE run rather than merely as a catalogue listing. Both
# `jsonld` and `dom_range` count — a range is a real, labelled number — so
# this fires on a parsing break, not on the site's own variety.
PRICE_COVERAGE_FLOOR = 95

# A page holding less than this share of the page size is reported as thin.
# The size is the site's own `sz`, echoed back as `data-page-size`, and every
# captured full page held exactly it — so the only legitimately short page is
# the last one of a listing, which is why this can sit high without false
# alarms.
THIN_PAGE_SHARE = 0.6


# ---------------------------------------------------------------------------
# page_flow, bound to Playwright
# ---------------------------------------------------------------------------
# Every decision about WHAT to do with a page — how long to wait, when to
# scroll, when a fresh session is the only fix — lives in page_flow.py so all
# three engines make it identically. What lives here is only HOW to ask this
# particular driver. See page_flow's docstring for why that split exists.
def _driver(page):
    # Named OPERATIONS rather than JavaScript, and that is the point of the
    # split. Selenium's execute_script takes a function BODY with an explicit
    # `return` while Playwright and pyppeteer take `() => expr`, so a shared
    # module handing JS across this boundary would quietly acquire one
    # driver's dialect.
    #
    # There is no scroll primitive here, and its absence is measured rather
    # than forgotten (§8: missing content is one of three things, so check
    # which before "fixing" it). Montblanc SERVER-RENDERS its grid: a plain
    # HTTP fetch with no JavaScript at all returns the full JSON-LD ItemList
    # of 24 products and 49 `data-pid` attributes. Everything the parser
    # wants is in the document before any scrolling could happen, on every
    # capture, so a scroll would be ceremony that looks load-bearing.
    return {
        "count": lambda selector: len(page.query_selector_all(selector)),
        "sleep": page.wait_for_timeout,
        "content": lambda: _content_when_settled(page),
        "current_url": lambda: page.url,
    }


def _ready_selector(args) -> str:
    return page_flow.ready_selector(args.mode)


def _min_matches(args) -> int:
    return page_flow.min_matches(args.mode)


def _classify(page, html: str, status=None) -> str:
    return page_flow.classify(html, status, page.url)

# Every readiness constant and every state policy lives in page_flow.py, with
# its measurement beside it. Nothing about WHAT to do with a page is
# duplicated here — this file only knows HOW to ask Playwright.


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


def _plan_page_urls(args, page_one_url: str,
                    pages_avail: Optional[int]) -> List[str]:
    """URLs for pages 2..N, decided once from what page 1 reported.

    On most sites in this family this function has to hedge: it compares the
    site's own next-link against what the URL convention would build, and
    falls back to chaining link-to-link when the two disagree, because a
    constructed URL the site does not honour produces a complete-looking run
    holding page 1.

    Montblanc needs none of that hedging, and the reason was verified rather
    than assumed (§7). Its own sort links are built as
    `…?srule=…&start=0&sz=24`, so the convention is not guessed from the
    shape of page 1's URL — it is the convention the site itself publishes.
    Fetching `?start=24&sz=24` returned a full page whose ItemList held item
    25, which is the check §7 demands before trusting a constructed URL.

    The plan is CAPPED by the site's own `result-count`, printed above the
    grid on page 1. Overshooting is cheap here — `start=288` on a
    280-product category is HTTP 200 with an empty grid, not the HTTP 500 the
    same overshoot produces on bbb-scraper's site — but planning against the stated count
    still saves the wasted fetch and keeps the sidecar honest.
    """
    wanted = page_flow.pages_to_plan(args.pages, pages_avail)
    if wanted < args.pages:
        logger.info("Montblanc reports %s page(s) of %d for this listing; %d "
                    "were asked for. Planning %d — the pages past the end are "
                    "served, they are just empty.",
                    pages_avail, args.page_size, args.pages, wanted)
    return [page_url(page_one_url, n, args.page_size, args.sort)
            for n in range(2, wanted + 1)]


# Chromium's own names for "the proxy is the problem, not the site". Matched
# on the error text because Playwright surfaces them as a generic Error.
_PROXY_ERROR_MARKERS = (
    "ERR_PROXY_CONNECTION_FAILED",     # nothing listening / refused
    "ERR_TUNNEL_CONNECTION_FAILED",    # CONNECT rejected by the proxy
    "ERR_PROXY_AUTH_UNSUPPORTED",      # auth scheme we cannot satisfy
    "ERR_PROXY_AUTH_REQUESTED",        # credentials missing or wrong
    "ERR_UNEXPECTED_PROXY_AUTH",
    "ERR_PROXY_CERTIFICATE_INVALID",
)


def _proxy_failure(exc) -> str:
    """The Chromium proxy-error name in `exc`, or "" if it is not one.

    Distinguishing this from an ordinary timeout matters because the two want
    opposite responses: a timeout deserves a retry from the same exit, while
    an unusable exit deserves a different exit — retrying it unchanged just
    spends the retry budget on a proxy that is not going to answer.
    """
    text = str(exc)
    for marker in _PROXY_ERROR_MARKERS:
        if marker in text:
            return marker
    return ""


def _launch_local(pw, args, pool):
    """Launch our own Chromium on `pool`'s current exit; return (browser, context, page).

    Factored out of scrape() so a proxy rotation can tear the whole browser
    down and call this again. Swapping the proxy under a live session would
    be cheaper and wrong: cookies a bot manager issued against one exit,
    replayed from another, are a stronger signal than either address alone.
    A rotation therefore means a genuinely fresh browser — new cookie jar,
    new storage — which is what an ordinary user on a different network
    looks like.
    """
    launch_kwargs = {"headless": args.headless}
    proxy = to_playwright(pool.current) if pool else None
    if proxy:
        launch_kwargs["proxy"] = proxy
        logger.info("Using proxy exit %s", mask(pool.current))

    browser = pw.chromium.launch(**launch_kwargs)
    # Only override the UA when we launched our own bundled Chromium.
    # Forcing a UA on a page reached via --cdp-endpoint mismatches the remote
    # browser's real TLS/JS fingerprint on purpose-matched values.
    ctx_kwargs = {"user_agent": _chrome_ua(browser.version), "locale": args.locale}
    init_script = None
    if args.fingerprint:
        # Only meaningful on this branch. Over --cdp-endpoint the Scraping
        # Browser already has its own fingerprint, and layering a second one
        # on top produces a mismatch rather than better cover.
        from fingerprint_client import (get_fingerprint,
                                        playwright_context_kwargs,
                                        playwright_init_script)
        fp = get_fingerprint(args.twocaptcha_key,
                             tags=args.fp_tags, country=args.fp_country)
        ctx_kwargs.update(playwright_context_kwargs(fp))
        init_script = playwright_init_script(fp)
        logger.info("Using 2captcha fingerprint %s (%s)", fp.get("id"), fp.get("country"))

    context = browser.new_context(**ctx_kwargs)
    if init_script:
        # Must be installed on the context, before any page script runs.
        context.add_init_script(init_script)
    return browser, context, context.new_page()


class _BrowserSession:
    """One browser + context + page, relaunchable onto a different exit.

    Exists because a rotation replaces all three handles at once, and passing
    three mutable locals through every helper is how one of them ends up
    stale. It also gives a worker thread a single object to own: with
    Playwright's sync API, a browser and everything reachable from it belong
    to the thread that created them, so each worker builds its own.
    """

    def __init__(self, pw, args, pool, remote: bool = False):
        self.pw, self.args, self.pool, self.remote = pw, args, pool, remote
        self.browser = self.context = self.page = None

    def open(self):
        if self.remote:
            self.browser, self.context, self.page = _connect_remote(self.pw, self.args)
        else:
            self.browser, self.context, self.page = _launch_local(
                self.pw, self.args, self.pool)
        return self

    def relaunch(self):
        """Tear the browser down and come back on the pool's current exit.

        On a remote browser this is a no-op — its exit is not ours to change.
        """
        if self.remote:
            return
        try:
            self.browser.close()
        except Exception as e:  # noqa: BLE001 — teardown must not mask the reason we're here
            logger.debug("Ignoring error while closing browser for rotation: %s", e)
        self.open()

    def close(self):
        try:
            if self.remote:
                self.page.close()  # leave the remote browser app running
            else:
                self.browser.close()
        except Exception as e:  # noqa: BLE001
            logger.debug("Ignoring error during browser teardown: %s", e)


def _connect_remote(pw, args):
    """Attach to an already-running browser over CDP; return (browser, context, page)."""
    logger.info("Connecting to existing browser over CDP: %s",
                _mask_credentials(args.cdp_endpoint))
    # Explicit timeout. Playwright defaults to 30s here, but stating it makes
    # the contract visible next to the pyppeteer twin, which has no connect
    # timeout at all. A Scraping Browser session that is still held answers
    # with HTTP 500 rather than stalling, so this mostly guards against the
    # endpoint going quiet.
    try:
        browser = pw.chromium.connect_over_cdp(args.cdp_endpoint, timeout=30000)
    except (PWError, PWTimeout) as e:
        # Playwright puts the endpoint it tried into the exception text, and
        # the endpoint is a URL with the password in it. Unmasked, that
        # password lands in the terminal, in CI output and in any log the run
        # is piped to — which is the one thing this project promises does not
        # happen ("credentials never reach argv or logs"). The message is
        # rewritten with the credentials masked and the host and port kept,
        # because WHICH endpoint failed is the useful half and is not the
        # secret.
        raise PWError(
            f"could not connect to --cdp-endpoint "
            f"{_mask_credentials(args.cdp_endpoint)}: "
            f"{_mask_credentials(str(e))}\n"
            f"A Scraping Browser profile allows ONE live connection at a "
            f"time, so a 500 here usually means another run still holds this "
            f"`pid`. Wait for it to finish, or use a different pid."
        ) from None
    # Reuse the remote browser's existing context so its
    # fingerprint/session/proxy settings stay intact.
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()

    # The Scraping Browser API exposes a documented CDP domain
    # (`Captcha.setAutoSolve` / `Captcha.solve`) that clears supported
    # challenges inside the browser: https://2captcha.com/scraper/browser-api/api
    # Tried first when --cdp-endpoint is set; this script's own detect+solve
    # logic still runs as a fallback if the endpoint does not support it.
    # Note it cannot cover Montblanc's own refusal, which is not a challenge
    # and not even a page: the edge resets the connection, so there is no
    # markup for any solver to look at and no widget to solve. See
    # page_flow.classify_transport_error. This path exists for a challenge
    # the site does not render today — zero reCAPTCHA, Turnstile, DataDome
    # and PerimeterX markers across every capture — because a bot manager
    # can be switched on between deploys.
    try:
        cdp_session = context.new_cdp_session(page)
        cdp_session.send("Captcha.setAutoSolve", {"autoSolve": True, "options": [{"type": "*"}]})
        cdp_session.on("Captcha.detected", lambda *_: logger.info("[Scraping Browser] CAPTCHA detected on page."))
        cdp_session.on("Captcha.waitForSolve", lambda *_: logger.info("[Scraping Browser] CAPTCHA sent to 2captcha for solving."))
        cdp_session.on("Captcha.solveFinished", lambda *_: logger.info("[Scraping Browser] CAPTCHA solved automatically."))
        cdp_session.on("Captcha.solveFailed", lambda *_: logger.warning("[Scraping Browser] CAPTCHA auto-solve failed."))
        logger.info("Scraping Browser API Captcha.setAutoSolve enabled — supported "
                    "challenge types will be solved automatically if this "
                    "--cdp-endpoint is a Scraping Browser API session.")
    except Exception as e:
        logger.info("Captcha.setAutoSolve not available on this --cdp-endpoint (%s) — "
                    "relying on this script's own detect+solve logic instead.", e)
    return browser, context, page


def _resolve_pagination_url(base_url: str, href: str) -> str:
    """Resolve a pagination link's raw href against the page it came from.

    Playwright's get_attribute("href") returns the raw HTML attribute,
    unresolved — unlike the DOM .href property Puppeteer/Selenium read for
    the same purpose in this project, which the browser resolves for you.
    urljoin handles every shape correctly — absolute, protocol-relative,
    absolute-path, and page-relative hrefs alike.
    """
    return urljoin(base_url, href)


# Every `scheme://user:pass@` in a string, however many times it occurs.
# Matching globally rather than once is the point: a Playwright connection
# error repeats the endpoint five times (the message plus a four-line call
# log), so a masker that handled only the first occurrence would print the
# password four times and look like it was working.
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


def _content_when_settled(page, attempts: int = 4, pause_ms: int = 700):
    """page.content() that tolerates a page mid-navigation.

    Playwright raises `Page.content: Unable to retrieve content because the
    page is navigating and changing the content` if the document swaps under
    it, and on this site that is a live risk rather than a theoretical one:
    **the bare host geo-redirects**. `https://www.montblanc.com/` answered
    from a Finnish exit lands on `/en-fi` (measured 2026-09-17), so the
    market a run reads follows the EXIT ADDRESS unless the URL names a
    locale — which is why every URL this engine builds carries one, and why
    a snapshot taken right after goto() can land exactly on the swap.

    Retries briefly and returns None if the page won't hold still, so the
    caller can skip a check instead of failing the run.
    """
    for attempt in range(1, attempts + 1):
        try:
            return page.content()
        except PWError as e:
            if "navigating" not in str(e).lower():
                raise
            if attempt == attempts:
                logger.warning("Page kept navigating through %d attempts — "
                               "continuing without a snapshot.", attempts)
                return None
            logger.info("Page is navigating (a URL canonicalisation?) — "
                        "retrying content() in %dms (%d/%d).",
                        pause_ms, attempt, attempts)
            page.wait_for_timeout(pause_ms)
    return None


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


def _snapshot(page, url: str) -> Optional[str]:
    """What the parser is given for this address.

    Two shapes, because the two addresses answer with two things and
    Chromium does not hand them over the same way. A PAGE is read with
    `content()`. The ENDPOINT answers with JSON, which Chromium wraps in its
    own JSON-viewer markup — so `content()` there returns the viewer's HTML
    and the payload would be unreachable. `document.body.innerText` gives
    back exactly what the server sent.

    Getting this wrong is silent: the viewer markup parses as "not a listing
    payload", which reads as an empty result rather than as a bug.
    """
    if _is_endpoint(url):
        try:
            return page.evaluate("() => document.body.innerText") or ""
        except (PWError, PWTimeout) as e:
            logger.warning("Could not read the endpoint response: %s", e)
            return None
    return _content_when_settled(page)


def handle_captcha_if_present(page, args) -> bool:
    """Detect and solve a challenge. True if something was solved.

    Runs after EVERY navigation, for ANY page — not scoped to one URL. The
    static-HTML and runtime reCAPTCHA detectors are run and reconciled
    against each other rather than short-circuited, because they can disagree
    about the variant and the parameters for one are rejected for the other.

    NOTE what this cannot help with. Montblanc's refusal is not a page at
    all — the edge resets the HTTP/2 stream and nothing arrives — so a
    2Captcha key does nothing about the state a blocked run is most likely to
    meet, and no solve is attempted or billed for it. NO challenge of any
    kind has been observed on this site: zero reCAPTCHA, hCaptcha, Turnstile,
    DataDome, PerimeterX, Incapsula, Kasada and AWS WAF markers across every
    capture, and no `data-sitekey` anywhere.

    That is a statement about what the SITE renders, not about what can be
    solved (§19: never write that a captcha cannot be solved — the only
    sentence anyone is entitled to is "this page carries no widget"). This
    path exists because a bot manager can be switched on between deploys, and
    because the family's rule is that detection stays broad:
    different geos and scenarios surface different challenges.
    """
    html = _content_when_settled(page)
    if html is None:
        # Couldn't get a stable snapshot — skip detection for this navigation
        # rather than taking the whole run down. The next navigation gets
        # another chance, and the parse below reads its own copy of the DOM.
        return False

    # Detected is not the same as blocking. A challenge on a page whose
    # products are already rendered guards nothing, and counting the anchors
    # is instant — no wait_for_function, no 20s — which is why this check
    # sits here rather than after the readiness wait. Doing it the other way
    # round would cost 20 wasted seconds on a page the captcha genuinely
    # gates, where solving FIRST is what makes the content appear.
    already_rendered = len(page.query_selector_all(_ready_selector(args)))
    when_blocked = getattr(args, "solve_captcha", "when-blocked") == "when-blocked"

    html_challenge = detect_recaptcha_v3(html, page.url)
    runtime_challenge = detect_recaptcha_in_page(
        lambda js: page.evaluate(js), page_url=page.url)
    challenge = reconcile_detections(html_challenge, runtime_challenge)
    if not challenge:
        return False

    if when_blocked and already_rendered > MIN_CARD_MATCHES:
        logger.info("%s detected via %s, but %d anchors are already on the "
                    "page — not solving it. Pass --solve-captcha always to "
                    "solve it anyway.", challenge.kind, challenge.source,
                    already_rendered)
        return False

    logger.warning("%s detected via %s (sitekey=%s, action=%s) — attempting to solve.",
                   challenge.kind, challenge.source, challenge.sitekey, challenge.action)
    if not args.twocaptcha_key:
        logger.warning("No 2captcha API key, so this challenge cannot be "
                       "solved — continuing with whatever the page already "
                       "holds.")
        return False
    try:
        token = solve_recaptcha(challenge, args.twocaptcha_key,
                               api_version=args.captcha_api,
                               min_score=args.min_score)
    except Exception as e:  # noqa: BLE001 — a solver failure is not a crash
        logger.error("Solving the challenge failed (%s) — continuing with "
                     "whatever the page holds.", e)
        return False

    page.evaluate(INJECT_TOKEN_JS, token)
    logger.info("Token injected. Reloading page to continue.")
    page.wait_for_timeout(1500)
    page.reload(wait_until="domcontentloaded", timeout=60000)
    return True


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


def _fetch_one_page(session, args, pool, page_num: int, url: str) -> PageOutcome:
    """Fetch and parse one page. Retries, rotations and debug dumps live here.

    Returns a PageOutcome and never raises for an EXPECTED failure — a
    timeout, a 403 refusal, a captcha page, a dead exit are all recorded on the
    outcome instead. What the run should do about them differs between the
    sequential and concurrent paths, so that decision belongs to the caller
    rather than to a raised exception unwinding through it.

    Always goes through `session.page`, never a captured local: a rotation
    replaces the browser, context and page together, and a stale handle is
    exactly the bug _BrowserSession exists to prevent.
    """
    outcome = PageOutcome(page_num=page_num, url=url)

    # How many times a blocked page may be retried.
    #
    # With a pool, each retry moves to a DIFFERENT exit and the budget is the
    # user's `--proxy-block-retries`. WITHOUT one — the ordinary case here,
    # because `--cdp-endpoint` brings its own exit — the retry re-fetches
    # through the same access path, and that is worth doing on this site
    # rather than giving up: a Scraping Browser profile was measured refusing
    # two requests and serving the third. Zero was the family default and it
    # made the first live run of this engine abandon page 1 on its first
    # block without retrying once.
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
    html, state, load_failed = None, "ok", False

    for block_attempt in range(block_retries + 1):
        logger.info("Fetching page %d/%d: %s", page_num, args.pages, url)
        # Retry a navigation timeout rather than ending the run on it. One
        # network flap on page 12 of 50 should not break the loop.
        load_failed, exit_failed = False, None
        for attempt in range(1, args.retries + 1):
            try:
                session.page.goto(url, wait_until="domcontentloaded", timeout=60000)
                load_failed = False
                break
            except (PWTimeout, PWError) as e:
                # A dead or misconfigured proxy raises PWError
                # (net::ERR_PROXY_CONNECTION_FAILED), not PWTimeout —
                # catching only the latter lets it escape as a traceback,
                # which is the likeliest failure the first time anyone points
                # --proxy-file at a real list.
                reason = _proxy_failure(e)
                if reason:
                    exit_failed = reason
                    load_failed = True
                    break  # a different exit is the only thing that helps
                load_failed = True
                if attempt < args.retries:
                    pause = args.retry_delay * (2 ** (attempt - 1))
                    logger.warning("Timeout loading %s (attempt %d/%d) — "
                                   "retrying in %.1fs.", url, attempt,
                                   args.retries, pause)
                    time.sleep(pause)

        if exit_failed and has_pool and block_attempt < block_retries:
            logger.warning("Exit %s is unusable (%s) — rotating to another "
                           "one (%d/%d).", mask(pool.current), exit_failed,
                           block_attempt + 1, block_retries)
            pool.advance(f"unusable exit: {exit_failed}")
            session.relaunch()
            continue
        if load_failed:
            break

        if handle_captcha_if_present(session.page, args):
            # A solve navigated the page. Give the destination a moment
            # before judging what came back.
            session.page.wait_for_timeout(1000)

        html = _snapshot(session.page, url) or ""
        state = _classify(session.page, html)

        # Montblanc server-renders its grid, so a listing is parseable in
        # the FIRST response and there is nothing to wait for on a healthy
        # page. Measured 2026-09-17 with no JavaScript executed at all — a
        # plain HTTP fetch — /en-fi/bags/backpacks returned the full JSON-LD
        # ItemList of 24 products and 49 `data-pid` attributes, and a product
        # page returned its whole 16-entry ProductGroup. That is why this
        # engine has no scroll step and no readiness pause on the happy path:
        # either would be ceremony that looks load-bearing.
        #
        # The wait below is therefore only for the state that says Montblanc
        # served SOMETHING that is not a catalogue page. That is the one case
        # where waiting can still help, and it is bounded.
        if state == "unknown":
            wait_timeout = page_flow.content_timeout_ms(args.mode)
            logger.info("Page %d is something Montblanc served (%d bytes, "
                        "its own assets referenced %d time(s)) but carries no "
                        "product data — waiting up to %.0fs rather than "
                        "spending a retry.", page_num, len(html),
                        references_own_assets(html), wait_timeout / 1000)
            found = page_flow.wait_for_count(
                lambda sel: len(session.page.query_selector_all(sel)),
                _ready_selector(args), _min_matches(args), wait_timeout,
                session.page.wait_for_timeout)
            if found < _min_matches(args):
                logger.info("Still nothing after %.0fs (%d match(es) for %s).",
                            wait_timeout / 1000, found, _ready_selector(args))
            html = _snapshot(session.page, url) or html
            state = _classify(session.page, html)

        # The paid path is reached only for state "captcha" — a rendered
        # widget, which IS a test and can be solved. It is NOT reached for
        # "blocked": an edge refusal offers no widget, no sitekey and no
        # challenge of any kind, so a solve there would be a charge for
        # nothing. That distinction is the whole reason page_flow separates
        # the two states, and it is bounded by SOLVES_PER_PAGE so a rotation
        # loop cannot become a bill.
        if (page_flow.should_solve(state)
                and solves_bought < page_flow.SOLVES_PER_PAGE):
            solves_bought += 1
            if handle_captcha_if_present(session.page, args):
                session.page.wait_for_timeout(1000)
                html = _snapshot(session.page, url) or html
                state = _classify(session.page, html)
                # The VERIFIED outcome, and the only one worth reporting: a
                # "ready" task result is not evidence the token works. This
                # line is what says whether the money bought anything.
                if state == "content":
                    logger.info("The solve was accepted — page %d is content "
                                "now.", page_num)
                else:
                    logger.warning(
                        "The solve was NOT accepted: page %d is still %s. The "
                        "purchase is spent.", page_num, state)

        if not page_flow.should_retry(state):
            # "content" and "empty" are both final answers. An empty page is
            # a CORRECT one — a hub category has no grid, and one page past
            # the end of a listing has no products — so retrying it would
            # spend the user's budget re-confirming the same right answer,
            # and rotating the exit would blame an address for the URL it was
            # given.
            break

        # Blocked or challenged. A different exit is the one thing that
        # plausibly changes the outcome: the ADDRESS is what was scored, not
        # the URL, so retrying it unchanged would only confirm it.
        if block_attempt < block_retries:
            if has_pool:
                logger.warning("Page %d came back as %s from %s — retrying "
                               "from another exit (%d/%d).", page_num, state,
                               mask(pool.current), block_attempt + 1,
                               block_retries)
                pool.advance(f"{state} on page {page_num}")
                session.relaunch()
            else:
                # No pool, so nowhere else to go — but a plain re-fetch is
                # what clears this on a Scraping Browser profile. The browser
                # is NOT relaunched: over `--cdp-endpoint` a profile allows
                # one live connection, so tearing the session down and
                # reconnecting risks `profile_locked` and would lose the very
                # cookies the retry is meant to build on.
                pause = args.retry_delay * (block_attempt + 1)
                logger.warning("Page %d came back as %s — re-fetching through "
                               "the same access path in %.1fs (%d/%d).",
                               page_num, state, pause, block_attempt + 1,
                               block_retries)
                time.sleep(pause)

    if load_failed:
        logger.error("Gave up loading %s after %d attempt(s).", url, args.retries)
        outcome.load_failed = True
        return outcome

    outcome.state = state

    if state == "blocked":
        # What a caller needs here is WHICH refusal this is, because the two
        # want different answers and only one of them is solvable.
        #
        # What a reader needs here is WHICH refusal arrived, because the
        # two have different remedies and only one of them is about the
        # address:
        #
        #   a page that is not Montblanc's        — the exit is scored, or
        #                                           something is intercepting.
        #                                           A different exit.
        #   nothing at all (the stream was reset) — the edge refused the
        #                                           REQUEST, and on this site
        #                                           that is a User-Agent
        #                                           denylist, not the address.
        #
        # Saying which one arrived is more use than a captcha hint that would
        # cost money for a page carrying no widget.
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
            "then try a different exit with --proxy or --cdp-endpoint. This "
            "is exit 3, distinct from a genuinely empty result (exit 4).%s",
            len(html or ""), assets, debug_html,
            (f" Tried {block_retries + 1} exit(s)." if has_pool
             else f" Re-fetched {block_retries + 1} time(s)."))
        outcome.blocked_by = "edge refusal" if html else "no-response"
        outcome.final_url = session.page.url
        return outcome

    # No readiness wait and no scroll on the content path, and their absence
    # is MEASURED rather than forgotten — see the "unknown" branch above.
    # Montblanc server-renders its whole grid in the first response, so there
    # is nothing to wait for and nothing to scroll into view. Porting the
    # sibling repos' scroll loop here would be dead code that looks
    # load-bearing (CLAUDE.md §4).

    # Dumping on success, not only on failure: a run can return the right
    # NUMBER of rows with a field silently unpopulated, and then the only way
    # to tell a parsing bug from a too-early snapshot is to inspect the exact
    # bytes the parser was given.
    if args.dump_html:
        dump_path = (args.dump_html if args.pages == 1
                     else f"{args.dump_html}.page{page_num}")
        with open(dump_path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("Saved the snapshot the parser sees to %s (%d bytes).",
                    dump_path, len(html))

    # Only for a state page_flow already counts as BLOCKED, and that
    # narrowing was earned twice.
    #
    # A marker on a page whose products have rendered guards nothing — that
    # is the "detected is not blocking" rule the captcha default follows,
    # applied to the blocking decision instead of the spending one. But
    # `state != "content"` is still too wide: an EMPTY page is a correct
    # answer, and a live run of a /p/<slug> hub reported exit 3 on a 191 KB
    # page the site had plainly served, because the hub's own performance
    # script names `akamaihd.net` and "akamai" was in the marker list. Both
    # halves were wrong; the marker is gone (see
    # product_parser.BOT_CHALLENGE_MARKERS) and this now only refines the
    # REASON for a page the policy had already given up on.
    vendor = (detect_bot_challenge(html, url=session.page.url)
              if page_flow.counts_as_blocked(state) else None)
    if vendor:
        debug_html = f"{args.out}_page{page_num}_debug.html"
        with open(debug_html, "w", encoding="utf-8") as f:
            f.write(html)
        try:
            session.page.screenshot(path=f"{args.out}_page{page_num}_debug.png",
                                    full_page=True)
        except Exception as e:
            logger.warning("Could not capture screenshot: %s", e)
        logger.error("Blocked by %s before parsing (%d bytes) — saved to %s%s. "
                     "This is exit 3, distinct from a genuinely empty result "
                     "(exit 4).", vendor, len(html), debug_html,
                     (f" (tried {block_retries + 1} exit(s))" if has_pool
                      else f" (re-fetched {block_retries + 1} time(s))"))
        outcome.blocked_by = vendor
        return outcome

    if not page_flow.should_parse(state):
        # Reached only for a state the policy says holds no rows — and it
        # says so in ONE place, so an engine cannot quietly decide to parse
        # something its twins would not.
        logger.info("Page %d came back as %s; nothing to parse.", page_num,
                    state)
        outcome.final_url = session.page.url
        return outcome

    products, listing = _parse_for_mode(html, session.page.url, args, page_num)
    logger.info("Parsed %d row(s) from page %d.", len(products), page_num)

    if listing is not None:
        # Montblanc prints its `result-count` on PAGE 1 ONLY — a `start=24`
        # slice does not repeat it — so a later page reports None here and
        # None means UNKNOWN. It is page 1's copy the run plans against,
        # which is exactly the shape §7 assumes: page 1 is fetched alone and
        # decides how far the rest can be addressed.
        outcome.total_available = listing.total_results
        outcome.pages_available = listing.pages_available
        outcome.sort_applied = listing.site_default_sort
        if page_num == 1:
            logger.info("Montblanc reports %s result(s) across %s page(s) of "
                        "%s. Asked for ordering %r; the category's own "
                        "default is %r.", listing.total_results,
                        listing.pages_available, listing.page_size,
                        listing.sort, listing.site_default_sort)
            if (listing.site_default_sort
                    and listing.sort
                    and listing.sort != listing.site_default_sort):
                # Said ONCE per run, at info level, and it is not a
                # disagreement — the site honours the `srule`, it just never
                # states which one it used, so there is nothing to compare
                # against. What this line reports is the OVERRIDE: which
                # ordering the run chose instead of the site's, because that
                # decides which products are in the file at all (0 of 24 in
                # common between two orderings of one category).
                logger.info("That is an override: a visitor with no --sort "
                            "would get %r and a different set of products. "
                            "The `sort` column records what this run asked "
                            "for; the site does not publish what it applied.",
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
        # Reported every time rather than only when it trips, so a consumer
        # gets the number rather than a threshold someone guessed.
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
                    "rather than the products being unusual — re-run with "
                    "--dump-html.",
                    share, page_num, field_name, CORE_FIELD_FLOOR)

        priced = sum(1 for row in products if row.price is not None)
        ranged = sum(1 for row in products if row.price_source == "dom_range")
        price_share = 100.0 * priced / len(products)
        if price_share < PRICE_COVERAGE_FLOOR:
            logger.warning(
                "Only %.0f%% of page %d carries a price, against a measured "
                "floor of %d%%. Both the JSON-LD price and the DOM range "
                "count toward that, so a shortfall is a parsing break rather "
                "than the site's own variety — re-run with --dump-html.",
                price_share, page_num, PRICE_COVERAGE_FLOOR)

        if args.mode == "product":
            group = {row.variant_of for row in products if row.variant_of}
            logger.info("Product: %d variant(s) of %s, %d priced.",
                        len(products), next(iter(group), products[0].sku),
                        sum(1 for r in products if r.price is not None))
        else:
            in_stock = sum(1 for row in products if row.in_stock is True)
            logger.info("Page %d: %d row(s), %d priced (%d from a 'From' "
                        "range, which is a MINIMUM over a variant group, not "
                        "a price), %d in stock. Ordering %r decides which "
                        "products are here at all.",
                        page_num, len(products), priced, ranged, in_stock,
                        listing.sort if listing else args.sort)

    outcome.products = products
    outcome.final_url = session.page.url
    return outcome


def _worker_pool(pool, worker_index: int):
    """A private ProxyPool for one worker, starting at a different exit.

    Each worker gets its OWN pool object holding the same exits rotated to a
    different offset. Two things fall out of that, both wanted:

      * Workers start on distinct exits, which is the point of running
        several — N workers all leaving from one address is just a faster way
        to burn that address.
      * No shared mutable state between threads, so rotation needs no lock.
        A worker that gets blocked can still walk the rest of the pool on its
        own.

    Its exit stays put for the worker's lifetime otherwise: a SESSION must
    not change address mid-flight, and a worker is one session.
    """
    if not pool:
        return None
    proxies = pool.proxies
    offset = worker_index % len(proxies)
    return ProxyPool(proxies[offset:] + proxies[:offset], rotate="per-run")


def _fetch_pages_concurrently(args, pool, specs, concurrency: int):
    """Fetch `specs` [(page_num, url), ...] across `concurrency` workers.

    Each worker owns its own Playwright instance, browser and exit: with the
    sync API a browser belongs to the thread that made it, so sharing one
    across threads is not an option even if it were desirable.
    """
    work = queue.Queue()
    for spec in specs:
        work.put(spec)

    results = []
    results_lock = threading.Lock()
    # Set when a page comes back with no rows at all — the end of the
    # listing. Without it, asking for 50 pages of a 5-page result would fetch
    # 45 empty ones. Workers check it before taking more work, so at most
    # (concurrency - 1) extra pages are in flight when it trips.
    exhausted = threading.Event()

    def worker(index: int):
        name = f"worker-{index + 1}"
        try:
            with sync_playwright() as pw:
                session = _BrowserSession(pw, args, _worker_pool(pool, index)).open()
                try:
                    first = True
                    while not exhausted.is_set():
                        try:
                            page_num, url = work.get_nowait()
                        except queue.Empty:
                            break
                        if not first:
                            time.sleep(args.delay)
                        first = False
                        outcome = _fetch_one_page(session, args, session.pool,
                                                  page_num, url)
                        with results_lock:
                            results.append(outcome)
                        if outcome.ok and not outcome.products:
                            logger.info("[%s] page %d returned no rows — "
                                        "treating that as the end of the listing "
                                        "and stopping dispatch.", name, page_num)
                            exhausted.set()
                finally:
                    session.close()
        except Exception:  # noqa: BLE001 — a dead worker must not hang the run
            logger.exception("[%s] died; its pages will be reported as failed.", name)

    threads = [threading.Thread(target=worker, args=(i,), name=f"page-worker-{i + 1}")
               for i in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Anything still queued was never attempted (a worker died, or dispatch
    # stopped at the end of the listing). Not reported as failed pages: they
    # were not tried, and claiming otherwise would overstate the damage.
    unattempted = []
    while True:
        try:
            unattempted.append(work.get_nowait()[0])
        except queue.Empty:
            break
    return results, sorted(unattempted), exhausted.is_set()


def scrape(args) -> int:
    # One entry per page attempted, merged after the loop rather than folded
    # into shared state during it — see PageOutcome for why that ordering
    # matters more than it looks.
    outcomes: List[PageOutcome] = []
    seen_keys = set()
    blocked = False
    # All three modes are one row per business-at-a-location, so `sku` is the
    # key for all of them.
    dedupe_key = "sku"
    # Why the loop ended. "completed" means every requested page was fetched;
    # "no_new_products" means the listing itself ran out (also a complete
    # result). "single_page_mode" is complete by construction — a detail page
    # has no page 2. Anything else is an early stop, and the run is only a
    # partial view.
    # "page_cap_reached" means the run asked for more pages than the
    # category holds and was clamped to the site's own count. On this site
    # that is a COMPLETE result in the strong sense: Montblanc imposes no cap
    # of its own, so the clamp IS the whole category — 280 results across 12
    # pages of 24, and start=288 is a served, empty grid.
    #
    # Only --mode product is single-page. Both listing modes paginate
    # identically — the same `start`/`sz` on the same URL — so neither may be
    # treated as single-page, which is the silent-success failure this family
    # exists to avoid. Note that "single page" is a statement about FETCHING:
    # --mode product still emits one row per variant, so a one-page run of a
    # sixteen-nib pen is sixteen rows.
    stop_reason = "single_page_mode" if args.mode == "product" else "completed"

    pool = proxy_pool_from_args(args)
    if pool and args.cdp_endpoint:
        logger.warning("Ignoring --proxy/--proxy-file: with --cdp-endpoint the "
                       "remote browser has its own exit, and layering a second "
                       "proxy on top would contradict it.")
        pool = None

    concurrency = max(1, args.concurrency)
    if concurrency > 1:
        if args.mode == "product":
            logger.info("--concurrency is ignored in --mode product: there is "
                        "one page to fetch (however many variants it holds).")
            concurrency = 1
        elif page_flow.concurrency_limit(args.cdp_endpoint) == 1:
            # The limit is page_flow's to state, not this engine's, so all
            # three refuse in the same place for the same reason.
            logger.warning("--concurrency is ignored with --cdp-endpoint: the "
                           "Scraping Browser API allows one live connection per "
                           "profile, and several workers would collide on it "
                           "(profile_locked). Use several pids instead, one run "
                           "each.")
            concurrency = 1
        elif not pool:
            logger.warning("--concurrency %d with no proxy pool: every worker "
                           "leaves from the SAME address, which is a faster way "
                           "to get that address scored than to gather data. "
                           "Montblanc serves an ordinary datacenter address "
                           "today, which makes an address that works one "
                           "worth not burning. Pass --proxy-file to spread "
                           "the load.", concurrency)
        if pool and pool.rotates_per_page():
            logger.info("--proxy-rotate per-page is redundant under "
                        "--concurrency: each worker already holds its own exit "
                        "for its lifetime, which is the same spread without a "
                        "browser relaunch per page.")
        if concurrency > 8:
            logger.warning("--concurrency %d means %d browsers at once "
                           "(~150-300MB each). Make sure the machine has the "
                           "memory for it.", concurrency, concurrency)

    with sync_playwright() as pw:
        session = _BrowserSession(pw, args, pool,
                                  remote=bool(args.cdp_endpoint)).open()
        try:
            target = _target_url(args)
            if target != args.url:
                # The run's own page size and ordering, applied to page 1 as
                # well as to 2..N. Page 1 under a different ordering is a
                # different set of products, so skipping it here would give a
                # run one page of one sample and N-1 pages of another.
                logger.info("Applying --sort %s and --page-size %d to page 1 "
                            "too: %s", args.sort, args.page_size, target)

            # Page 1 is always fetched on its own: its content is what decides
            # how many pages 2..N there are to address at all.
            first = _fetch_one_page(session, args, pool, 1, target)
            outcomes.append(first)

            if not first.ok:
                stop_reason = ("page_load_timeout" if first.load_failed
                               else f"blocked_{first.blocked_by}")
                blocked = first.blocked_by is not None
            elif first.state == "parse_failed":
                # Served, linked to products, parsed to nothing: OUR bug, and
                # it must not reach the sidecar as a complete run (§20).
                stop_reason = "parser_found_nothing"
            elif args.mode == "product":
                pass  # one page is the whole run — but many rows
            else:
                seen_keys.update(p.sku for p in first.products if p.sku is not None)
                planned = _plan_page_urls(args, first.final_url,
                                          first.pages_available)
                if len(planned) + 1 < args.pages:
                    # Clamped to the site's own result count, which is a
                    # complete answer rather than an early stop — see
                    # COMPLETE_STOP_REASONS.
                    stop_reason = "page_cap_reached"

                if args.pages > 1 and concurrency > 1 and not page_flow.pagination_is_addressable(first.final_url):
                    logger.warning("--concurrency %d requested, but this "
                                   "listing's pages cannot be addressed "
                                   "independently — falling back to one page "
                                   "at a time.", concurrency)
                    concurrency = 1

                if planned and concurrency > 1:
                    # Close the page-1 browser before starting workers: it has
                    # done its job, and holding it open would cost one more
                    # browser than asked for.
                    session.close()
                    specs = [(n, planned[n - 2]) for n in range(2, len(planned) + 2)]
                    logger.info("Fetching pages 2-%d across %d workers%s.",
                                len(planned) + 1, concurrency,
                                f" over {len(pool)} exit(s)" if pool else "")
                    rest, unattempted, exhausted = _fetch_pages_concurrently(
                        args, pool, specs, concurrency)
                    outcomes.extend(rest)

                    failed = [o for o in rest if not o.ok]
                    if failed:
                        worst = min(failed, key=lambda o: o.page_num)
                        stop_reason = ("page_load_timeout" if worst.load_failed
                                       else f"blocked_{worst.blocked_by}")
                        blocked = any(o.blocked_by for o in rest)
                    elif exhausted:
                        stop_reason = "no_new_products"
                    elif unattempted:
                        # Should not happen without a failure or exhaustion,
                        # but say so rather than reporting a complete run.
                        stop_reason = "pages_unattempted"
                    session = None  # already closed
                elif planned:
                    url = planned[0]
                    for page_num in range(2, len(planned) + 2):
                        # A new exit per page is what actually spreads a run's
                        # volume, and it costs a browser relaunch: carrying the
                        # session across exits would defeat the point.
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

                        # Whether this page contributed anything not already
                        # seen. Kept as a running check because the condition is
                        # inherently sequential — "new" only means anything
                        # relative to the pages before it. The authoritative
                        # dedupe happens once, after the loop, in page order.
                        fresh_count = sum(1 for p in outcome.products
                                          if p.sku is None or p.sku not in seen_keys)
                        seen_keys.update(p.sku for p in outcome.products
                                         if p.sku is not None)

                        # A page past the first that contributes nothing new
                        # means the end of the results — or that pagination is
                        # looping back on itself. Either way there is nothing
                        # further to fetch, and this is the honest terminating
                        # condition: a property of the DATA, not of a CSS
                        # selector that may have been renamed.
                        if not fresh_count:
                            logger.info("Page %d added no rows not already seen "
                                        "— treating that as the end of the "
                                        "listing.", page_num)
                            stop_reason = "no_new_products"
                            break

                        if page_num - 1 < len(planned):
                            url = planned[page_num - 1]
                            time.sleep(args.delay)
        finally:
            if session is not None:
                session.close()

    # Merge once, in PAGE order — not in the order pages happened to finish.
    # At one page at a time the two are identical, which is the point: this is
    # what keeps the output byte-for-byte the same while removing the
    # dependency on arrival order that concurrency would otherwise introduce.
    all_rows = []
    merged_seen = set()
    for oc in sorted(outcomes, key=lambda o: o.page_num):
        fresh = dedupe_by_key(oc.products, merged_seen, key=dedupe_key)
        if len(fresh) < len(oc.products):
            # Not necessarily "on an earlier page" — a duplicate can be on
            # this page. Montblanc's pagination was measured NOT repeating:
            # start=0 and start=24 of /en-fi/writing-instruments shared 0 of
            # 48 skus. So any non-zero count here is worth a look, and a
            # count near the page size means a page was re-fetched rather
            # than advanced.
            logger.info("Page %d: dropped %d duplicate row(s).",
                        oc.page_num, len(oc.products) - len(fresh))
        all_rows.extend(fresh)

    # Completeness, checked over the MERGED result rather than per page — a
    # per-page check cannot see a gap BETWEEN two pages, which is exactly
    # where a short page hides.
    #
    # NOT "pages x rows-per-page" as a hard expectation, even though the
    # page size is the run's own `sz`: the LAST page of a listing is
    # legitimately short — /en-fi/writing-instruments ends 240 + 24 + 16 =
    # 280 — and a threshold that fires on every healthy run teaches the
    # reader to ignore it. What is worth warning about is a page that came
    # back materially THIN against its siblings, which is what a truncated
    # response looks like.
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
                "(%d rows): %s. Montblanc fills every page but the last "
                "one, so a short page in the middle of a run is a truncated "
                "response rather than a short listing — re-run with "
                "--dump-html.",
                ", ".join(str(p) for p, _ in thin), fullest,
                ", ".join("page %d: %d" % (p, n) for p, n in thin))
        if total_available:
            # The honest sentence, and the reason `result-count` is worth
            # carrying: it says what share of the category this file holds.
            # Unlike bbb-scraper, where the same line has to warn that a
            # complete run is a 1.2% sample, here a run that walks to the end
            # really does reach 100% — the site imposes no cap.
            logger.info("Montblanc reports %d product(s) for this listing; "
                        "this run holds %d (%.1f%%) across %d page(s) of the "
                        "%s available.", total_available, len(all_rows),
                        100.0 * len(all_rows) / total_available,
                        len([o for o in outcomes if o.ok]), pages_available)

    ok_pages = [o for o in outcomes if o.ok]
    failed_pages = [o.page_num for o in outcomes if not o.ok]
    final_url = (max(ok_pages, key=lambda o: o.page_num).final_url
                 if ok_pages else args.url)

    # One-per-run context, in the sidecar rather than repeated down a column.
    #
    # For a listing run that is Montblanc's own arithmetic —
    # `total_results`, `pages_available`, the page size it actually applied,
    # and the ordering it says it applied. Together they let a consumer tell
    # a run that covered a whole category from one that read the first two
    # pages of it, and no column can carry them because they describe the
    # LISTING rather than any product.
    #
    # `locale` is in here too, and it is not decoration: Montblanc sets
    # prices per market rather than converting them, so a file of prices
    # without the market it was read from is a file of numbers without
    # units. It is on every row as well, because a consumer merging two runs
    # needs it per row.
    extra = None
    if args.mode != "product":
        sorts_applied = sorted({o.sort_applied for o in outcomes
                                if o.sort_applied})  # the site's defaults
        extra = {"total_results": total_available,
                 "pages_available": pages_available,
                 "page_size": args.page_size,
                 "locale": locale_from_url(final_url or args.url),
                 "sort_requested": args.sort,
                 # What a visitor with no --sort would have got. NOT "what
                 # the site applied": Montblanc does not publish that, and a
                 # column claiming it would be a guess (see
                 # product_parser.default_sort_rule).
                 "site_default_sort": sorts_applied[0] if len(sorts_applied) == 1
                                      else sorts_applied}
        # No `capped_by_site` and no `reachable_max` here, and their ABSENCE
        # is the finding rather than an omission. bbb-scraper needs both
        # because that site refuses to serve past page 15 however many rows
        # matched, so "complete" there can mean a 1.2% sample. Montblanc
        # imposes no cap (measured: 280 results, 240 + 24 + 16 = 280, and
        # start=288 is a served empty grid), so `pages_available` already
        # says everything those fields would — and a `reachable_max` of
        # pages x page_size would OVERSTATE a short last page: 2 x 24 = 48
        # on a category holding 25.

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
        logger.error("--fingerprint needs --twocaptcha-key (the Fingerprint API "
                     "uses the same key, though it's a separate subscription "
                     "from solving).")
        sys.exit(2)
    if args.fingerprint and args.cdp_endpoint:
        logger.warning("--fingerprint is ignored with --cdp-endpoint: the "
                       "Scraping Browser supplies its own fingerprint, and "
                       "stacking a second one on top creates a mismatch rather "
                       "than better cover.")
    try:
        sys.exit(scrape(args))
    except ProxyError as e:
        # Bad usage, not a crash: a typo in a proxy list would otherwise
        # surface as a connection failure on page 1 with nothing naming it.
        logger.error("%s", e)
        sys.exit(2)
    except PWError as e:
        # A remote browser that will not accept the connection is a REMOTE
        # API failure (exit 5), not a crash in this code (exit 1) and not bad
        # usage (exit 2). The distinction earns its keep on the commonest one:
        # `profile_locked` means another run still holds this `pid`, and a
        # harness that sees exit 1 goes looking for a bug in the scraper
        # instead of waiting or passing a different pid.
        text = _mask_credentials(str(e))
        if "profile_locked" in text or "connect to --cdp-endpoint" in text:
            logger.error("%s", text)
            sys.exit(EXIT_API_ERROR)
        raise

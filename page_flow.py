"""
page_flow.py
------------
The retry / solve / blocked decision, as DATA rather than as three copies of
an if-chain (CLAUDE.md §1).

Montblanc answers a request five ways, and four of them want a different
response:

    a listing or product page with structured data on it  -> parse
    a page it served with no products on it (a hub)       -> parse, it is an answer
    a challenge widget                                    -> solve, or rotate
    a refusal                                             -> rotate; nothing to solve
    something that is not a Montblanc page at all         -> wait, then retry

Three copies of that triage across three engines would drift, and the drift
would be silent — one engine reporting exit 3 where its twin reports exit 0
on the same response.

THE FIFTH ANSWER IS NOT A PAGE
-------------------------------
This site's primary refusal has a shape the rest of this family has not met,
and it is the reason `classify_transport_error` exists beside `classify`.

Montblanc's edge does not send a 403 and does not send a challenge page. It
KILLS THE CONNECTION. Measured 2026-09-17 from a Hetzner address that the
same URL serves normally with a browser-shaped User-Agent:

    curl      `curl: (92) HTTP/2 stream 1 was not closed cleanly:
              INTERNAL_ERROR (err 2)`
    requests  `ReadTimeout` after the full timeout elapsed

There is no status code to read, no body to match a marker against, and
nothing for `detect_page_state` to classify — it is never called, because no
response arrived. The trigger is an explicit client-library denylist on the
User-Agent header: no UA at all, `curl/*`, `python-requests/*`,
`Python-urllib/*` and `Go-http-client/*` were all refused, while
`Mozilla/5.0`, `Wget/1.21`, `Scrapy/2.11` and the literal string `foo` were
all served HTTP 200.

Two things follow, and the second is the one that saves an hour:

  * The three browser engines never see this. They send a real browser's UA,
    which is a browser-shaped string by definition.
  * On THIS site a timeout from an HTTP client is a BLOCK, not a slow
    network. CLAUDE.md §8 says a proxy failure is not a timeout; this is the
    same class one step further out, and it wants the opposite response from
    a real timeout. Retrying the identical request at the same exit cannot
    ever succeed, because nothing about the address is what was rejected.
    The fix is the User-Agent.

Nothing here imports a browser, and **no JavaScript crosses this boundary**:
Selenium's `execute_script` takes a function BODY with an explicit `return`
while Playwright and pyppeteer take `() => expr`, so a shared snippet would
quietly acquire one driver's dialect. The callbacks below are named for the
OPERATION instead, and each engine spells it in its own dialect (§1).
"""

import logging
import re
from typing import Callable, Optional, Tuple
from urllib.parse import urlparse

from product_parser import (PAGE_SIZE, detect_bot_challenge,  # noqa: F401
                            detect_page_state, is_product_url,
                            pages_available)

log = logging.getLogger("page_flow")


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

# How many product tiles mean "this listing has rendered".
#
# `> 1` on purpose, per CLAUDE.md §5: waiting for ONE match resolves on
# something unrelated long before the grid is really there. Montblanc serves
# a default page size of 24 and every captured listing carried 24 tiles
# (49 `data-pid` attributes, since the wishlist form repeats the id), so four
# is a floor a real page clears instantly and a half-arrived one does not.
MIN_CARD_MATCHES = 4

# A listing page's readiness anchor.
#
# `data-pid` is the site's OWN id attribute, which §4 calls a better anchor
# than any class and nearly as good as structured data. The alternative on
# this site is `.product-tile`, and that one is measurably worse: it counts
# 49 on a listing and **111 on a product page**, because a detail page
# carries a recommendation carousel built out of the same tiles. An anchor
# that fires more strongly on the wrong page kind is not an anchor.
READY_SELECTOR_LISTING = "[data-pid]"

# A product page's readiness anchor. `.product-detail` counts 4 on both
# captured product pages and **0** on every listing, so it separates the two
# page kinds cleanly in the direction the carousel breaks.
READY_SELECTOR_PRODUCT = ".product-detail"

CONTENT_TIMEOUT_MS = 45_000
CONTENT_TIMEOUT_MS_PRODUCT = 30_000


def ready_selector(mode: str) -> str:
    return READY_SELECTOR_PRODUCT if mode == "product" else READY_SELECTOR_LISTING


def min_matches(mode: str) -> int:
    return 1 if mode == "product" else MIN_CARD_MATCHES


def content_timeout_ms(mode: str) -> int:
    return CONTENT_TIMEOUT_MS_PRODUCT if mode == "product" else CONTENT_TIMEOUT_MS


# How long to keep polling for an anchor, and how often.
#
# Polled through `count(selector)` — a callback each engine implements with
# its own `querySelectorAll` call — and NEVER by handing the browser a string
# to evaluate. CLAUDE.md §18: a site whose Content-Security-Policy omits
# `unsafe-eval` kills `wait_for_function` with an `EvalError` and takes the
# run down with exit 1, on the site's most obvious URL. Montblanc has not
# been measured for that, and the cheap habit costs nothing on a site that
# would have allowed it.
READY_POLL_MS = 500


def wait_for_count(count: Callable[[str], int], selector: str, minimum: int,
                   timeout_ms: int, sleep_ms: Callable[[int], None]) -> int:
    """Poll `count(selector)` until it reaches `minimum` or the budget runs out.

    Returns the last count seen, so a caller can report "3 of 4 expected"
    rather than only that it timed out.
    """
    waited = 0
    seen = 0
    while waited <= timeout_ms:
        seen = count(selector)
        if seen >= minimum:
            return seen
        sleep_ms(READY_POLL_MS)
        waited += READY_POLL_MS
    return seen


# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------

def classify(html: Optional[str], status: Optional[int] = None,
             url: str = "") -> str:
    """Name what Montblanc answered with. See product_parser.detect_page_state.

    The argument ORDER is the contract: every engine calls
    `classify(html, status, url)`. A sibling repo shipped `classify(html,
    url=...)` in two of three engines against a callee that took `status`
    second, and both crashed on their first fetch — invisible to import,
    `--help`, `compileall` and 400+ green offline assertions, because none of
    those calls a function the way a live run does (§17). `smoke_test.py`
    binds every engine's call against this signature for that reason.
    """
    return detect_page_state(html or "", status, url)[0]


def classify_with_reason(html: Optional[str], status: Optional[int] = None,
                         url: str = "") -> Tuple[str, Optional[str]]:
    """`classify`, keeping the detail — the marker or the status that decided.

    Engines log this so a red run says WHY, rather than only that it was
    red.
    """
    return detect_page_state(html or "", status, url)


# ---------------------------------------------------------------------------
# The transport-level refusal
# ---------------------------------------------------------------------------
# Exception TEXT this site's refusal arrives as, in the three libraries this
# repo can reach it through. Matched against `type(exc).__name__` and
# `str(exc)` together, because `requests` puts the useful half in the class
# name and Chromium puts it in the message.
#
# Chromium's proxy errors are listed separately below and deliberately NOT
# folded in here: §8 says a proxy failure is not a timeout and the two want
# opposite responses — a timeout deserves another try at the same exit, a
# dead proxy a different one.
_TRANSPORT_REFUSAL_RE = re.compile(
    r"stream .* was not closed cleanly"
    r"|INTERNAL_ERROR"
    r"|ERR_HTTP2_PROTOCOL_ERROR"
    r"|ERR_CONNECTION_RESET"
    r"|ERR_EMPTY_RESPONSE"
    r"|RemoteDisconnected"
    r"|Connection aborted"
    r"|ReadTimeout"
    r"|Read timed out",
    re.I)

# Chromium's own names for "the proxy is dead", which is a different fault
# with a different remedy (§8). Kept apart so the caller can rotate rather
# than retry.
_PROXY_FAILURE_RE = re.compile(
    r"ERR_PROXY_CONNECTION_FAILED"
    r"|ERR_TUNNEL_CONNECTION_FAILED"
    r"|ERR_PROXY_AUTH_(?:UNSUPPORTED|REQUESTED)"
    r"|ERR_UNEXPECTED_PROXY_AUTH",
    re.I)


def classify_transport_error(exc: BaseException) -> str:
    """Name a fetch that raised instead of answering.

    Returns one of:

        "proxy"      the exit is dead          -> rotate, do not retry here
        "refused"    the edge hung up          -> see below
        "error"      anything else             -> ordinary retry

    "refused" is the interesting one and it is why this function exists. On
    Montblanc a connection reset or a read timeout is not usually a network
    fault; it is the edge declining a request whose User-Agent named an HTTP
    client library. The engines all send a browser's own UA so they should
    never see it — if one does, the message it logs needs to point at the
    header rather than at the network, because retrying the identical
    request cannot work.

    This deliberately over-classifies a genuine slow network as "refused".
    That is the safer direction: the remedy printed for "refused" (send a
    browser UA, then rotate the exit) is harmless advice on a slow network,
    whereas silently retrying a refusal forever is a run that never ends and
    never says why.
    """
    text = f"{type(exc).__name__}: {exc}"
    if _PROXY_FAILURE_RE.search(text):
        return "proxy"
    if _TRANSPORT_REFUSAL_RE.search(text):
        return "refused"
    return "error"


# A User-Agent this site serves. Used ONLY to sanity-check an HTTP client's
# header before it is sent, never to overwrite a browser's own — §8: the user
# agent comes from the browser, not a literal, and a hardcoded version drifts
# from whatever Chromium is installed.
#
# The test is "does this name an HTTP client library", not "does this look
# like Chrome", because the denylist is what was measured: `Wget/1.21`,
# `Scrapy/2.11` and `foo` are all served.
_CLIENT_LIBRARY_UA_RE = re.compile(
    r"^\s*$|curl|python-requests|python-urllib|go-http-client|libwww|okhttp"
    r"|java/|apache-httpclient|axios/|node-fetch",
    re.I)


def ua_will_be_refused(user_agent: Optional[str]) -> bool:
    """True when this User-Agent is one Montblanc's edge refuses outright.

    Cheap pre-flight for the HTTP paths, so a run fails with "this UA is on
    the edge's denylist" instead of with a timeout twenty seconds later.
    """
    return bool(_CLIENT_LIBRARY_UA_RE.search(user_agent or ""))


# How many product links a SERVED page must carry before "we parsed nothing"
# is reported as OUR failure rather than as an empty category (§20).
#
# Two rather than one: a single stray product link can appear in a nav
# flyout or a "recently viewed" strip on a page that genuinely lists no
# products, and calling that a parser failure would cry wolf on a correct
# answer. A real grid links to far more than two.
PARSE_FAILURE_MIN_LINKS = 2


def looks_like_a_parse_failure(state: str, rows: int, link_count: int) -> bool:
    """True when the page was SERVED, links to products, and parsed to zero.

    That combination is this repo's bug, not the site's, and it deserves to
    say so by name — "0 products" sends the reader to check the URL when the
    thing to check is the parser. Deliberately NOT a new exit code: the
    catalogue question really was answered, so it stays EXIT_NO_PRODUCTS and
    only the `stop_reason` differs (§20).
    """
    if rows:
        return False
    if state not in ("content", "empty"):
        return False
    return link_count >= PARSE_FAILURE_MIN_LINKS


STATE_POLICY = {
    # A page with the site's own structured data on it.
    "content":   {"retry": False, "solve": False, "blocked": False, "parse": True},
    # A page Montblanc served that has no products on it — a hub page, a
    # discover article, or a `start=` past the end of a category. The site
    # answered exactly what was asked. EXIT_NO_PRODUCTS rather than
    # EXIT_BLOCKED: reporting it as blocked sends a user hunting for a proxy
    # problem that is not there.
    "empty":     {"retry": False, "solve": False, "blocked": False, "parse": True},
    # A refusal with no challenge on it. There is nothing to solve — the edge
    # is not offering a test, it is declining — so the only move is a
    # different exit. Paying a solver here would buy nothing, which is why
    # `solve` is False on a state whose name says blocked.
    "blocked":   {"retry": True,  "solve": False, "blocked": True,  "parse": False},
    # A challenge widget. This one IS a test, and it is the state that pays
    # for a solver. A fresh browser from a different exit clears it too,
    # which is why `retry` is also True.
    "captcha":   {"retry": True,  "solve": True,  "blocked": True,  "parse": False},
    # Not recognisably a Montblanc page. A wait, not a spend.
    "unknown":   {"retry": True,  "solve": False, "blocked": False, "parse": False},
}


def should_retry(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["unknown"])["retry"]


def should_solve(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["unknown"])["solve"]


def counts_as_blocked(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["unknown"])["blocked"]


def should_parse(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["unknown"])["parse"]


# Whether a blocked page is worth re-fetching at all.
#
# True here, and CONSULTED rather than merely documented — the engines read
# it, so setting it False really does stop the retry loop. (A sibling repo
# carried this constant with a paragraph of justification and no reader,
# which is the same defect as dead code that looks load-bearing: §17.)
#
# True because on Montblanc a re-fetch genuinely can change the answer: what
# the edge scores is the address and the request's headers, and a rotation
# moves the first while a fresh browser re-rolls the second.
RETRY_ON_BLOCKED = True

# How many times to re-fetch a blocked page when there is no proxy pool to
# rotate into.
#
# One, and only one: without a pool every retry leaves from the same address
# with the same headers, which is the pair the edge decided on. A second
# attempt is a second identical refusal. WITH a pool, the engines retry once
# per remaining exit instead, because there the retry changes the one
# variable the refusal depends on.
BLOCK_RETRIES_WITHOUT_POOL = 1

# At most one solve per page. A challenge that survives a solved token is not
# a challenge this run can pass, and a second solve is a second charge for
# the same answer.
SOLVES_PER_PAGE = 1


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def pagination_is_addressable(url: str) -> bool:
    """Whether page N of this listing can be fetched without walking to it.

    This is the question CLAUDE.md §18 says to ask PER URL rather than per
    site, because a sibling repo has one page kind that paginates by address
    and one that does not — and a `page_url()` used unconditionally there
    reported a COMPLETE run holding page 1.

    On Montblanc both listing kinds answer the same way, and it was measured
    rather than assumed (2026-09-17):

        /en-fi/bags/backpacks?start=24&sz=24
            -> HTTP 200, a full page, its own ItemList holding item 25
        /en-fi/search?q=fountain+pen&start=0&sz=24
            -> HTTP 200, 24 tiles, its own ItemList of 24

    A PRODUCT page is not addressable and not paginated — it is one page —
    so it returns False and the engines fetch it alone.
    """
    parsed = urlparse(url or "")
    if not (parsed.scheme and parsed.netloc):
        return False
    return not is_product_url(url)


def pages_to_plan(pages_requested: int, pages_avail: Optional[int]) -> int:
    """How many pages a run may ask for, given what page 1 reported.

    Montblanc prints its own `result-count` above the grid, so the number of
    pages is known from page 1 rather than discovered by walking off the end
    (§7 layer 2, with the site doing the arithmetic).

    There is NO site-imposed ceiling to clamp against — unlike bbb-scraper,
    where the same function has to cap at 15 — so a request is limited only
    by what the category actually holds. Where page 1 stated no count, the
    request stands and the run discovers the end from the data (§7 layer 3):
    an unknown is not a zero.
    """
    requested = max(1, int(pages_requested))
    if not pages_avail:
        return requested
    return max(1, min(requested, int(pages_avail)))


def plan_from_total(pages_requested: int, total: Optional[int],
                    page_size: int = PAGE_SIZE) -> int:
    """`pages_to_plan`, taking the site's stated result COUNT rather than pages."""
    return pages_to_plan(pages_requested, pages_available(total, page_size))


def concurrency_limit(cdp_endpoint: Optional[str]) -> Optional[int]:
    """1 when workers would collide, else None for "no limit imposed here".

    The Scraping Browser API allows ONE live connection per profile, so N
    workers sharing a `pid` collide with `profile_locked`. Several `pid`s,
    one run each, is the way to parallelise that path (§7).
    """
    return 1 if cdp_endpoint else None

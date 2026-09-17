"""
product_parser.py
-----------------
Everything that knows what montblanc.com looks like. The engines drive
browsers; this file turns what they fetched into rows.

What the site publishes, measured before a line of this was written
-------------------------------------------------------------------
Counted 2026-09-17 from a Hetzner address in Helsinki, over captures of the
homepage, four category listings, a search page, two product pages and one
`Search-UpdateGrid` fragment:

    listing page   2 JSON-LD blocks — `BreadcrumbList` and `ItemList`.
                   The ItemList holds one `Product` per tile with `name`,
                   `brand`, `url`, `image[]` and an `offers` object carrying
                   `priceCurrency`, `price` and `availability`.
    product page   2 JSON-LD blocks — `BreadcrumbList` and **`ProductGroup`**,
                   NOT `Product`.

That second line is CLAUDE.md §20's trap, present here exactly as described:
porting a listing parser onto a detail page returns zero rows in silence,
because it is looking for a type the page does not publish. `parse_products`
and `parse_product_detail` are therefore separate entry points, and the smoke
suite pins that the listing parser finds NOTHING on a detail fixture — the
failure stated directly rather than guarded against.

`ProductGroup` is also the reason `--mode product` exists at all. It carries
`hasVariant` with one entry per nib width or size, each with its own `sku`,
its own `price` and its own `url` — sixteen of them on one Meisterstück
fountain pen, whose group price is EUR 1000 while every variant is EUR 850.
A listing row cannot answer "what does the Medium nib cost"; these can.

    Read `url` on a variant, never `offers.url`.
    Every variant's `offers.url` is the GROUP's page, so using it makes all
    sixteen rows point at the same address while their titles, skus and
    prices all look correct — §4's "product URL under offers.url" trap
    inverted, and just as quiet.

No JSON-LD id, so the URL is the id
------------------------------------
Neither the ItemList's `Product` nor the tile carries a `sku` FIELD. The id
lives at the end of the product URL and in `data-pid`, and the two agree:
`…-MB222875VG.html` / `data-pid="MB222875VG"`. Measured id shapes across
three captures: `MB\\d+` (46), `MB\\d+VG` (16), `MB\\d+B` (1), plus `MB\\d+M`
on the range-priced groups — so the pattern is `MB` then digits then
optional trailing letters, and the regex anchors on the END of the path
because a slug can hold other numbers.

The tile's tracking payload is the richest source on the page
--------------------------------------------------------------
Every tile carries `data-tracking-event-payload`, an HTML-escaped JSON blob
built for the site's own analytics, holding far more than the JSON-LD:
collection, sub-collection, colour, category, special-edition and — the one
that matters most — real stock.

Coverage over 144 tiles on six categories: `item_id` and `item_name` 100%,
`item_category` 97%, `item_collection` 96%, `item_material_color` 94%,
`item_sub_collection` 93%.

This is §4's "the site's own data attribute is a better anchor than any
class", with the twist that here it SUPPLEMENTS structured data rather than
replacing it. The JSON-LD stays primary — it is one object per product and
cannot be mis-scoped — and the payload is joined onto it by sku.

Three fields in that payload were measured and deliberately NOT kept:
`item_engraved` was False on 384 of 384 tiles (a constant), and
`item_variant` equalled `item_material_color` on 383 of 384 (a duplicate).
`item_sellable` was populated on 7%. See output_writer.Product.

Prices: one convention everywhere, and a range that is not a price
-------------------------------------------------------------------
Montblanc renders every market in ONE numeric convention — comma thousands,
dot decimal — with the symbol prefixed either with a space or without:

    en-fi  &euro; 2,000.00      de-de  &euro; 360.00
    en-us  $1,990.00            ja-jp  &yen; 49,500

The family's three-convention amount parser is kept anyway rather than
narrowed to this one, because it is shared, correct, and costs nothing: a
comma with exactly three trailing digits reads as a thousands grouping, so
the JPY form parses to 49500 without a special case.

What DOES need a special case is the second price shape. 14 of 192 listing
rows had NO `offers.price` in the JSON-LD, and every one of them was a
variant group rendering a RANGE:

    <span class="range">
      <span class="from-range-text">From </span>
      <span class="value" content="515.00">&euro; 515.00</span>
    </span>

That number is a MINIMUM over the group's variants, not the price of
anything you can buy. It is recorded, because "From 515" is a useful and
true fact, but it is recorded with `price_source="dom_range"` so a consumer
comparing prices can exclude it. Writing it as an ordinary price would be
§8's "never present a guess as a fact" with a decimal point on it.

The `content="515.00"` attribute is read in preference to the text: it is
the site's own machine-readable value, already normalised, and it sidesteps
the currency symbol entirely.

There is NO discount chain here, so there is no DOM overlay
------------------------------------------------------------
§4 says to count the KINDS of struck-through price before writing either
path, and to delete the overlay rather than port it where a site has no
discount chain. Counted here: 0 `strike`, 0 `was-price`, 0
`price-standard`, 0 `discount`, 0 Omnibus 30-day-low, on listings AND
product pages. `/en-fi/sale` and `/en-fi/outlet` are both HTTP 404.

So `_overlay_tile_prices` does not exist in this file, and `price_source`
records WHICH NODE was read rather than a two-view confirmation — the same
choice amazon-scraper made for the same reason.

The block is not a status code and not a page
----------------------------------------------
See `detect_page_state`. Montblanc's edge refuses by killing the connection,
which reaches Python as a timeout. That cannot be detected from markup at
all, so the markers below are the SECONDARY signal and the primary one lives
in the engines.

`akamai` is not a marker, and counting it is what proved that
-------------------------------------------------------------
§18: count every candidate on a page you KNOW is good before adding it.
Done here, and it changed the set. Montblanc is Akamai-fronted and its own
performance script references `akamaihd.net` on every page it serves —
1 occurrence on the homepage, 1 on a listing, 1 on a product page. A bare
`akamai` marker would report every page blocked, which is precisely the
tokopedia-scraper failure that put the rule in CLAUDE.md.

Every other candidate counted 0 on all four known-good captures, and those
are the ones below: `errors.edgesuite.net`, `Reference #`, `Access Denied`,
and the vendor names. `cf-turnstile` is deliberately absent (§8/§19: it is
injected by 2Captcha's own auto-solve extension and is measured useless);
`challenges.cloudflare.com` is kept instead, at 0 on every served page here.
"""

import html as _html
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import (parse_qsl, quote, urlencode, urljoin, urlparse,
                          urlunparse)

from bs4 import BeautifulSoup

from output_writer import Product


# ---------------------------------------------------------------------------
# Hosts and locales
# ---------------------------------------------------------------------------
# All 72 markets are served from ONE host with the market in the path. There
# is no per-country TLD to support, which is the whole difference between
# this file and mediamarkt-scraper's host table.
HOSTS = ("www.montblanc.com", "montblanc.com")
CANONICAL_HOST = "www.montblanc.com"

# Locales the site's own country selector listed, as an ADVISORY set — not
# as an allowlist to refuse against. That distinction cost a live run and is
# the most transferable thing in this file.
#
# Fetched 2026-09-17 from
# `/on/demandware.store/Sites-MontblancROW-Site/en_FI/CountrySelector-Start`:
#
#     curl -sS -A "$UA" '…/CountrySelector-Start' \
#       | grep -ohE '/[a-z]{2}-[a-z]{2}(/|")' | tr -d '/"' | sort -u
#
# 72 entries — and `en-fi` WAS NOT ONE OF THEM, because the selector omits
# the locale you are currently browsing and that request was served from a
# Finnish exit. So the set the site hands you depends on where you ask from,
# and treating any single fetch of it as the complete list is wrong by
# exactly one entry: the one you are most likely to be using.
#
# The first version of this file made that list an allowlist. It then refused
# `https://www.montblanc.com/en-fi/writing-instruments` — the very URL the
# site geo-redirects a Finnish visitor to — with "en-fi is not one of
# Montblanc's 72 locales", which is false and sends the reader hunting for a
# typo they did not make. That is §5's "refuse WITH THE REASON, and do not
# give a reason that is untrue" failing in the most embarrassing available
# direction.
#
# So the CHECK is the shape — `{lang}-{cc}`, which is the site's own
# addressing scheme — and this set is used only to warn that a locale was
# not in the snapshot. A warning is right for a list that is known to be
# incomplete; a refusal is not.
KNOWN_LOCALES = (
    "ar-ae", "ar-sa", "de-at", "de-ch", "de-de", "en-ae", "en-au", "en-be",
    "en-bg", "en-bh", "en-ca", "en-ch", "en-cl", "en-co", "en-cy", "en-cz",
    "en-dk", "en-ee", "en-eg", "en-fi", "en-gb", "en-gr", "en-hk", "en-hr",
    "en-hu", "en-id", "en-ie", "en-il", "en-is", "en-jo", "en-kw", "en-li",
    "en-lt", "en-lu", "en-lv", "en-mt", "en-my", "en-nl", "en-no", "en-nz",
    "en-om", "en-pe", "en-ph", "en-pl", "en-pt", "en-qa", "en-ro", "en-rs",
    "en-sa", "en-se", "en-sg", "en-si", "en-sk", "en-th", "en-tw", "en-us",
    "en-vn", "en-za", "es-es", "es-us", "fr-be", "fr-ca", "fr-ch", "fr-fr",
    "fr-lu", "fr-mc", "it-it", "ja-jp", "ko-kr", "zh-cn", "zh-hk", "zh-sg",
    "zh-tw",
)

# Kept under the old name so a caller reading either spelling gets the same
# object; `KNOWN_LOCALES` is the name that says what it is.
SUPPORTED_LOCALES = KNOWN_LOCALES

DEFAULT_LOCALE = "en-us"

_LOCALE_RE = re.compile(r"^/([a-z]{2}-[a-z]{2})(?=/|$)")


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
# Salesforce Commerce Cloud, and it paginates with `start`/`sz` on the
# CATEGORY URL ITSELF — verified rather than assumed (§7):
#
#     /en-fi/bags/backpacks?start=24&sz=24
#     -> HTTP 200, a full page, its own JSON-LD ItemList holding item 25.
#
# That is what makes page URLs constructible and therefore concurrency
# possible. The site also exposes the fragment endpoint its own front end
# calls (`…/Search-UpdateGrid?cgid=backpacks&start=24&sz=24`), which returns
# the tiles WITHOUT any JSON-LD; the full-page route is preferred precisely
# because it keeps structured data on every page rather than only the first.
#
# Position restarts at 1 on every slice, which is why `page` is threaded
# through the parser and why the suite asserts the PAIR is unique (§18).
PAGE_PARAM_START = "start"
PAGE_PARAM_SIZE = "sz"

# 24 is the site's own default and the value its sort links are built with.
# `sz=48` and `sz=96` both work and return more, but the page size is left at
# the site's default so a run looks like a visitor rather than like a crawl.
PAGE_SIZE = 24

# Montblanc applies NO page cap: `writing-instruments` states 280 results and
# start=240 → 24 tiles, start=264 → 16, start=288 → 0. 240+24+16 = 280.
# So unlike bbb-scraper there is no `MAX_PAGES` here, and a run that walks a
# category to the end genuinely holds the whole category.


# ---------------------------------------------------------------------------
# Sort rules — and why the default is not the site's
# ---------------------------------------------------------------------------
# §21 says to run the same query under two orderings and diff the id sets,
# because on a site that sells placement the ordering decides WHICH rows are
# in the file. Done here, on page 1 of `writing-instruments` (2026-09-17):
#
#     srule=recommended_sv_30d (the site's default)   24 ids
#     srule=FA_price-ascending                        24 ids
#     products in common                               0
#
# Zero of twenty-four. The site's default is a merchandised ordering — a
# 30-day sales-velocity recommendation — so page 1 under it is "what sold
# recently", which is a perfectly good thing for a shopper and a badly
# skewed sample for anyone measuring a catalogue.
#
# Unlike BBB's accreditation bias this is not a paid placement and there is
# nothing dishonest about it, so the argument for overriding it is weaker —
# but the CONSEQUENCE is identical: a run under the default is not a sample
# of the catalogue, and two runs under different orderings are different
# samples rather than two observations. So `sort` is a column, `diff_runs.py`
# refuses to compare across it, and the default here is a deterministic one.
#
# `FA_price-ascending` is chosen as the default because it is total and
# stable: every product has a price position, so page N means the same set
# tomorrow unless prices changed — which is exactly the thing being
# monitored. "recommended" reshuffles on the site's own schedule, which
# would manufacture diffs that say nothing about the catalogue.
SORTS = {
    "price-asc": "FA_price-ascending",
    "price-desc": "FA_price-descending",
    "new": "new_in",
    "recommended": "recommended_sv_30d",
}
DEFAULT_SORT = "price-asc"

# The site's own default, recorded so the README and the CLI help can say
# what is being overridden, and so `--sort recommended` reproduces a
# visitor's view exactly.
SITE_DEFAULT_SORT = "recommended"

SORT_PARAM = "srule"


# ---------------------------------------------------------------------------
# URL shapes
# ---------------------------------------------------------------------------
# A product is a FLAT path under the locale, ending in its id:
#     /en-fi/montblanc-companion-rectangular-backpack--MB222875VG.html
# A category is an extensionless path of one to three segments:
#     /en-fi/bags/backpacks
# Japanese category slugs are percent-encoded, which is why the category
# check is "not a product and not a known non-category" rather than a
# positive pattern over `[a-z-]`.
_SKU_IN_URL_RE = re.compile(r"-(MB\d+[A-Za-z]*)\.html(?:$|[?#])", re.I)

_PRODUCT_PATH_RE = re.compile(r"-MB\d+[A-Za-z]*\.html(?:$|[?#])", re.I)

# Route prefixes that live under a locale and are not catalogue listings.
# `search` is deliberately absent: it IS a listing, just selected by keyword,
# and `--mode search` parses it with the same code.
_NOT_A_CATEGORY = frozenset({
    "cart", "login", "wishlist", "customer-service", "customer-service-rna",
    "discover", "craftsmanship", "careers.html", "corporate-gifts.html",
    "art-of-gifting.html", "stores", "account", "checkout", "order",
})


def _split(url: str):
    return urlparse(url)


def locale_from_url(url: str) -> Optional[str]:
    """The `en-fi` out of `https://www.montblanc.com/en-fi/bags/backpacks`.

    Returns None when the path carries no locale segment, which is what the
    bare host does before its geo-redirect fires.
    """
    m = _LOCALE_RE.match(_split(url).path or "/")
    return m.group(1).lower() if m else None


def is_supported_url(url: str) -> Tuple[bool, str]:
    """(ok, reason). The reason is shown to the user, so it must be true.

    §5: refuse a host WITH THE REASON, and never with a reason that sends the
    reader looking for a typo they did not make.

    The reason NEVER repeats the URL. Every caller prefixes it with the URL
    already (`p.error(f"{url!r} {why}.")`), and a reason that names it too
    prints the address twice in one sentence.
    """
    parts = _split(url)
    if parts.scheme not in ("http", "https"):
        return False, "is not an http(s) URL"
    host = (parts.hostname or "").lower()
    if not host:
        return False, "has no hostname"
    if host not in HOSTS:
        # No count in this message on purpose (§13): the number of markets
        # is a living thing and a stale figure in an error message reads as
        # current. The shape is what the reader needs.
        return False, (f"{host} is not a Montblanc storefront — this scraper "
                       f"reads {CANONICAL_HOST}, which serves every market "
                       f"from one host with the locale in the path "
                       f"(/en-us/…, /ja-jp/…)")
    loc = locale_from_url(url)
    if loc is None:
        return False, ("has no locale segment — Montblanc addresses every "
                       "page as /{locale}/… (e.g. /en-us/bags). The bare host "
                       "only geo-redirects to one.")
    # Shape only — see KNOWN_LOCALES for why this is not an allowlist.
    return True, ""


def locale_is_known(locale: Optional[str]) -> bool:
    """Whether this locale appeared in the captured country-selector set.

    False is NOT an error — the set omits whichever locale the request was
    served in, so a perfectly real market can be absent from it. Callers warn
    on False and carry on. See KNOWN_LOCALES.
    """
    return (locale or "").lower() in KNOWN_LOCALES


def is_product_url(url: str) -> bool:
    """True for a product detail page — a path ending in `-MB….html`."""
    return bool(_PRODUCT_PATH_RE.search(_split(url).path or ""))


def is_search_url(url: str) -> bool:
    path = (_split(url).path or "").rstrip("/")
    return path.endswith("/search")


def sku_from_url(url: Optional[str]) -> Optional[str]:
    """`MB222875VG` out of a product URL, or None.

    Anchored on the end of the path because a slug legitimately holds other
    digits, and case-insensitive because the site's own links are consistent
    but a user's paste may not be. The value is upper-cased so a row's id
    matches `data-pid` regardless of how the URL was typed.
    """
    if not url:
        return None
    m = _SKU_IN_URL_RE.search(_split(url).path or url)
    return m.group(1).upper() if m else None


def category_from_url(url: str) -> Optional[str]:
    """The category slug a listing URL names, or None.

    Returns the LAST path segment — `backpacks` out of `/en-fi/bags/backpacks`
    — because that is the category actually being listed; the segments above
    it are its parents.
    """
    parts = _split(url)
    segs = [s for s in (parts.path or "").split("/") if s]
    if segs and _LOCALE_RE.match("/" + segs[0]):
        segs = segs[1:]
    if not segs:
        return None
    if is_product_url(url):
        return None
    if segs[0].lower() in _NOT_A_CATEGORY:
        return None
    if segs[-1].lower() == "search":
        q = dict(parse_qsl(parts.query))
        return q.get("q") or None
    return segs[-1]


def _replace_query(url: str, updates: Dict[str, Any]) -> str:
    """Set query parameters, REPLACING rather than appending (§5).

    Existing parameters are preserved — a filtered category URL carries its
    refinements in the query and dropping them would silently widen the run
    to the unfiltered category.
    """
    parts = _split(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    for k, v in updates.items():
        if v is None:
            q.pop(k, None)
        else:
            q[k] = str(v)
    return urlunparse(parts._replace(query=urlencode(q)))


def page_url(url: str, page_num: int, page_size: int = PAGE_SIZE,
             sort: Optional[str] = None) -> str:
    """The URL for page `page_num` (1-based) of this listing.

    §7 layer 2: the address of page N is constructible, so pages after the
    first can be fetched independently and therefore concurrently. Verified
    against the site's own next-link, which builds the same `start`/`sz`
    pair — the check §7 demands before trusting a constructed URL.
    """
    if page_num < 1:
        raise ValueError(f"page_num must be >= 1, got {page_num}")
    updates: Dict[str, Any] = {
        PAGE_PARAM_START: (page_num - 1) * page_size,
        PAGE_PARAM_SIZE: page_size,
    }
    if sort:
        updates[SORT_PARAM] = SORTS.get(sort, sort)
    return _replace_query(url, updates)


def page_number_from_url(url: str, page_size: int = PAGE_SIZE) -> Optional[int]:
    """The 1-based page number a listing URL addresses, or None."""
    q = dict(parse_qsl(_split(url).query))
    raw = q.get(PAGE_PARAM_START)
    if raw is None:
        return None
    try:
        start = int(raw)
    except ValueError:
        return None
    size = int(q.get(PAGE_PARAM_SIZE) or page_size) or page_size
    return start // size + 1


def search_url(query: str, locale: str = DEFAULT_LOCALE, *,
               sort: Optional[str] = None, page_size: int = PAGE_SIZE) -> str:
    """A keyword-search listing URL for one market.

    Measured 2026-09-17: `/en-fi/search?q=fountain+pen&start=0&sz=24` answers
    200 with 24 tiles, its own `ItemList` of 24 and a result-count of 172 —
    the same markup a category serves, which is why one parser reads both.
    """
    if not _LOCALE_RE.match("/" + (locale or "")):
        raise ValueError(f"{locale!r} is not shaped like a Montblanc locale "
                         f"({{lang}}-{{cc}}, e.g. en-us)")
    base = f"https://{CANONICAL_HOST}/{locale}/search?q={quote(query)}"
    return page_url(base, 1, page_size, sort)


def category_url(path: str, locale: str = DEFAULT_LOCALE, *,
                 sort: Optional[str] = None, page_size: int = PAGE_SIZE) -> str:
    """A category listing URL from a bare category path.

    NOTE the category path is LOCALE-SPECIFIC and cannot be ported by
    swapping the locale segment — `/en-fi/bags/backpacks` is a real page and
    `/de-de/bags/backpacks` is an HTTP 404, because German addresses it as
    `/de-de/lederwaren/…` and Japanese percent-encodes its slugs. So this
    takes a path that is already right for its market, and `--category` is
    documented as a path rather than as a portable name.
    """
    if not _LOCALE_RE.match("/" + (locale or "")):
        raise ValueError(f"{locale!r} is not shaped like a Montblanc locale "
                         f"({{lang}}-{{cc}}, e.g. en-us)")
    base = f"https://{CANONICAL_HOST}/{locale}/{path.strip('/')}"
    return page_url(base, 1, page_size, sort)


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------
# The symbol table covers the markets this site actually serves. `$` is
# listed as host-resolved rather than assumed to be USD, because Montblanc
# sells in CAD, AUD, NZD, SGD, HKD and TWD too and every one of them prints a
# bare dollar sign on its own market's pages.
_CURRENCY_SYMBOLS = {
    "€": "EUR", "£": "GBP", "¥": "JPY", "₩": "KRW", "₪": "ILS",
    "zł": "PLN", "Kč": "CZK", "Ft": "HUF", "лв": "BGN", "kr": None,
}

# Symbols that cannot name themselves. A match on one of these yields the
# locale's currency where the caller supplied it, and None otherwise — never
# a plausible guess (§4's ladder, rung 4 vs rung 5).
_HOST_RESOLVED_SYMBOLS = {"$", "kr", "¥"}

# Matched longest-first so a prefix is not swallowed by a bare symbol.
_PREFIXED_SYMBOLS = {
    "CHF": "CHF", "HK$": "HKD", "NT$": "TWD", "A$": "AUD", "C$": "CAD",
    "S$": "SGD", "R$": "BRL", "AED": "AED", "SAR": "SAR", "RM": "MYR",
}

# An explicit allowlist, not a bare [A-Z]{3}: the latter matches any three
# capitals next to a number, so a nib width ("EF 850") or a model name would
# start producing phantom prices. Every entry is a real ISO 4217 code and
# every one is a currency this site quotes.
_CURRENCY_CODES = frozenset("""
    EUR USD GBP JPY CHF SEK NOK DKK PLN CZK HUF RON BGN HRK TRY
    AED SAR QAR KWD BHD OMR JOD EGP ILS ZAR
    AUD NZD CAD HKD SGD TWD CNY KRW THB MYR IDR PHP VND INR
    CLP COP PEN BRL MXN
""".split())

# Space characters used as a THOUSANDS separator. A rendered page uses a
# no-break variant so the number does not wrap: plain space, NBSP (U+00A0),
# narrow NBSP (U+202F) and thin space (U+2009) all appear on the web. Kept
# even though Montblanc groups with a comma in every market measured,
# because the cost is nothing and the failure mode is silent (§4: missing
# these parsed "1 234 €" as 234).
_GROUP_SPACES = "    "

_AMOUNT = (r"\d{1,3}(?:[" + _GROUP_SPACES + r"]\d{3})+(?:[.,]\d{1,2})?"
           r"|[\d.,]+(?:[.,]\d{1,2})?")
_PREFIXED_RE = "|".join(re.escape(s) for s in
                        sorted(_PREFIXED_SYMBOLS, key=len, reverse=True))
_BARE_RE = "|".join(re.escape(s) for s in sorted(
    set(_CURRENCY_SYMBOLS) | _HOST_RESOLVED_SYMBOLS, key=len, reverse=True))
_SPACE = "[" + _GROUP_SPACES + "]?"
_PRICE_RE = re.compile(
    r"(?:(" + _PREFIXED_RE + r"|" + _BARE_RE + r")" + _SPACE + r"(" + _AMOUNT + r")"
    r"|(" + _AMOUNT + r")" + _SPACE + r"(" + _BARE_RE + r")"
    r"|\b([A-Z]{3})" + _SPACE + r"(" + _AMOUNT + r")"
    r"|\b(" + _AMOUNT + r")" + _SPACE + r"([A-Z]{3})\b)"
)

# A percentage badge is not a price, and removing it BEFORE matching is the
# only way to be sure of that — a rejected match has still consumed the
# currency symbol belonging to the price beside it (§4). No discount badge
# was found on this site, so this guard has nothing to do today; it is kept
# because it is three lines and a "Save 10%" banner is one merchandising
# decision away.
_PERCENTAGE_RE = re.compile(
    r"[-+−]?\s*(?:%\s*\d[\d.,]*|\d[\d.,]*\s*%)")


def _normalize_amount(raw: str) -> Optional[float]:
    """Parse a price amount written in either decimal convention.

    When BOTH separators appear, 'whichever comes last is the decimal point'
    disambiguates on its own. When only one appears, that is ambiguous
    between a thousands grouping and a decimal point — and no currency this
    parser recognises has a 3-digit subunit. So a single separator followed
    by exactly 3 digits is a thousands grouping; anything else is a decimal.

    That rule is what makes the Japanese form work without a special case:
    `49,500` has three trailing digits, so it reads as 49500 rather than as
    49.5.
    """
    for space in _GROUP_SPACES:
        raw = raw.replace(space, "")

    last_dot, last_comma = raw.rfind("."), raw.rfind(",")
    if last_dot != -1 and last_comma != -1:
        norm = (raw.replace(",", "") if last_dot > last_comma
                else raw.replace(".", "").replace(",", "."))
    else:
        sep_pos = max(last_dot, last_comma)
        trailing = raw[sep_pos + 1:] if sep_pos != -1 else ""
        if len(trailing) == 3 and trailing.isdigit():
            norm = raw.replace(".", "").replace(",", "")
        else:
            norm = raw.replace(",", ".")
    try:
        return float(norm)
    except ValueError:
        return None


def _prices_in(text: str, locale_cur: Optional[str] = None
               ) -> Tuple[List[float], Optional[str]]:
    """Return ([amounts], currency_code_or_None) for all prices in `text`.

    `locale_cur` resolves a symbol that cannot name itself using the market
    whose page rendered it. That is still a guess, but a much better one: if
    the page printed a bare dollar sign at all, the visitor is being served
    that market's own currency. When the market is unknown, such a symbol
    yields a price with currency None rather than a plausible wrong code.
    """
    amounts: List[float] = []
    currency: Optional[str] = None
    prepared = _PERCENTAGE_RE.sub(" ", text)
    for m in _PRICE_RE.finditer(prepared):
        sym = m.group(1) or m.group(4)
        code = m.group(5) or m.group(8)
        if code and code not in _CURRENCY_CODES:
            # Three capitals next to a number that are not a real currency —
            # a nib width, a material code, a model name. Not a price.
            continue
        raw = m.group(2) or m.group(3) or m.group(6) or m.group(7)
        if currency is None:
            if code:
                currency = code
            elif sym in _PREFIXED_SYMBOLS:
                currency = _PREFIXED_SYMBOLS[sym]
            elif sym in _HOST_RESOLVED_SYMBOLS:
                currency = locale_cur
            else:
                currency = _CURRENCY_SYMBOLS.get(sym) or locale_cur
        amount = _normalize_amount(raw)
        if amount is not None:
            amounts.append(amount)
    return amounts, currency


def _first_price(node, locale_cur: Optional[str] = None
                 ) -> Tuple[Optional[float], Optional[str]]:
    """(amount, currency) from a price node's text, or (None, None)."""
    if node is None:
        return None, None
    amounts, currency = _prices_in(node.get_text(" ", strip=True), locale_cur)
    return (amounts[0] if amounts else None), currency


def _float_or_none(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    return _normalize_amount(text)


def _str_or_none(value: Any) -> Optional[str]:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# Page-level facts
# ---------------------------------------------------------------------------
# The site prints its own total above the grid, which is §7's layer 2 with
# the site doing the arithmetic:
#
#     <div class="result-count"><span> 280 Results </span></div>
#
# Present on page 1 only — a `start=24` slice does not repeat it — which is
# exactly the shape §7 assumes: page 1 is fetched alone and decides whether
# and how far the rest can be addressed.
# The site prints its own total above the grid, which is §7's layer 2 with
# the site doing the arithmetic:
#
#     <div class="result-count"><span> 280 Results </span></div>
#
# THE NOUN IS LOCALISED AND THE NUMBER IS NOT, so this matches the digits
# inside the element and ignores the word entirely. That is not tidiness; it
# is the second-country rule (§15 step 5) catching a real bug. The first
# version listed the nouns it expected — Results, Ergebnisse, Résultats — and
# the very first run against the US market came back with `total_results:
# None`, because en-us writes:
#
#     <div class="result-count"><span> 281 Items </span></div>
#
# "Items", not "Results". A noun list is a list of the locales someone
# thought of; the digits are the same in all of them.
#
# Present on page 1 only — a `start=24` slice does not repeat it — which is
# exactly the shape §7 assumes: page 1 is fetched alone and decides whether
# and how far the rest can be addressed.
_TOTAL_RE = re.compile(
    r'class="result-count"[^>]*>\s*<span>\s*([\d.,\u00a0\u202f ]+?)\s*[^\d<]*</span>',
    re.I)

# The site's own page-config blob, carrying the market and currency it
# believes it served. Used as the locale fallback for a bare symbol and as a
# structural signal that a real page came back.
_PRELOADED_RE = re.compile(
    r'"pg_country"\s*:\s*"([A-Z]{0,3})"\s*,\s*"pg_language"\s*:\s*"([a-z]{0,3})"'
    r'\s*,\s*"currency"\s*:\s*"([A-Z]{0,3})"')


def total_results(html: str) -> Optional[int]:
    """Montblanc's own stated result count for this listing, or None.

    Returns None on a page that does not print one (every slice after the
    first), which the caller must treat as "unknown", never as zero.
    """
    m = _TOTAL_RE.search(html or "")
    if not m:
        return None
    digits = re.sub(r"[^\d]", "", m.group(1))
    return int(digits) if digits else None


def page_currency(html: str) -> Optional[str]:
    """The currency the site says it served this page in, or None."""
    m = _PRELOADED_RE.search(html or "")
    return (m.group(3) or None) if m else None


# What the page says about ordering, and what it does NOT say.
#
# This block is written at length because the obvious reading of it is wrong
# and cost a live run to find.
#
# Montblanc's listing markup carries the sort rule in two places — a
# `"ruleId"` in the grid's config blob, and a `checked` radio in the sort
# dropdown. Both look exactly like "the ordering that was applied". Neither
# is. Measured 2026-09-17 on /en-fi/writing-instruments:
#
#     request                          ruleId                checked radio
#     ?start=0&sz=24                   recommended_sv_30d    recommended_sv_30d
#     …&srule=FA_price-ascending       recommended_sv_30d    recommended_sv_30d
#     …&srule=new_in                   recommended_sv_30d    recommended_sv_30d
#
# Always the same value, whatever was asked for — it is the CATEGORY'S
# DEFAULT rule, rendered server-side, with the active selection applied by
# JavaScript afterwards.
#
# Meanwhile the sort really is honoured: the same three requests returned
# first products MB138571, MB136806 and MB138571 respectively, and page 1
# under price-ascending shares 0 of 24 products with page 1 under the
# default. So the site APPLIES the ordering and simply does not TELL you it
# did.
#
# The first version of this file read `ruleId` as the applied sort. The
# result was a warning — "asked for price-asc and it applied 'recommended'"
# — on every single page of every single run, and a `sort` column that
# recorded the wrong ordering on every row. A check that fires on every
# healthy run teaches the reader to ignore checks, and a column that is
# confidently wrong is worse than one that is absent (§8).
#
# So: the ordering recorded on a row is the one REQUESTED, read back from
# the URL's own `srule` — a fact about the request, which is the only fact
# available. The site's default rule is still worth having, under a name
# that says what it is, and goes in the sidecar so a reader can see what a
# run WITHOUT --sort would have got.
_DEFAULT_RULE_RE = re.compile(
    r'(?:"|&quot;)ruleId(?:"|&quot;)\s*:\s*(?:"|&quot;)([^"&]+)')
_PAGE_SIZE_RE = re.compile(r'data-page-size="(\d+)(?:\.\d+)?"')
_PAGE_NUMBER_RE = re.compile(r'data-page-number="(\d+)(?:\.\d+)?"')

# `srule` value -> this repo's `--sort` name, so a row records the ordering
# in the vocabulary the CLI uses rather than in Salesforce's.
_SORT_BY_RULE = {rule: name for name, rule in SORTS.items()}


def default_sort_rule(html: str) -> Optional[str]:
    """The category's OWN default ordering, as a `--sort` name, or None.

    This is NOT the ordering that was applied — see the comment above; the
    site does not publish that. It is what a visitor arriving with no `srule`
    would get, which is worth recording so a reader can see what the run
    chose to override.

    The pattern matches BOTH spellings of the same thing, which is §20's
    "a marker must survive both encodings" earning its keep in this file:
    the blob lives inside an HTML ATTRIBUTE, so the raw bytes an HTTP client
    receives carry `&quot;ruleId&quot;:&quot;…&quot;` while a browser hands
    back the parsed, re-serialised form. A pattern written for the plain
    spelling alone returns None on every page curl fetched — which is what it
    did on the first attempt here.
    """
    m = _DEFAULT_RULE_RE.search(html or "")
    if not m:
        return None
    rule = m.group(1).strip()
    return _SORT_BY_RULE.get(rule, rule) or None


def sort_from_url(url: str) -> Optional[str]:
    """The ordering this URL ASKS for, as a `--sort` name, or None.

    None means the URL named no `srule`, so the site applied its own default
    — which `default_sort_rule` can read off the page that comes back.
    """
    rule = dict(parse_qsl(_split(url).query)).get(SORT_PARAM)
    if not rule:
        return None
    return _SORT_BY_RULE.get(rule, rule) or None


def applied_page_size(html: str) -> Optional[int]:
    """The page size the site says it used, or None."""
    m = _PAGE_SIZE_RE.search(html or "")
    return int(m.group(1)) if m else None


def applied_page_number(html: str) -> Optional[int]:
    """The 1-based page number the site says it served, or None.

    The attribute is 0-BASED while every other page number in this repo is
    1-based, which is exactly the sort of off-by-one that reaches a column
    silently. The +1 happens here, once, rather than at each caller.
    """
    m = _PAGE_NUMBER_RE.search(html or "")
    return int(m.group(1)) + 1 if m else None


def pages_available(total: Optional[int], page_size: int = PAGE_SIZE
                    ) -> Optional[int]:
    """How many pages `total` results occupy, or None when total is unknown."""
    if not total or total < 0 or page_size < 1:
        return None
    return (total + page_size - 1) // page_size


def pages_to_fetch(pages_requested: int, pages_avail: Optional[int]) -> int:
    """How many pages a run should actually attempt.

    Clamped to what the site says exists so a `--pages 50` on a two-page
    category costs two fetches rather than fifty. Where the site stated no
    count, the request stands and the run discovers the end from the data
    (§7 layer 3) — an unknown is not a zero.
    """
    pages_requested = max(1, int(pages_requested))
    if not pages_avail:
        return pages_requested
    return max(1, min(pages_requested, int(pages_avail)))


# ---------------------------------------------------------------------------
# Detection: what came back
# ---------------------------------------------------------------------------
# Every marker below was counted on four pages this address is served
# normally — the homepage, a category listing, a product page and a grid
# fragment — and every one counted ZERO on all four (§18). The two that did
# NOT count zero are `akamai` (1, 1, 1, 0) and `akamaihd` (1, 1, 1, 0), and
# they are absent from this tuple for that reason: Montblanc's own
# performance script references its CDN on every page it serves, so a bare
# `akamai` marker reports a served catalogue as a block.
#
# `cf-turnstile` is absent on purpose. §8/§19: 2Captcha's own auto-solve
# extension injects it into every page it loads over `--cdp-endpoint`, so it
# fires on good pages and has been measured MISSING from real challenges.
# `challenges.cloudflare.com` is the one that works, and it is 0 on all four
# known-good captures here.
BOT_CHALLENGE_MARKERS = (
    "errors.edgesuite.net",
    "Reference&#32;#",
    "Reference #",
    "Access Denied",
    "You don't have permission to access",
    "challenges.cloudflare.com",
    "datadome",
    "px-captcha",
    "_px_",
    "incapsula",
    "kasada",
    "awswaf",
    "g-recaptcha",
    "grecaptcha",
    "hcaptcha",
)

# The site's own asset host, referenced by everything Montblanc actually
# serves and by nothing else. Counted 2026-09-17:
#
#     homepage 634 · listing 1114 · product 524 · grid fragment 24
#
# A block page, Chromium's own network-error page and an edge interstitial
# all reference it ZERO times, which is what makes this a POSITIVE signal
# rather than another marker list (§8, §18). The grid fragment's 24 is the
# number that matters: it is the smallest real response this site produces,
# and the threshold has to sit below it.
_ASSET_MARKER = "demandware.static"

# How far into the document to normalise HTML entities before matching.
#
# §20: Akamai's refusal page reaches a parser spelled two different ways —
# `errors&#46;edgesuite&#46;net` in the raw bytes an HTTP client receives,
# and `errors.edgesuite.net` once a browser has parsed and re-serialised it.
# A literal marker matches the browser engines and silently misses the HTTP
# client. Unescaping fixes that; bounding it is what keeps the fix cheap and
# safe, since a refusal page is a few hundred bytes and unescaping 1.8 MB on
# every fetch buys nothing while risking a product title deep in a grid
# reading as a marker.
_UNESCAPE_PREFIX = 20_000


def _normalised_head(html: str) -> str:
    return _html.unescape((html or "")[:_UNESCAPE_PREFIX])


def detect_bot_challenge(html: str, url: str = "") -> Optional[str]:
    """The name of the challenge vendor on this page, or None.

    Matches against BOTH the raw markup and an entity-normalised prefix of
    it, so one marker spelling covers the HTTP client and the browsers alike.
    """
    if not html:
        return None
    haystacks = (html, _normalised_head(html))
    for marker in BOT_CHALLENGE_MARKERS:
        for hay in haystacks:
            if marker.lower() in hay.lower():
                return marker
    return None


def references_own_assets(html: str) -> int:
    """How many times this page references Montblanc's own asset host."""
    return (html or "").count(_ASSET_MARKER)


# A served page always carries several of these. The threshold is 2 rather
# than 1 so a stray mention in a script cannot vouch for a page, and rather
# than 20 so the `Search-UpdateGrid` fragment (24 references, the smallest
# real response measured) clears it with room to spare.
_MIN_ASSET_REFERENCES = 2


def detect_page_state(html: str, status: Optional[int] = None,
                      url: str = "") -> Tuple[str, Optional[str]]:
    """Classify a fetched page: (state, detail).

    States, and what each one asks the caller to do:

        content   a real page with products on it          -> parse
        empty     a real page with no products on it       -> EXIT_NO_PRODUCTS
        captcha   a challenge widget                       -> solve, then retry
        blocked   the edge refused                         -> rotate the exit
        unknown   none of the above                        -> retry

    THE SIGNALS ARE ORDERED BY HOW MUCH THEY PROVE, not by how cheap they
    are (§17's classification-order trap). An unambiguous positive — the
    site's own structured data, or its own "no results" copy — is read
    BEFORE the asset-reference heuristic, so a minimal but real page cannot
    be reported as blocked.

    A note on what this function CANNOT see. Montblanc's primary refusal is
    not a page and not a status: the edge resets the connection, which
    reaches an HTTP client as a timeout and never reaches this function at
    all. That case is classified in the engines, where the exception is,
    and the module docstring of `page_flow.py` says what to do about it.
    """
    text = html or ""

    # 1. Unambiguous positive: the site's own structured data. Nothing but a
    #    served Montblanc page carries an ItemList or a ProductGroup.
    if _ld_type_present(text):
        return "content", None

    # 1b. The same question asked of a page kind that publishes no JSON-LD.
    #
    #     §18: which page kind server-renders its grid decides what "unknown"
    #     means, and on this site the two kinds disagree. A full listing page
    #     carries an ItemList; the `Search-UpdateGrid` fragment its own front
    #     end calls carries ZERO JSON-LD blocks and 24 product tiles. Judged
    #     on structured data alone that fragment reads as "served but
    #     empty" — a correct answer reported as an empty category.
    #
    #     `data-pid` is the site's own id attribute, so a document carrying
    #     one is a document with a product in it. This is still an
    #     unambiguous positive and it belongs here, ABOVE the reference-count
    #     heuristic, per §17: order the signals by how much they prove, not
    #     by how cheap they are.
    if _TILE_ID_RE.search(text):
        return "content", None

    # 2. A status the edge owns. 403/429 are refusals; 5xx is the site.
    if status is not None:
        if status in (401, 403, 429):
            return "blocked", f"http {status}"
        if status >= 500:
            return "unknown", f"http {status}"

    # 3. A challenge widget.
    vendor = detect_bot_challenge(text, url)
    if vendor:
        return "captcha", vendor

    # 4. Structural: was this built out of Montblanc's own assets?
    refs = references_own_assets(text)
    if refs >= _MIN_ASSET_REFERENCES:
        # A real page with no ItemList on it. A hub page is the honest case:
        # `/en-fi/discover/...` is a landing page and legitimately lists no
        # products. This is EXIT_NO_PRODUCTS, not a block.
        return "empty", "served, no product data"

    if not text.strip():
        return "unknown", "empty response"
    return "blocked", "no montblanc assets referenced"


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------
# The site's own product-id attribute. Used as a positive content signal for
# the page kinds that publish no JSON-LD — see detect_page_state.
_TILE_ID_RE = re.compile(r'data-pid="(?:MB)?\d', re.I)

_LD_SCRIPT_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S)


def _ld_type_present(html: str) -> bool:
    """True when this markup carries an ItemList or a ProductGroup block."""
    for raw in _LD_SCRIPT_RE.findall(html or ""):
        if '"ItemList"' in raw or '"ProductGroup"' in raw or '"Product"' in raw:
            return True
    return False


def ld_blocks(html_or_soup) -> List[dict]:
    """Every parseable JSON-LD object in the document.

    A block that does not parse is SKIPPED rather than raising: one broken
    script on a page must not cost the other one's products. A block holding
    a list is flattened, since both shapes are legal.
    """
    if isinstance(html_or_soup, str):
        raws = _LD_SCRIPT_RE.findall(html_or_soup)
    else:
        raws = [s.string or s.get_text() or ""
                for s in html_or_soup.find_all("script",
                                               attrs={"type": "application/ld+json"})]
    out: List[dict] = []
    for raw in raws:
        try:
            data = json.loads((raw or "").strip())
        except (ValueError, TypeError):
            continue
        for node in (data if isinstance(data, list) else [data]):
            if isinstance(node, dict):
                out.append(node)
    return out


def _ld_offer(node: dict) -> dict:
    """The offer dict out of any legal `offers` shape.

    §4's table, all four rows:
      * `"offers": null` is an EXPLICIT null, so a `.get()` default does not
        apply and a naive `.get("offers", {})` returns None and raises.
      * a list, possibly holding non-dicts.
      * a bare dict.
      * absent.
    """
    offers = node.get("offers")
    if isinstance(offers, dict):
        return offers
    if isinstance(offers, list):
        for entry in offers:
            if isinstance(entry, dict):
                return entry
    return {}


def _ld_image(value: Any) -> Optional[str]:
    """The first image URL out of any legal `image` shape.

    String, list of strings, `ImageObject`, or a list of `ImageObject` — all
    four are valid schema.org and all four appear in the wild. Montblanc
    publishes a list of plain strings today; the other three cost two lines.
    """
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("url", "contentUrl"):
            got = value.get(key)
            if isinstance(got, str) and got.strip():
                return got.strip()
        return None
    if isinstance(value, list):
        for entry in value:
            got = _ld_image(entry)
            if got:
                return got
    return None


# Availability is mapped as an ALLOWLIST, not as `!= OutOfStock`, so a value
# schema.org adds later reads as "unknown" rather than silently as "in
# stock" (§20).
_IN_STOCK_VALUES = {"instock", "in_stock", "onlineonly", "limitedavailability",
                    "presale", "preorder", "backorder"}
_OUT_OF_STOCK_VALUES = {"outofstock", "soldout", "discontinued"}


def _ld_in_stock(offer: dict) -> Optional[bool]:
    """True/False/None from `offers.availability`.

    NOTE this is NOT what fills the `in_stock` column on a listing row. The
    JSON-LD said InStock on 192 of 192 listing rows while the tiles reported
    7 sold out, so on a listing the tile wins and this is used only where no
    tile exists (`--mode product`). See output_writer's module docstring.
    """
    raw = offer.get("availability")
    if not isinstance(raw, str) or not raw.strip():
        return None
    leaf = raw.rstrip("/").rsplit("/", 1)[-1].replace(" ", "").lower()
    if leaf in _IN_STOCK_VALUES:
        return True
    if leaf in _OUT_OF_STOCK_VALUES:
        return False
    return None


def _brand_name(node: dict) -> Optional[str]:
    brand = node.get("brand")
    if isinstance(brand, dict):
        return _str_or_none(brand.get("name"))
    return _str_or_none(brand)


def _item_list_products(blocks: Sequence[dict]) -> List[dict]:
    """The `Product` nodes of a listing's ItemList, in the site's own order.

    Handles `itemListElement` holding either `ListItem` wrappers (what
    Montblanc publishes) or bare products (also legal), and reads `@graph`
    as well — §4 lists "products inside @graph rather than itemListElement"
    as a shape that returns zero products SILENTLY.
    """
    out: List[dict] = []
    queue = list(blocks)
    seen_ids = set()
    while queue:
        node = queue.pop(0)
        if not isinstance(node, dict):
            continue
        node_id = id(node)
        if node_id in seen_ids:
            continue
        seen_ids.add(node_id)

        graph = node.get("@graph")
        if isinstance(graph, list):
            queue.extend(g for g in graph if isinstance(g, dict))

        if node.get("@type") == "ItemList":
            for entry in node.get("itemListElement") or []:
                if not isinstance(entry, dict):
                    continue
                item = entry.get("item") if entry.get("@type") == "ListItem" else entry
                if isinstance(item, dict) and item.get("@type") == "Product":
                    out.append(item)
    return out


def _product_group(blocks: Sequence[dict]) -> Optional[dict]:
    """The `ProductGroup` node of a detail page, or None.

    A detail page publishes ProductGroup and a listing publishes ItemList, so
    this returning None on a listing is correct and is what keeps the two
    entry points from quietly parsing each other's pages.
    """
    for node in blocks:
        if isinstance(node, dict) and node.get("@type") == "ProductGroup":
            return node
    return None


# ---------------------------------------------------------------------------
# Tiles: the site's own tracking payload
# ---------------------------------------------------------------------------
_TILE_PAYLOAD_RE = re.compile(
    r'data-tracking-click-event="select_item"\s+'
    r'data-tracking-event-payload="([^"]+)"')


def tile_facts(html: str) -> Dict[str, Dict[str, Any]]:
    """{sku: {fields}} read from every tile's own analytics payload.

    The payload is HTML-escaped JSON sitting in an attribute, so it is read
    with a regex over the raw markup rather than through the soup: BeautifulSoup
    would unescape it for us, but the engines hand this function a string and
    building a second soup for one attribute is wasteful on a 930 KB page.

    A payload that does not parse is skipped. It is one tile's enrichment,
    never the row itself — the JSON-LD already produced the row — so failing
    soft here loses a colour, not a product.
    """
    facts: Dict[str, Dict[str, Any]] = {}
    for raw in _TILE_PAYLOAD_RE.findall(html or ""):
        try:
            payload = json.loads(_html.unescape(raw))
        except (ValueError, TypeError):
            continue
        items = (payload.get("ecommerce") or {}).get("items") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            sku = _str_or_none(item.get("item_id"))
            if not sku:
                continue
            facts[sku.upper()] = item
    return facts


# One variant attribute on this site is published as an internal constant
# rather than as the value, and only one.
#
# A fountain pen's nib widths come back as `EF`, `F`, `B`, `BB`, `OBB` — and
# the MEDIUM nib comes back as `MTB_WRITING_INSTRUMENT_M`. Measured across
# three product pages (2026-09-17): 3 occurrences of the long form against
# clean two-letter codes for every other width.
#
# Written through, a consumer grouping variants by nib width sees the M nib
# as a different KIND of thing from its siblings — the one value that will
# not join. Stripping the `MTB_<WORDS>_` prefix leaves `M` and makes the set
# consistent.
#
# Deliberately narrow: it only fires on the `MTB_…_` shape, so any other
# value the site publishes passes through untouched rather than being
# guessed at.
_INTERNAL_CODE_RE = re.compile(r"^MTB(?:_[A-Z]+)+_([A-Z0-9]+)$")


def _variant_value(value: Any) -> Optional[str]:
    """A variant attribute, with the site's internal constant unwrapped."""
    text = _na(value)
    if text is None:
        return None
    m = _INTERNAL_CODE_RE.match(text)
    return m.group(1) if m else text


def _na(value: Any) -> Optional[str]:
    """The payload writes an absent attribute as the literal string "N/A"."""
    text = _str_or_none(value)
    if text is None or text.upper() == "N/A":
        return None
    return text


# `item_available` -> in_stock, as an allowlist. Measured over 384 tiles:
# `available` 377, `soldout` 7. Anything else is None rather than True, so a
# third value the site introduces reads as unknown.
_TILE_STOCK = {"available": True, "soldout": False}


def _tile_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = _str_or_none(value)
    if text is None or text.upper() == "N/A":
        return None
    low = text.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    return None


# ---------------------------------------------------------------------------
# DOM price fallback
# ---------------------------------------------------------------------------
def _tile_nodes(soup) -> Dict[str, Any]:
    """{sku: tile_element} for every product tile in the document.

    The tile is found by `data-pid`, which is the site's own id attribute and
    therefore immune to the class-name churn §4 warns about. No ancestor walk
    is needed and none is done: `div.product[data-pid]` already covers
    exactly one product, so the "junk-link data theft" failure mode — a scope
    that widens one level too far and reports a neighbour's price — cannot
    arise here.
    """
    out: Dict[str, Any] = {}
    for node in soup.select("[data-pid]"):
        sku = (node.get("data-pid") or "").strip().upper()
        if sku and sku not in out:
            out[sku] = node
    return out


def dom_price(tile, locale_cur: Optional[str] = None
              ) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    """(amount, currency, price_source) read from a tile's price node.

    Two shapes, and telling them apart is the whole point:

        <span class="sales">&euro; 2,000.00</span>
            -> the product's price.                  price_source "dom"

        <span class="range"><span class="from-range-text">From </span>
          <span class="value" content="515.00">&euro; 515.00</span></span>
            -> the MINIMUM over a variant group.     price_source "dom_range"

    The `content` attribute is preferred over the rendered text because it is
    the site's own normalised value; the text is parsed when it is absent.
    """
    if tile is None:
        return None, None, None

    price_root = tile.select_one(".price") or tile
    currency = None

    range_node = price_root.select_one(".range")
    if range_node is not None:
        value_node = range_node.select_one(".value")
        _, currency = _first_price(value_node or range_node, locale_cur)
        raw = (value_node.get("content") if value_node is not None else None)
        amount = _float_or_none(raw)
        if amount is None:
            amount, currency2 = _first_price(value_node or range_node, locale_cur)
            currency = currency or currency2
        if amount is not None:
            return amount, currency or locale_cur, "dom_range"

    sales_node = price_root.select_one(".sales") or price_root.select_one(
        ".price-container")
    if sales_node is not None:
        raw = sales_node.get("content")
        amount = _float_or_none(raw)
        text_amount, text_cur = _first_price(sales_node, locale_cur)
        if amount is None:
            amount = text_amount
        currency = text_cur or currency
        if amount is not None:
            return amount, currency or locale_cur, "dom"

    return None, None, None


# ---------------------------------------------------------------------------
# Row building
# ---------------------------------------------------------------------------
def _absolute(url: Optional[str], base_url: str = "") -> str:
    """Make a product URL absolute.

    The listing's JSON-LD publishes `url` as a ROOT-RELATIVE path
    ("/en-fi/…-MB222875VG.html") while a ProductGroup's variants publish it
    absolute. Both go through here so every row carries something a reader
    can open.
    """
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    if base_url:
        return urljoin(base_url, url)
    return urljoin(f"https://{CANONICAL_HOST}/", url)


_BASE_ID_RE = re.compile(r"^(MB\d+)", re.I)


def base_id(sku: Optional[str]) -> Optional[str]:
    """`MB132288` out of `MB132288M`, `MB132288VG` or `MB132288`.

    The digits are the product; the trailing letters are a variant suffix.
    """
    if not sku:
        return None
    m = _BASE_ID_RE.match(sku.strip())
    return m.group(1).upper() if m else None


def _join_tile(key: Optional[str], by_sku: Dict[str, Any],
               by_base: Dict[str, Any]) -> Any:
    """The tile for this JSON-LD row: exact sku first, base id second.

    The second stage is not belt-and-braces; it closes a measured hole. The
    ItemList and the tile can spell the SAME product with two different
    variant suffixes on one page:

        JSON-LD url   …-MB132288M.html     -> sku MB132288M
        tile          data-pid="MB132288VG"

    Joined on the full sku those two never meet, so the row loses everything
    the tile carries — on one live run that was 3 of 71 rows silently
    missing `image_url`, `in_stock`, `collection` and `color` while the row
    itself looked fine. A column that is quietly empty on a few rows is
    exactly the failure this family is worst at noticing.

    The base join is applied ONLY when exactly one tile on the page shares
    that base. Where two do, the page is showing two variants of one product
    and picking either would be a guess — so the row keeps the JSON-LD's own
    answer and the tile columns stay None, which is the honest result (§8).
    """
    if not key:
        return None
    exact = by_sku.get(key)
    if exact is not None:
        return exact
    base = base_id(key)
    return by_base.get(base) if base else None


def _by_base(mapping: Dict[str, Any]) -> Dict[str, Any]:
    """{base_id: value} for the base ids that are UNAMBIGUOUS on this page."""
    counts: Dict[str, int] = {}
    first: Dict[str, Any] = {}
    for key, value in mapping.items():
        base = base_id(key)
        if not base:
            continue
        counts[base] = counts.get(base, 0) + 1
        first.setdefault(base, value)
    return {b: v for b, v in first.items() if counts.get(b) == 1}


def parse_row(node: dict, *, base_url: str = "", page: Optional[int] = None,
              position: Optional[int] = None, mode: str = "category",
              locale: Optional[str] = None, sort: Optional[str] = None,
              tile: Any = None, facts: Optional[Dict[str, Any]] = None,
              locale_cur: Optional[str] = None) -> Product:
    """One JSON-LD `Product` node (plus its tile, where there is one) -> a row.

    The JSON-LD is the spine and the tile is enrichment. Where the two can
    both answer a question the JSON-LD wins — it is one object per product
    and cannot be mis-scoped — with the single, measured exception of stock,
    where the tile is the only source that varies.
    """
    facts = facts or {}
    url = _absolute(node.get("url") or _ld_offer(node).get("url"), base_url)
    sku = sku_from_url(url) or _str_or_none(facts.get("item_id"))

    offer = _ld_offer(node)
    price = _float_or_none(offer.get("price"))
    currency = _str_or_none(offer.get("priceCurrency"))
    price_source: Optional[str] = "jsonld" if price is not None else None

    if price is None:
        amount, dom_cur, source = dom_price(tile, locale_cur)
        if amount is not None:
            price, price_source = amount, source
            currency = currency or dom_cur

    # A price with no currency is a number without units. The market's own
    # currency is the last resort and is still a fact about the page rather
    # than a guess about the number.
    if price is not None and not currency:
        currency = locale_cur

    return Product(
        url=url,
        sku=sku,
        title=_str_or_none(node.get("name")),
        brand=_brand_name(node),
        price=price,
        currency=currency,
        in_stock=_TILE_STOCK.get(str(facts.get("item_available") or "").lower()),
        # JSON-LD first, tile second. The fallback is not decorative: the
        # ItemList entry for a range-priced variant group is THIN — it omits
        # `price` and it omits `image` — while the tile's tracking payload
        # carries `product_image_url` on 100% of tiles measured. Without this
        # line, 3 of 71 rows on one live run came back imageless for the same
        # reason 14 of 192 came back priceless.
        image_url=_ld_image(node.get("image")) or _na(
            facts.get("product_image_url")),
        category=_na(facts.get("item_category")) or (
            category_from_url(base_url) if base_url else None),
        price_source=price_source,
        page=page,
        position=position,
        mode=mode,
        locale=locale,
        base_sku=_na(facts.get("item_reference")),
        collection=_na(facts.get("item_collection")),
        sub_collection=_na(facts.get("item_sub_collection")),
        color=_na(facts.get("item_material_color")),
        size=_variant_value(facts.get("item_size")),
        special_edition=_tile_bool(facts.get("item_special_edition")),
        variant_of=None,
        sort=sort,
    )


def parse_products(html: str, base_url: str = "", *, page: int = 1,
                   mode: str = "category", sort: Optional[str] = None
                   ) -> List[Product]:
    """Rows from a CATEGORY or SEARCH listing page.

    JSON-LD first, in the site's own order, with the tile joined on by sku.
    Returns [] on a detail page, and that is deliberate rather than a gap:
    §20's trap is a listing parser silently returning zero on a page that
    publishes `ProductGroup`, so the smoke suite pins this exact behaviour
    and `parse_product_detail` is the entry point for that page kind.
    """
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    blocks = ld_blocks(soup)

    # REFUSE a detail page, and refuse it on the page's OWN evidence.
    #
    # This guard is not defensive tidiness; it closes a measured hole. A
    # product page publishes no ItemList, so the JSON-LD path correctly
    # returns nothing — and the tile fallback below then harvests the "you
    # may also like" carousel instead. Measured on the Meisterstück pen
    # page: 17 recommendation tiles (ink bottles, refills, a pouch) plus the
    # product itself, returned as **18 products with prices**, in silence.
    #
    # That is §4's junk-link data theft one level up: not a neighbour's price
    # on the right product, but a neighbour's products on the wrong page,
    # and nothing in the output says so. `parse_product_detail` is the entry
    # point for this page kind.
    #
    # The signal is the page's own `ProductGroup` block rather than the URL,
    # because a caller may pass no `base_url` at all and the markup is the
    # thing actually being parsed. The URL check comes second, for the
    # ProductGroup-less case.
    if _product_group(blocks) is not None:
        return []
    if base_url and is_product_url(base_url):
        return []

    nodes = _item_list_products(blocks)

    locale = locale_from_url(base_url) if base_url else None
    locale_cur = page_currency(html)
    facts = tile_facts(html)
    tiles = _tile_nodes(soup)
    facts_by_base = _by_base(facts)
    tiles_by_base = _by_base(tiles)

    rows: List[Product] = []
    for index, node in enumerate(nodes, start=1):
        url = _absolute(node.get("url") or _ld_offer(node).get("url"), base_url)
        sku = sku_from_url(url)
        key = (sku or "").upper()
        rows.append(parse_row(
            node, base_url=base_url, page=page, position=index, mode=mode,
            locale=locale, sort=sort,
            tile=_join_tile(key, tiles, tiles_by_base),
            facts=_join_tile(key, facts, facts_by_base) or {},
            locale_cur=locale_cur))

    if rows:
        return rows

    # The JSON-LD path found nothing. Fall back to the site's own id
    # attribute — §4's "a better anchor than any class and nearly as good as
    # structured data" — before giving up, because a page that renders tiles
    # and publishes no ItemList is a real shape (the `Search-UpdateGrid`
    # fragment is exactly that: 24 tiles, 0 JSON-LD blocks).
    return _parse_tiles_only(soup, html, base_url=base_url, page=page,
                             mode=mode, sort=sort, locale=locale,
                             locale_cur=locale_cur)


def _parse_tiles_only(soup, html: str, *, base_url: str, page: int,
                      mode: str, sort: Optional[str], locale: Optional[str],
                      locale_cur: Optional[str]) -> List[Product]:
    """Fallback: rows built from tiles alone, with no structured data.

    This is the path the `Search-UpdateGrid` fragment needs, and the path a
    JSON-LD removal would leave the scraper standing on. It anchors on
    `data-pid` and on the product URL pattern, never on a class name.
    """
    facts = tile_facts(html)
    rows: List[Product] = []
    position = 0
    for sku, tile in _tile_nodes(soup).items():
        href = None
        for anchor in tile.select("a[href]"):
            candidate = anchor.get("href") or ""
            if _PRODUCT_PATH_RE.search(candidate):
                href = candidate
                break
        if not href:
            continue
        position += 1
        item = facts.get(sku) or {}
        url = _absolute(href, base_url)
        amount, dom_cur, source = dom_price(tile, locale_cur)
        title = _na(item.get("item_name"))
        if not title:
            name_node = tile.select_one(".product-name, .pdp-link, .link")
            title = name_node.get_text(" ", strip=True) if name_node else None
        rows.append(Product(
            url=url,
            sku=sku_from_url(url) or sku,
            title=title,
            brand=_na(item.get("item_brand")),
            price=amount,
            currency=dom_cur or locale_cur,
            in_stock=_TILE_STOCK.get(str(item.get("item_available") or "").lower()),
            image_url=_na(item.get("product_image_url")),
            category=_na(item.get("item_category")) or (
                category_from_url(base_url) if base_url else None),
            price_source=source,
            page=page,
            position=position,
            mode=mode,
            locale=locale,
            base_sku=_na(item.get("item_reference")),
            collection=_na(item.get("item_collection")),
            sub_collection=_na(item.get("item_sub_collection")),
            color=_na(item.get("item_material_color")),
            size=_variant_value(item.get("item_size")),
            special_edition=_tile_bool(item.get("item_special_edition")),
            variant_of=None,
            sort=sort,
        ))
    return rows


@dataclass
class ListingPage:
    """One listing page: its rows, plus what the SITE said about the run.

    The page-level facts are kept beside the rows rather than repeated down a
    column.

    `sort` is the ordering the RUN ASKED FOR, recovered from the URL's own
    `srule`. That is a deliberate retreat from what this class first tried to
    do: the site does not publish which ordering it applied (see
    `default_sort_rule`), so the request is the only fact available and
    claiming more would be a guess in a column. `site_default_sort` records
    what a visitor with no `srule` would have got, which is the honest,
    useful half of the same question.

    `total_results` is the site's own `result-count`, printed above the grid
    on page 1 only. On a later slice it is None, and None means UNKNOWN —
    never zero. Treating a missing count as zero would cap every run after
    page 1 at no pages at all.
    """
    rows: List[Product]
    total_results: Optional[int] = None
    pages_available: Optional[int] = None
    page_size: Optional[int] = None
    page_number: Optional[int] = None
    sort: Optional[str] = None
    site_default_sort: Optional[str] = None


def parse_listing(html: str, base_url: str = "", *, page: int = 1,
                  mode: str = "category", sort: Optional[str] = None,
                  page_size: int = PAGE_SIZE) -> ListingPage:
    """`parse_products`, with the page-level facts the engines plan from.

    The engines call this rather than `parse_products` directly, so that the
    site's own arithmetic reaches the sidecar by default instead of only
    when someone remembers to read it.
    """
    # The ordering on the row is the one the URL asked for, preferred over
    # whatever the caller passed, because the URL is what was actually sent.
    # They agree on every path in this repo; the URL wins so a hand-built
    # URL cannot disagree with a flag in silence.
    requested = sort_from_url(base_url) or sort
    rows = parse_products(html, base_url, page=page, mode=mode, sort=requested)
    applied_size = applied_page_size(html) or page_size
    total = total_results(html)

    return ListingPage(
        rows=rows,
        total_results=total,
        pages_available=pages_available(total, applied_size),
        page_size=applied_size,
        page_number=applied_page_number(html) or page,
        sort=requested,
        site_default_sort=default_sort_rule(html),
    )


def parse_product_detail(html: str, base_url: str = "", *,
                         mode: str = "product", sort: Optional[str] = None
                         ) -> List[Product]:
    """Rows from a PRODUCT page — one per variant.

    A detail page publishes `ProductGroup` with `hasVariant`, and each
    variant is a real thing to buy with its own sku and its own price. A
    sixteen-variant pen is therefore sixteen rows, and `variant_of` carries
    the group's sku so they fold back together.

    Where a group publishes no variants at all, one row is emitted for the
    group itself rather than none — the page named a product and the caller
    asked about it.

    Read `url` on each variant and NOT `offers.url`: every variant's
    `offers.url` is the group's own page, so the latter makes sixteen rows
    share one address while everything else about them looks right.
    """
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    group = _product_group(ld_blocks(soup))
    if group is None:
        return []

    locale = locale_from_url(base_url) if base_url else None
    locale_cur = page_currency(html)
    group_sku = _str_or_none(group.get("sku")) or sku_from_url(base_url)
    group_offer = _ld_offer(group)
    group_brand = _brand_name(group)
    group_image = _ld_image(group.get("image"))
    category = _breadcrumb_category(ld_blocks(soup)) or category_from_url(base_url)

    variants = [v for v in (group.get("hasVariant") or []) if isinstance(v, dict)]
    rows: List[Product] = []

    if not variants:
        rows.append(_detail_row(
            group, group_offer, base_url=base_url, locale=locale,
            locale_cur=locale_cur, brand=group_brand, image=group_image,
            category=category, variant_of=None, position=1, mode=mode,
            sort=sort, fallback_sku=group_sku))
        return rows

    for index, variant in enumerate(variants, start=1):
        rows.append(_detail_row(
            variant, _ld_offer(variant), base_url=base_url, locale=locale,
            locale_cur=locale_cur, brand=_brand_name(variant) or group_brand,
            image=_ld_image(variant.get("image")) or group_image,
            category=category, variant_of=group_sku, position=index,
            mode=mode, sort=sort, fallback_sku=None))
    return rows


def _detail_row(node: dict, offer: dict, *, base_url: str,
                locale: Optional[str], locale_cur: Optional[str],
                brand: Optional[str], image: Optional[str],
                category: Optional[str], variant_of: Optional[str],
                position: int, mode: str, sort: Optional[str],
                fallback_sku: Optional[str]) -> Product:
    # `url` first, `offers.url` only as a fallback — see the caller.
    url = _absolute(node.get("url") or offer.get("url") or base_url, base_url)
    sku = _str_or_none(node.get("sku")) or sku_from_url(url) or fallback_sku
    price = _float_or_none(offer.get("price"))
    currency = _str_or_none(offer.get("priceCurrency")) or (
        locale_cur if price is not None else None)
    return Product(
        url=url,
        sku=sku,
        title=_str_or_none(node.get("name")),
        brand=brand,
        price=price,
        currency=currency,
        # No tile exists on a detail page, so this is the JSON-LD's own
        # availability — the only source there is here. It said InStock on
        # every variant measured; see output_writer's docstring for why that
        # is treated as weaker evidence than a listing tile's.
        in_stock=_ld_in_stock(offer),
        image_url=image,
        category=category,
        price_source="jsonld" if price is not None else None,
        page=1,
        position=position,
        mode=mode,
        locale=locale,
        base_sku=variant_of,
        collection=None,
        sub_collection=None,
        color=_variant_value(node.get("color")),
        size=_variant_value(node.get("size")),
        special_edition=None,
        variant_of=variant_of,
        sort=sort,
    )


def _breadcrumb_category(blocks: Sequence[dict]) -> Optional[str]:
    """The last named crumb before the product itself, or None.

    A product page's BreadcrumbList ends with the product, so the category is
    the one before it.
    """
    for node in blocks:
        if not isinstance(node, dict) or node.get("@type") != "BreadcrumbList":
            continue
        names: List[str] = []
        for entry in node.get("itemListElement") or []:
            if not isinstance(entry, dict):
                continue
            item = entry.get("item")
            name = (entry.get("name")
                    or (item.get("name") if isinstance(item, dict) else None))
            name = _str_or_none(name)
            if name:
                names.append(name)
        if len(names) >= 2:
            return names[-2]
        if names:
            return names[-1]
    return None

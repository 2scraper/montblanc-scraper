"""
output_writer.py
-----------------
Shared row models + JSON/CSV writers used by all three scrapers.

Three modes, one row shape
--------------------------
    --mode category   /{locale}/{category-path}?start=N&sz=24   a category listing
    --mode search     /{locale}/search?q=…&start=N&sz=24        a keyword search
    --mode product    /{locale}/{slug}-MB{id}.html              one product page

All three yield the SAME class. Montblanc publishes one leaf — a PRODUCT —
and a category listing, a search result and a product page are three views
of it. The first two are ways of SELECTING products; the third is one
product described more fully, and on this site "more fully" means its
variants, because a detail page publishes `ProductGroup.hasVariant` with one
entry per size or nib width, each carrying its own sku and its own price.

So `--mode product` emits one row PER VARIANT, not one row per page. A
sixteen-variant fountain pen is sixteen rows, which is the only shape that
can answer "what does the Medium nib cost" — the question the detail page
exists to answer. `variant_of` carries the group's sku so the rows can be
folded back together.

Columns this site does NOT have, and the measurement behind each
--------------------------------------------------------------
CLAUDE.md §9: a column null on every row of every run should not exist, and
removing one needs the measurement written down so someone can put it back
with a better one. Counted 2026-09-17 over eight category captures on
`en-fi` (384 tiles), plus two product pages:

    original_price / discount_pct    0 strike-price nodes, 0 `discount`,
    lowest_price_30d                 0 `price-standard`, 0 `was-price` on
                                     listings OR product pages. Montblanc is
                                     a full-price luxury house: `/en-fi/sale`
                                     and `/en-fi/outlet` are both HTTP 404,
                                     and there is no reduced price anywhere
                                     to disclose. The EU Omnibus 30-day-low
                                     column that mediamarkt-scraper needs is
                                     therefore absent here rather than null:
                                     no reduction, no disclosure.

    rating / review_count            0 `aggregateRating`, 0 `ratingValue`,
                                     0 `reviewCount`, 0 `class="rating` on a
                                     listing, a bag page and a pen page. The
                                     site carries no review system at all.

    ean / gtin / mpn                 0 `gtin`, 0 `mpn` in any JSON-LD block.
                                     The `ean` substring matches only inside
                                     ordinary words ("clean", "meaning").
                                     Montblanc identifies by its own MB id.

    engraved                         `item_engraved` was False on 384 of 384
                                     tiles — a constant, which is the same
                                     defect as a null column.

`in_stock` comes from the TILE, not from the JSON-LD
----------------------------------------------------
This one is worth stating loudly because the obvious source is wrong, and
wrong in the direction that looks right.

`offers.availability` in the listing's JSON-LD was `schema.org/InStock` on
**192 of 192** rows across four categories. Read as truth it makes `in_stock`
a constant — CLAUDE.md §20's "a column you never saw take its other value".

The tile's own tracking payload disagrees, and it is the one that varies:
`item_available` was `available` on 377 and **`soldout` on 7** of the same
384 tiles. So the JSON-LD's availability is a template default on this site,
not a fact about the item, and `parse_row` reads stock from the tile.

Where only the JSON-LD is available (an engine that captured no tile markup,
or `--mode product`, whose ProductGroup has no tile), `in_stock` is left
None rather than filled with the default — an unknown recorded as unknown
(§8: never present a guess as a fact).

Availability is mapped as an ALLOWLIST (`available` -> True, `soldout` ->
False, anything else -> None) so a value the site adds later reads as
"unknown" instead of silently as "in stock".
"""

import csv
import json
from dataclasses import dataclass, asdict, field, fields
from datetime import datetime, timezone
from typing import Optional, List, Set, Sequence, Any, Type


# The hostname a row came from. Montblanc serves all 72 of its locales from
# ONE host — `www.montblanc.com` — with the market in the PATH (`/en-fi/`,
# `/ja-jp/`) rather than in a TLD, so this column is `montblanc.com` on
# every row of every run. It is kept because the family's schema has it in
# this position and consumers read the columns by name across repos;
# `locale` below is what actually varies.
SOURCE_DEFAULT = "montblanc.com"


@dataclass
class Product:
    # ---- the family prefix, byte-identical and in order (§9) ------------
    source: str = SOURCE_DEFAULT
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # The absolute product URL. On a listing the JSON-LD publishes this as a
    # ROOT-RELATIVE path ("/en-fi/…-MB222875VG.html"); the parser joins it
    # onto the host so every row carries something a reader can open.
    url: str = ""
    # Montblanc's own id, as it appears at the end of the product URL and in
    # `data-pid`: "MB222875VG".
    #
    # The trailing letters are a VARIANT suffix and they matter. Measured
    # shapes across three captures: `MB\d+` (46), `MB\d+VG` (16), `MB\d+B`
    # (1), plus `MB\d+M` on the range-priced groups. `item_reference` on the
    # same tile is the bare `MB222875` — the base product — and it is kept
    # separately as `base_sku`, because two colourways of one bag share a
    # base and are two different things to buy.
    sku: Optional[str] = None
    title: Optional[str] = None

    # ---- commerce -------------------------------------------------------
    # `brand.name` from the JSON-LD, which is the literal string "montblanc"
    # on every row — this is a single-brand house. Kept because the family
    # schema has it here and a consumer joining across repos reads it by
    # name; it is a fact about the site rather than a null column.
    brand: Optional[str] = None
    price: Optional[float] = None
    # Never defaulted. `offers.priceCurrency` is a written ISO code on this
    # site (EUR / USD / GBP / JPY …) — the most trustworthy source in §4's
    # ladder — and where it is absent the column is None.
    currency: Optional[str] = None
    # See the module docstring: read from the tile's `item_available`, as an
    # allowlist, and left None when only JSON-LD was available.
    in_stock: Optional[bool] = None
    image_url: Optional[str] = None
    # The site's own `item_category` ("Fountain Pens"), falling back to the
    # category segment recovered from the URL.
    category: Optional[str] = None

    # ---- provenance and ordering ---------------------------------------
    # WHICH node the price was read from, per §8. There is no discount chain
    # on this site and therefore no DOM overlay to reconcile against (§4
    # says delete the overlay rather than port it), so this records the
    # source rather than a confirmation:
    #
    #   "jsonld"     `offers.price` in the listing's ItemList, or the
    #                variant's own offer in `--mode product`.
    #   "dom"        the tile's `<span class="sales">` — used where the
    #                JSON-LD carried no price.
    #   "dom_range"  the tile's `<span class="range">`, whose text reads
    #                "From € 515.00". This is a MINIMUM over the group's
    #                variants, not the price of a thing you can buy, and it
    #                is exactly where the JSON-LD price is absent: 14 of 192
    #                listing rows had no `offers.price`, and every one of
    #                them was a range. A consumer must be able to exclude
    #                these from a price comparison, hence its own value
    #                rather than a quiet "dom".
    price_source: Optional[str] = None
    # `page` is 1-based over the run; `position` is the item's rank WITHIN
    # its page and restarts at 1 on each one. Neither is unique alone —
    # assert the PAIR is unique across a multi-page run (§18).
    page: Optional[int] = None
    position: Optional[int] = None
    # Which mode produced the row. The repo no longer implies it — three
    # modes share this schema — and `diff_runs.py` refuses to compare two
    # runs whose modes it cannot line up (§9).
    mode: Optional[str] = None

    # ---- site-specific, at the end (§9) ---------------------------------
    # The market this row was read from ("en-fi"), because the PRICE IS SET
    # PER MARKET rather than converted, and the column is the only thing
    # that says which market a number belongs to. Measured on one backpack,
    # 2026-09-17: EUR 2000 on en-fi, EUR 1900 on de-de, GBP 1700 on en-gb,
    # USD 1990 on en-us. Two of those share a currency and still disagree,
    # so `currency` alone cannot stand in for this.
    locale: Optional[str] = None
    # `item_reference` — the site's own base product id.
    #
    # It is NOT always different from `sku`, and the measurement is the
    # point: across three listing captures it EQUALLED `sku` on 37 tiles and
    # differed on 11. Where it differs it is the sku with its variant suffix
    # removed — `MB132446M` -> `MB132446` — which is exactly the set of
    # range-priced groups. So it answers "which product is this a variant
    # of" on a listing, where nothing else does, and repeats `sku`
    # harmlessly everywhere else.
    base_sku: Optional[str] = None
    # Montblanc's own product hierarchy, from the tile's tracking payload.
    # Coverage over 144 tiles on six categories: collection 96%,
    # sub_collection 93%, color 94%. Populated on a listing and absent on a
    # ProductGroup, which publishes `color` on each variant instead.
    collection: Optional[str] = None
    sub_collection: Optional[str] = None
    color: Optional[str] = None
    # 12% on listings — watch case widths ("43 mm") and belt sizes ("35") —
    # and 100% in `--mode product`, where it is the axis `hasVariant` varies
    # along and the reason to open a detail page at all. Kept for the second
    # number, not the first.
    size: Optional[str] = None
    # `item_special_edition`: False on 364 tiles, True on 13, absent on 7.
    special_edition: Optional[bool] = None
    # The group sku a `--mode product` variant row belongs to; None on a
    # listing row, where the row already IS the thing the listing named.
    variant_of: Optional[str] = None
    # WHICH ORDERING Montblanc was asked for. This belongs on the row rather
    # than only in the sidecar, because it changes which products are in the
    # file at all, not merely their order: page 1 of `writing-instruments`
    # under the site's default `recommended_sv_30d` and under
    # `FA_price-ascending` shared **0 of 24** products. Two runs that differ
    # only here are not comparable, and without the column nothing says so.
    sort: Optional[str] = None


# Row classes by --mode, so an engine maps its mode to a schema in one place.
# All three are Product here; the mapping exists so adding a mode later is a
# one-line change rather than a search for every place that assumed Product.
ROW_CLASS_BY_MODE = {"category": Product, "search": Product, "product": Product}

# Modes whose rows are one-per-sku, and therefore safe to dedupe on `sku` and
# to hand to diff_runs.py.
#
# All three qualify, but `product` qualifies for a different reason and it is
# worth saying why. A listing names each product once, so its `sku` is unique
# by construction. A product page emits one row per VARIANT, and a variant's
# `sku` ("MB132464") is distinct from its siblings' and from the group's
# ("MB132460") — 16 distinct variant skus on one pen, measured. So the key is
# still unique; it just identifies something one level finer.
UNIQUE_BY_SKU_MODES = ("category", "search", "product")


def dedupe_by_key(rows: Sequence[Any], seen: Set[str], key: str = "sku") -> List[Any]:
    """Drop rows whose key already appeared earlier in this same run.

    `seen` is mutated in place, so callers thread the same set across pages —
    a repeated page then re-parses without duplicating its rows into the
    final output. On Montblanc this should fire RARELY on a healthy run, and
    that is measured: pages 1 and 2 of `writing-instruments` (start=0 and
    start=24) shared **0** of 48 skus, and every page's 24 `data-pid`s were
    distinct.

    A non-zero drop count here therefore means a page was genuinely
    re-fetched — or that the run changed `--sort` mid-flight, which it
    cannot, because the ordering is fixed for the whole run and recorded on
    every row.

    A row with no key is always kept: there is nothing to check a duplicate
    against, and dropping it would be a silent data loss rather than a
    duplicate removal.

    All three of this repo's modes are one row per `sku`, so `key` is never
    overridden here — the parameter exists because the rest of the family
    shares this function and one of them needs it.
    """
    fresh = []
    for r in rows:
        val = getattr(r, key, None)
        if val is None or val not in seen:
            if val is not None:
                seen.add(val)
            fresh.append(r)
    return fresh


# Kept under its old name: the engines and smoke tests in this family all
# call it, and a listing run does dedupe by sku.
def dedupe_by_sku(rows: Sequence[Any], seen: Set[str]) -> List[Any]:
    return dedupe_by_key(rows, seen, key="sku")


# CSV cannot hold a list. Joining with " | " keeps the cell readable in a
# spreadsheet and round-trippable by splitting on the same separator; the
# JSON output keeps the real list, so nothing is lost for a consumer that
# wants structure. `repr()` of a Python list (the default if this is not
# handled) is neither readable nor parseable by anything but Python.
LIST_CSV_SEPARATOR = " | "


def _csv_value(v: Any) -> Any:
    if isinstance(v, (list, tuple)):
        return LIST_CSV_SEPARATOR.join(str(x) for x in v)
    return v


def write_json(rows: Sequence[Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in rows], f, ensure_ascii=False, indent=2)


def write_csv(rows: Sequence[Any], path: str, row_cls: Type = Product) -> None:
    # An empty result still gets the header row. A zero-byte file makes a
    # consumer fail on read (no columns to parse) instead of reading a valid
    # table with zero rows — and "an empty result is still a well-formed
    # result" is the same principle as `save` refusing to overwrite good data.
    #
    # The header comes from `row_cls`, not from the first row, so an empty
    # run still writes the columns of the mode that produced it.
    fieldnames = [f.name for f in fields(row_cls)]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: _csv_value(v) for k, v in asdict(r).items()})


# Exit code used when a run completes but produced nothing. Distinct from 1
# (crash) so a caller can tell "ran, found nothing" from "blew up".
EXIT_NO_PRODUCTS = 4

# Exit code for a run blocked by a bot-check/challenge page before parsing
# even started — distinct from EXIT_NO_PRODUCTS so a caller can tell "the
# search genuinely matched nothing" from "something stood between us and the
# content". See product_parser.detect_bot_challenge.
#
# On Montblanc this code does NOT cover a listing that simply ran out. A
# `start=` past the end of the catalogue answers HTTP 200 with zero product
# tiles and no error — `writing-instruments` states 280 results and
# `start=288` is an empty, perfectly valid page. That is EXIT_NO_PRODUCTS at
# worst and normally just the end of pagination: the request was served
# exactly as asked. Reporting it as blocked would send a user hunting for a
# proxy problem that does not exist.
#
# What EXIT_BLOCKED means here is Akamai, and Montblanc's refusal has a
# shape this family had not met before: it is NOT a status code and NOT a
# page. The edge kills the connection (measured 2026-09-17 from a Hetzner
# address, which is otherwise served normally):
#
#   curl     `curl: (92) HTTP/2 stream 1 was not closed cleanly:
#            INTERNAL_ERROR (err 2)` — no status, no body, no markers.
#   requests `ReadTimeout` after the full timeout, which is
#            INDISTINGUISHABLE from a slow network unless you know.
#
# The trigger is an explicit client-library denylist on the User-Agent, not
# behaviour and not the address. Measured on one URL, one address, one
# minute — REFUSED: no UA at all, `curl/8.5.0`, `curl`,
# `python-requests/2.31.0`, `Python-urllib/3.11`, `Go-http-client/2.0`.
# SERVED with HTTP 200: `Mozilla/5.0`, `Wget/1.21`, `Scrapy/2.11` and even
# the literal string `foo`. So it is the library's own default UA that is
# banned, and any browser-shaped string — including `HeadlessChrome` — walks
# straight through.
#
# Two consequences, and the second is why this is written at such length:
#
#   * The three engines drive real browsers and therefore never see it; the
#     HTTP paths (`scraper_api_client.py`, any `requests` call) will see it
#     on their first request if they ship a default UA.
#   * §8 says a proxy failure is not a timeout, and this is the same class
#     one step further out: on THIS site a timeout from an HTTP client is a
#     BLOCK, and the answer is to send a browser-shaped User-Agent, not to
#     retry the identical request or to go looking for a better exit.
#     Retrying it at the same address with the same UA cannot ever succeed.
EXIT_BLOCKED = 3

# Exit code for a run that gathered SOME rows and then stopped early — a
# page-load timeout, a 503 throttle, or a challenge on page 3 of 10. The
# output file is still written (throwing away three good pages would be
# worse), but it is not a complete picture, and a consumer that cannot tell
# the difference will read the pages that were never fetched as products that
# disappeared from the catalogue. See write_run_meta.
# A REMOTE service failed — the Scraping Browser refusing the connection
# (`profile_locked` is the common one: a profile allows a single live
# connection), or the Scraper API answering an error. Distinct from 1 (a
# crash in this code) and from 2 (bad usage) because it means "try again, or
# use a different profile", not "there is a bug here". Defined once, here,
# because the browser engines and scraper_api_client.py both return it and
# two definitions of the same code is exactly how a family's exit contract
# drifts.
EXIT_API_ERROR = 5

EXIT_PARTIAL = 6


def write_run_meta(out_prefix: str, meta: dict) -> str:
    """Write a run-metadata sidecar next to the output, return its path.

    Deliberately a separate `<out>.meta.json` rather than columns on every
    row: this describes the RUN, not the product, and repeating it across
    every row would both bloat the output and change the schema every
    consumer of this project already parses.

    diff_runs.py reads it to refuse a comparison between runs that are not
    both complete, and between runs of different `mode`.
    """
    path = f"{out_prefix}.meta.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"[+] Wrote run metadata -> {path} (status={meta.get('status')})")
    return path


def run_meta(status: str, stop_reason: str, pages_requested: int,
             pages_completed: int, start_url: str, final_url: str,
             products: int, pages_failed: Optional[List[int]] = None,
             mode: str = "listing", source: str = SOURCE_DEFAULT,
             extra: Optional[dict] = None) -> dict:
    """Build the metadata dict for a finished run.

    `status` is the field a consumer branches on:
      complete — every requested page was fetched, or the site's own
                 pagination genuinely ran out (nothing more existed to get)
      partial  — rows were gathered, then the run stopped early
      failed   — nothing was gathered at all

    `mode` and `source` are recorded because `mode` is not implied by the
    repo: the same output prefix can hold a search run, a category run or a
    product run, and those populate different columns. diff_runs.py refuses
    a pair whose modes or sources differ. `source` is `montblanc.com` on
    every row of every run here, since all 72 markets share one host with
    the locale in the path; it is kept because consumers read these columns
    by name across the family, and `locale` is the column that varies.

    `sort` and `locale` are recorded for the same reason one step harder:
    both change WHICH products are in the file rather than how they are
    arranged, so two runs differing in either are different samples of the
    catalogue rather than two observations of it.

    `extra` carries facts about the run that are not about any single row.
    A listing run uses it for Montblanc's OWN result count — the
    `<div class="result-count">280 Results</div>` the site prints above the
    grid — recorded as `total_results`, plus the `pages_available` that
    follows from it at the run's page size. Those belong to the run rather
    than repeated down a column.

    Unlike bbb-scraper, where the same field had to carry a warning, here it
    is good news and the sidecar says so plainly: Montblanc applies no page
    cap. `writing-instruments` states 280 results, and `start=240` returned
    24 tiles, `start=264` returned the last 16, `start=288` returned zero —
    240 + 24 + 16 = 280 exactly (measured 2026-09-17). A run that walks to
    the end of a category holds the whole category, so "complete" here means
    what the word ought to mean.

    `pages_failed` lists the pages that did not yield data, by number.
    `pages_completed` alone was enough only while pages were fetched strictly
    in order, where "3 of 10 completed" could only mean 1-2-3: a count is not
    a description once pages can be fetched independently and page 3 can fail
    while 4 and 5 succeed. Recording the numbers keeps the sidecar honest
    about WHICH part of the catalogue is missing, not just how much.
    """
    meta = {
        "source": source,
        "mode": mode,
        "status": status,
        "stop_reason": stop_reason,
        "pages_requested": pages_requested,
        "pages_completed": pages_completed,
        "pages_failed": pages_failed or [],
        # Named "products" even though these are products, and kept that
        # way deliberately: every repo in this family writes this key, and a
        # consumer reading several of them reads one sidecar shape.
        # quora-scraper made the same call for answers. The row TYPE is
        # `mode` plus `source`, which are right beside it.
        "products": products,
        "start_url": start_url,
        "final_url": final_url,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        # Merged rather than nested under a key, so a consumer reads
        # `shop_rating` at the top level beside `products`. Run fields win a
        # name collision: a caller cannot accidentally overwrite `status`.
        meta.update({k: v for k, v in extra.items() if k not in meta})
    return meta


def save(rows: Sequence[Any], out_prefix: str, fmt: str,
         allow_empty: bool = False, row_cls: Type = Product) -> int:
    """Write JSON/CSV and return a process exit code.

    Returns 0 when rows were written, EXIT_NO_PRODUCTS when there were none.
    Callers are expected to exit with it.

    On zero rows, nothing is written at all unless `allow_empty`. Two reasons,
    and a live run demonstrated both. A page-load timeout produced
    `Saved 0 products -> out.json` and exit 0: a two-byte `[]` that a
    consuming pipeline reads as a successful run with no stock. Worse, if the
    file already held a good result from an earlier run, that result is now
    gone — the failure destroyed the last known good data. So an empty result
    leaves the previous file intact and says why.

    `allow_empty=True` is for the legitimate case: a filter that genuinely
    matches nothing, where an empty file is the answer.
    """
    if not rows and not allow_empty:
        print(f"[!] 0 products — refusing to write {out_prefix}.json/.csv, so an "
              f"earlier good result isn't overwritten with an empty one. "
              f"Pass --allow-empty if an empty result is the expected answer.")
        return EXIT_NO_PRODUCTS

    if fmt in ("json", "both"):
        write_json(rows, f"{out_prefix}.json")
        print(f"[+] Saved {len(rows)} products -> {out_prefix}.json")
    if fmt in ("csv", "both"):
        write_csv(rows, f"{out_prefix}.csv", row_cls=row_cls)
        print(f"[+] Saved {len(rows)} products -> {out_prefix}.csv")
    return 0 if rows else EXIT_NO_PRODUCTS


# Stop reasons that mean the run saw everything there was to see. Anything
# else ended the page loop early, so the result is only a partial view.
#
# "no_new_products" belongs here and "pagination_exhausted" is kept for the
# engines that still stop on a missing next-link: the first is a property of
# the DATA (a page contributed nothing not already seen, so the listing is
# over), while the second is a property of a CSS SELECTOR and is therefore
# the weaker signal — a renamed attribute looks identical to a short
# catalogue.
#
# On Montblanc there is a THIRD, stronger signal, and it is the site's own
# arithmetic: page 1 of a listing prints its own `result-count`, so the
# number of pages is known from the first response rather than discovered by
# walking off the end. "page_cap_reached" is that stop reason.
#
# It is a COMPLETE run, and on this site it is complete in the strong sense:
# the site imposes no cap of its own, so the planned page count IS the whole
# category (see run_meta). Walking off the end is cheap rather than harmful
# — `start=288` on a 280-product category is HTTP 200 with an empty grid,
# not the HTTP 500 that the same overshoot produces on BBB — but planning
# against the stated count still saves the wasted fetch and keeps
# `pages_available` honest in the sidecar.
#
# "single_page_mode" is complete by construction: --mode product reads one
# page because one page is all there is. Note that it still emits MANY rows
# — one per variant — so "single page" is a statement about fetching, not
# about the size of the output.
# Note "parser_found_nothing" is deliberately ABSENT. A page Montblanc served
# that links to products and parsed to zero rows is OUR failure, not a
# complete answer, and a run that ends that way must not report `complete`
# (§20). Its exit code stays EXIT_NO_PRODUCTS — the catalogue question really
# was answered — so only the status and the stop_reason carry the distinction.
COMPLETE_STOP_REASONS = ("completed", "pagination_exhausted", "no_new_products",
                         "page_cap_reached", "single_page_mode")


def finish_run(rows: Sequence[Any], out_prefix: str, fmt: str,
               allow_empty: bool, *, blocked: bool, stop_reason: str,
               pages_requested: int, pages_completed: int,
               start_url: str, final_url: str,
               pages_failed: Optional[List[int]] = None,
               mode: str = "listing", source: str = SOURCE_DEFAULT,
               extra: Optional[dict] = None) -> int:
    """Write output + the run-metadata sidecar; return the exit code.

    Shared by all three browser engines so the status/exit-code mapping
    cannot drift between them.

    The metadata sidecar is written ONLY when the row file was written.
    Otherwise a failed run would leave a "status": "failed" sidecar next to
    the previous run's still-intact good output (which `save` deliberately
    does not overwrite) — the two files would contradict each other, and
    diff_runs.py would refuse to compare data that is in fact fine.
    """
    complete = stop_reason in COMPLETE_STOP_REASONS
    row_cls = ROW_CLASS_BY_MODE.get(mode, Product)
    rc = save(rows, out_prefix, fmt, allow_empty=allow_empty, row_cls=row_cls)
    wrote_output = bool(rows) or allow_empty

    if wrote_output:
        status = "complete" if (rows and complete) else (
            "partial" if rows else "failed")
        write_run_meta(out_prefix, run_meta(
            status=status, stop_reason=stop_reason,
            pages_requested=pages_requested, pages_completed=pages_completed,
            pages_failed=pages_failed, mode=mode, source=source,
            start_url=start_url, final_url=final_url, products=len(rows),
            extra=extra))

    if not rows:
        # Nothing gathered at all: a challenge outranks "empty result",
        # because it says something stood between the run and the content.
        return EXIT_BLOCKED if blocked else rc
    if not complete:
        print(f"[!] Partial run: stopped after {pages_completed} of "
              f"{pages_requested} page(s) ({stop_reason}). The output holds "
              f"what was gathered, but it is NOT a complete view — see "
              f"{out_prefix}.meta.json.")
        return EXIT_PARTIAL
    return rc

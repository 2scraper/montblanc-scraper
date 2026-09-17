# Contributing

Bug reports, site-change reports and pull requests are all welcome. This file
covers the few things specific to a scraper, which are not the usual ones.

## Before you open anything

Run the offline suite. It needs no network, no browser and no API key, and takes
about a second:

```bash
pip install -r requirements.txt
python3 smoke_test.py
```

It prints its own check count, and lists any group it had to skip because an
engine library is absent.

**The suite must pass with no engine installed at all.** CI installs only
`beautifulsoup4` and `requests`, so any import of `playwright_scraper`,
`puppeteer_scraper` or `selenium_scraper` in a test has to sit inside
`try/except ImportError` with the skip recorded. This is easy to get wrong
locally, where you almost certainly have an engine installed and an unguarded
import passes.

If the suite fails on a clean clone, that is itself the bug — say so.

## Never commit a credential

`.env` is in `.gitignore`. Keep it there.

The scrapers mask `user:pass@` in their own log lines, but three things are **not**
masked: raw HTML dumps, the Scraper API's `x-debug` response header, and your
shell history. Before pasting any output into an issue or a PR, replace keys,
proxy passwords and full `ws://user:pass@host:9222` endpoints with `***`.

CI fails the build if something that looks like a credential is committed. That
check is a backstop, not a review — a leaked key has to be rotated whether or
not the check caught it.

## Reporting a site change

This is the most useful issue you can open, and the template
(`site_changed.yml`) asks for the two things that make it actionable: the URL
and what the run printed.

**Include the exit code**, because it says which layer moved:

| Exit | What it means here |
|---|---|
| 4 | zero products — the page shape moved, or the category is genuinely empty |
| 3 | blocked — which on this site would be NEW; see below |
| 6 | partial — some pages came back and some did not |

**Exit 3 is the one worth reporting loudest.** Measured 2026-09-17, Montblanc
served an ordinary datacenter address on every mode with no key and no proxy.
If a plain run starts coming back blocked, the site has started gating its
catalogue and the README's central claim needs re-measuring the same day.

Four things are most likely to break the parser, in the order they would
actually bite:

1. **The JSON-LD types.** A listing publishes `ItemList` and a product page
   publishes `ProductGroup`. If either is renamed or dropped, the primary
   path goes quiet and the tile fallback takes over — which still produces
   rows, so watch for a `price_source` that suddenly reads `dom` everywhere.
2. **The tile's `data-tracking-event-payload`.** It is the site's own
   analytics blob and it carries the columns the JSON-LD does not:
   `item_available` (the only honest source of `in_stock`), `item_collection`,
   `item_material_color`, `item_category`. A rename here is the QUIET one —
   rows still write, with those columns null.
3. **`start` / `sz`.** The pagination convention, verified against the site's
   own sort links rather than guessed. If it changes, a multi-page run starts
   refetching page 1 and the dedupe drop count goes up sharply.
4. **`class="result-count"`.** The site's own total, and what pages are
   planned from. The NOUN beside the number is localised ("Results" on en-fi,
   "Items" on en-us) and the parser deliberately ignores it — if a report says
   `total_results: null`, check the element name, not the wording.

## Before this repository goes public

Read what the HISTORY exposes, not just the working tree — scan every blob
that ever existed (`git rev-list --objects --all`) for credentials, and
remember that a commit on top cannot reach what a published tag or a merged
PR's refs already hold. Decide before publishing; afterwards only a fresh
repository removes it.

Then the rest of the presentation, in the order that matters:

1. `python3 smoke_test.py` green, and the canary dispatched at least once.
   **Both of this repo's real canary jobs run with NO secrets** and are
   expected to be green — that is not a convenience, it is what keeps the
   README's central claim honest. Only the third job, which exercises the
   Scraping Browser path, skips without a secret; dispatch it by hand once
   and confirm the SKIP branch runs, not just the happy one.
   Note `workflow_dispatch` requires the workflow to exist on the DEFAULT
   branch — from a feature branch `gh workflow run` answers
   `HTTP 404: workflow canary.yml not found on the default branch`, which
   reads like a typo in the filename. Merge first, dispatch second.
2. The repo description, homepage and topics set. A banned-wording check
   covers the FILES in this repo; a GitHub description is not a file, and a
   phrase that check forbids has reached public repo descriptions in this
   family that way. Check the description, the topics and the release notes
   by hand.

   Note this bullet does not QUOTE the phrase, and the omission is
   deliberate: this file is scanned, so a note explaining the ban would
   itself fail the check. It has happened — a release note quoting the
   offending text failed the check the release was adding. Describe, do not
   quote. The check's own source is where the list lives.
3. Only then the row in the org profile README — and check every row on that
   page with an ANONYMOUS request rather than your own logged-in browser. You
   are a member of the org, so a logged-in view shows you the private repos
   too and the page looks whole to the one person who cannot see the problem.


## Pull requests

**Add a test for the behaviour you are changing.** `smoke_test.py` is a single
file of plain functions with inline HTML/JSON fixtures — no pytest, no
conftest, no fixtures directory. Copy the nearest existing check and edit it.

Seven properties in this repo exist because they were once absent or were
measured against expectation, and cost real time. Tests pin all seven, so a PR
that breaks one will fail rather than silently regress:

- **A product page is not a small listing, and the listing parser must
  return NOTHING on one.** Montblanc publishes `ProductGroup` there, not
  `ItemList`, so the JSON-LD path finds nothing — and before this was
  guarded, the tile fallback returned the *"you may also like"* carousel as
  **18 products with prices**, in silence. `parse_products` refuses a detail
  page on the page's own evidence, and the fixture carries a real carousel
  tile so the check can actually fail (a fixture without one passes for the
  wrong reason).

- **`in_stock` comes from the TILE, not from the JSON-LD.**
  `offers.availability` was `InStock` on **192 of 192** listing rows measured
  — a template default. The tile's `item_available` was `soldout` on **7 of
  384**. The canary asserts `in_stock` is not constant-True for exactly this
  reason: if the tile read breaks, the column silently becomes a lie that
  looks like good news.

- **A "From €515" range is not a price.** The JSON-LD omits `offers.price`
  for variant groups (14 of 192 rows) and the DOM renders a minimum instead.
  It is recorded, with `price_source: "dom_range"`, so a consumer can exclude
  it. If that ever collapses to `"dom"`, the column starts claiming a group's
  cheapest variant is the product's price.

- **The JSON-LD and the tile can spell one product two ways.** A listing
  entry's URL said `MB132288M` while its tile said `data-pid="MB132288VG"` —
  same product, different variant suffix. Joined on the full sku they never
  meet, and 3 of 71 rows on one live run silently lost `image_url`,
  `in_stock`, `collection` and `color` while looking fine. The join falls
  back to the base id, and REFUSES when two tiles share one base rather than
  guessing between them.

- **`--sort` defaults to `price-asc`, not to the site's own default**, and it
  is a COLUMN rather than only a sidecar field. Measured 2026-09-17: page 1
  of `/en-fi/writing-instruments` under the site's default ordering and under
  price-ascending shared **0 of 24** products. The ordering decides WHICH
  products are in the file, so two runs that differ on it are not comparable
  and `diff_runs.py` refuses them.

- **The site never says which ordering it APPLIED.** Its `ruleId` and its
  checked sort radio are both the category's default on every request,
  whatever was asked for, while the results demonstrably change. The `sort`
  column therefore records the REQUEST; the default goes in the sidecar under
  `site_default_sort`. An earlier version read `ruleId` as the applied sort
  and warned on every page of every run — a check that fires on every healthy
  run teaches the reader to ignore checks.

- **`akamai` is not a block marker.** Montblanc is Akamai-fronted and its own
  performance script references `akamaihd.net` on **every page it serves**, so
  a bare `akamai` marker reports a served catalogue as a block — the exact
  mistake a sibling repo shipped. Neither is **`cf-turnstile`**, which is the
  obvious marker for a Turnstile and is measured useless across this family:
  2Captcha's own Scraping Browser auto-solve extension injects its hunters
  into every page it loads. `challenges.cloudflare.com` is kept instead, at 0
  on every served page here.

  `smoke_test.py` pins it in both directions: no marker may fire on any of
  the four known-good fixtures, and the excluded strings must really be
  excluded. **Before adding any marker, count it on a page you know is
  good.**


### If your change needs a live run

Most do not — the suite covers the parser, the writers, the captcha classifier
and the CLI contract against inline fixtures. If yours genuinely needs
montblanc.com, say in the PR what you ran, which mode and URL, from which exit,
and what you got — including the sidecar's `total_results`,
`pages_available`, `locale` and `sort_requested`, and the coverage lines the
run prints.

Three things about running this live that are specific to Montblanc:

* **No mode needs a special exit.** All three answered a plain datacenter
  address on 2026-09-17, so "it worked from my laptop" is reproducible here
  in a way it is not on some sibling repos. If yours did not, say which exit
  you used — that is a finding.
* **Say which MARKET you ran.** Prices are set per market, not converted, so
  a number without its locale cannot be checked against anything. One
  backpack: EUR 2000 on en-fi, EUR 1900 on de-de, GBP 1700 on en-gb, USD 1990
  on en-us.
* **If you used an HTTP client rather than an engine, check your
  User-Agent.** The edge kills the connection for `curl`, `python-requests`
  and friends, and `requests` reports that as a ReadTimeout — which looks
  exactly like a slow network and is not one.

**Run more than the primary engine.** "Mirror them exactly" is a design rule,
not a verification: the first live run of the pyppeteer engine crashed on its
FIRST fetch on a signature mismatch that four separate offline checks and 400
green assertions had not caught.

Do not add anything that submits a form or puts anything in a cart.
Montblanc's pages carry an add-to-cart flow, a wishlist and a personalisation
step; this project reads the catalogue and must never touch any of them.

## Scope

This repo scrapes **public pages** on montblanc.com: category listings,
search results and product pages, exactly as an anonymous visitor is served
them.

Out of scope: anything behind a login, anything that submits a form
(including the cart, the wishlist and personalisation), anything that defeats a
protection rather than passing it the way an ordinary browser does, and the
named individuals Montblanc lists as a product's officers — there is deliberately
no column for them, and adding one is a product decision rather than a bug
fix.

## Licence

MIT. By opening a pull request you agree your contribution ships under it.

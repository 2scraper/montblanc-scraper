# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/) as closely as a
CLI toolkit can. A PATCH release means fixes — it does not promise that every
flag and default is frozen, so a behaviour-changing default can appear in one.
When it does, the release notes lead with it.

## [Unreleased]

### Fixed — leftovers from the repos this one was bootstrapped from

- **The Selenium and Puppeteer engines' "blocked" message described another
  site.** It said a datacenter address was what gets refused (measured
  2026-09-16, before this repo existed), pointed at a `--mode profile` this
  repo does not have, and recorded `blocked_by: "cloudflare (hard block)"`
  where the Playwright engine records `"edge refusal"` — so the three engines
  disagreed on `stop_reason` for the same page. Both now say what the
  Playwright engine says: an ordinary datacenter address is served here, so
  check the User-Agent first, then try another exit.
- The Playwright engine's no-pool re-fetch log line claimed a re-fetch "is
  often what clears it" on this site, and a comment cited a 403-from-a-
  datacenter measurement. Neither was measured on Montblanc; both removed.
- `--dump-html` help in all three engines pointed at a `TROUBLESHOOTING.md`
  that does not exist; it now points at the README's "Traps that look like
  bugs". The question issue template had the same dead link.
- `captcha_solver.py`'s module docstring and its "what is not here" note
  described another site's Cloudflare gate. They now state what was measured
  here: no challenge of any kind, Akamai present in the headers only.
- The bug-report and site-changed issue templates were another site's, with
  its URLs, its bot manager and its columns. Rewritten for montblanc.com from
  the README.
- `landing.html` described a business directory (grades, accreditation,
  complaint counts, a 15-page cap). Rewritten from `landing.md`.
- `requirements.txt` named another repo in its header; `.dockerignore`
  listed another repo's output prefix instead of `montblanc_products.*`.
- A few comments that compared with a sibling's site without naming it as a
  sibling now name `bbb-scraper`; one smoke-test docstring said "BBB has not
  been measured" where it meant this site.

## [0.1.2] — 2026-09-18

> **Correction to v0.1.0 and v0.1.1.** Both said "no challenge of any kind
> appeared — zero reCAPTCHA, Turnstile, DataDome, PerimeterX … markers". That
> is true, and it reads as "this site has no bot management", which is FALSE.
> Montblanc runs **Akamai Bot Manager**. It simply never challenged us.

### Fixed

- **The README now names the protection that is actually here.** Prompted by
  a plain question — "did we meet a captcha, and which one?" — answered by
  measuring rather than by re-reading the code (§21, which notes that a plain
  question has twice been the cheapest bug-finding tool available).

  The captcha count holds and got stronger: **0 markers of any vendor across
  37 captures totalling 21.7 MB**, including not one occurrence of the
  literal word *captcha*, and 0 `data-sitekey`.

  But the marker scan reads HTML BODIES, and this site's bot management is
  in the RESPONSE HEADERS: `_abck` and `bm_sz` cookies on every response,
  `ak_p` server-timing, `x-akamai-transformed`, with Cloudflare in the chain
  too (`cf-ray`, `server: cloudflare`). A body-only scan reports a clean
  page and is not wrong — it is just not looking where the answer is. This
  is §18's "'no challenge rendered' is not 'no captcha configured'" with the
  evidence one layer out from where that section looks for it.

  What was measured is that Bot Manager **never challenged**: `_abck` came
  back with its `~-1~` validation field on all three consecutive fetches, so
  no challenge was issued and no validation occurred. The honest claim is
  "it did not challenge this kind of address today", not "there is nothing
  here" — and volume, a worse-scored ASN or a policy change can each turn
  that around.

- **A stray `country-id` (Indonesia) in `env_config.py`'s endpoint example**,
  inherited verbatim from tokopedia-scraper. Now `country-de`, which is a
  market this repo actually discusses.

### Added

- A check pinning the finding in both directions: `_abck`, `bm_sz`, `ak_p`
  and `x-akamai-transformed` must stay OUT of `BOT_CHALLENGE_MARKERS` (they
  are on every GOOD response, so matching them would report a served
  catalogue as blocked — the same mistake `akamai` itself would be), AND the
  README must keep naming the bot manager, so "no captcha markers" can never
  quietly become "no bot management". Both directions controlled.

### Verified, not changed

A full secret audit, since the repo is public:

- **49 blobs across 75 objects** that have ever existed in this history —
  nothing credential-shaped. Every `user:pass@`-shaped hit is a documentation
  placeholder or a masking-test fixture.
- **Zero 32-hex strings** anywhere in history.
- **No `.env` was ever committed** — only `.env.example`.
- **No repo secrets are set**, so nothing could leak through CI; the public
  workflow logs carry no credential shapes either.
- **Published artifacts are data only** (14 KB listing, 1.5 KB product) — no
  `*_debug.html` page dump was ever uploaded.
- Release notes, description, topics and the org-profile commit: clean.

## [0.1.1] — 2026-09-18

A pass back over CLAUDE.md, section by section, checking each claim against
the repo rather than against memory. It found one missing signal, one
coverage floor that fires on correct data, and three pieces of stale
presentation.

### Added

- **A served page that links to products and parses to ZERO rows now says so
  by name** (§20). It reports `stop_reason: parser_found_nothing`, is
  excluded from `COMPLETE_STOP_REASONS`, and the canary fails on it by name.
  Reported as plain "0 products" it would send the reader to check the URL
  when the thing that moved is the parser. The exit code is unchanged —
  the catalogue question really was answered — so only the status and the
  reason carry the distinction.

  §20 also says to check such a signal CAN fire before adding it: here it
  can, because the tile fallback needs a `data-pid` element as well as a
  product href, so markup that loses its tiles while keeping its links lands
  exactly there.

### Fixed

- **`image_url` is no longer in the 99% coverage floor.** Montblanc's own
  ItemList can name a product it renders no tile for, and such an entry
  carries `"image": null` and `"brand": null` in the JSON-LD as well — so
  there is nothing on the page to read. Measured: 1 row of 92 on a four-page
  live run (`MB127852M`). The floor was firing on correct data, which is the
  behaviour that teaches readers to ignore floors. The figure is still
  reported on every page.
- **The repo description was 29 words**, against the 15–25 the family notes
  ask for. Now 23.
- **A stale check count inherited from a sibling repo** (`~339 checks`) was
  sitting in `pyproject.toml`, already wrong on arrival — §13's trap exactly.
  Removed rather than updated: `python3 smoke_test.py` prints the real one.

### Measured

Negative results, recorded because "we did not implement it" and "the site
has none" are different facts and only the second justifies the omission.
All on 2026-09-17/18:

- **No captcha is wired on this site at all** — 0 `data-sitekey`, 0
  `*_SITE_KEY`, 0 `<captcha-*>` elements, 0 reCAPTCHA/Turnstile/hCaptcha
  references across listing, product and home pages. That is stronger than
  "no challenge was rendered", which is all a sibling repo could say.
- **No `<link rel="next">` on any listing kind**, so the selector layer of
  the family's three-layer pagination rule is unavailable here and the
  remaining two (a verified `start`/`sz` convention, and the data running
  out) are the whole of it.
- **No cents-dash prices** (`349,– €`) on `en-fi`, `de-de` or `en-us`, and
  **no instalment text inside any price node** — so neither guard is ported.
- **Headless and headful both work**: 2/2 and 2/2, 24 rows either way. This
  site does not discriminate, unlike foodpanda where headless was 0/4.
- **`--concurrency 3` verified live** across four pages: rows merged in page
  order, `(page, position)` and `sku` unique, `status: complete`.

## [0.1.0] — 2026-09-17

First release. Scrapes montblanc.com through Playwright, Selenium, pyppeteer
or the 2Captcha Scraping Browser API, in three modes, with one row schema.

### The headline, because it decides whether you need to buy anything

**You do not need a 2Captcha account to scrape this site.** Measured
2026-09-17 from a bare datacenter address (Hetzner, Helsinki, AS24940) with no
key, no proxy and no fingerprinting: category listings, keyword searches and
product pages all answered HTTP 200. No reCAPTCHA, hCaptcha, Turnstile,
DataDome, PerimeterX, Incapsula, Kasada or AWS WAF markers appeared on any
capture, and there is no `data-sitekey` anywhere on the site.

What the paid products buy here is a *specific market* (prices are set per
market), volume from many addresses, and not having to run a browser — not
access. The daily canary runs with no secrets and is expected to be green,
which is what keeps that claim from going stale silently.

### Added

- **Three modes, one schema.** `--mode category` (default), `--mode search`,
  and `--mode product`, which emits **one row per variant** out of the page's
  `ProductGroup` — a sixteen-nib Meisterstück is sixteen rows, each with its
  own sku and price. A listing row cannot answer "what does the Medium nib
  cost"; these can.
- **Four engines that agree.** Playwright (primary, with `--concurrency`),
  pyppeteer, Selenium, and a Scraper API client needing no local browser. All
  three local engines were run live and returned identical output: the same
  25 skus for `/en-fi/bags/backpacks`, with identical prices, currencies,
  stock flags and titles, and the same `status`, `stop_reason` and page
  counts.
- **`--sort`, and it disagrees with the site on purpose.** Montblanc's own
  default is a merchandising rule, and page 1 under it shares **0 of 24**
  products with page 1 under price-ascending. The default here is
  `price-asc`, the only ordering stable enough for a multi-page run or a
  price diff; `--sort recommended` reproduces a visitor's view. The ordering
  is a COLUMN, because it decides which products are in the file rather than
  how they are arranged, and `diff_runs.py` refuses to compare across it.
- **`--locale`, and it is not cosmetic.** Montblanc serves every market from
  one host with the locale in the path and **sets prices per market rather
  than converting them**: one backpack was EUR 2000 on `en-fi`, EUR 1900 on
  `de-de`, GBP 1700 on `en-gb` and USD 1990 on `en-us`. Two of those share a
  currency and still disagree, so `locale` is on every row and `diff_runs.py`
  refuses a cross-market comparison.
- **Pagination planned from the site's own arithmetic.** `start`/`sz` on the
  listing URL, verified against the site's own sort links rather than
  guessed, and clamped to the `result-count` page 1 prints. There is no page
  cap: `/en-fi/writing-instruments` states 280 results and 240 + 24 + 16 =
  280 exactly, so a run that walks a category to the end holds all of it.
- **A sidecar per run** recording `total_results`, `pages_available`,
  `page_size`, `locale`, `sort_requested` and `site_default_sort`, plus which
  pages failed by number.

### Site-shaped traps this release already handles

Each of these was hit while building, and each returns a wrong answer that
looks right:

- **A product page publishes `ProductGroup`, not `ItemList`.** Point the
  listing parser at one and it finds no structured data — and then its tile
  fallback returned the *"you may also like"* carousel as **18 products with
  prices**, in silence. `parse_products` now refuses a detail page on the
  page's own evidence.
- **`in_stock` cannot be read from the structured data.**
  `offers.availability` was `InStock` on **192 of 192** listing rows — a
  template default — while the tiles' own `item_available` was `soldout` on
  **7 of 384**. Stock is read from the tile, and left `null` where there is
  no tile rather than filled with the default.
- **A "From €515" range is not a price.** The JSON-LD omits `offers.price`
  for variant groups (14 of 192 rows); the DOM renders a minimum over the
  group instead. It is recorded with `price_source: "dom_range"` so a
  consumer can exclude it from a comparison.
- **The JSON-LD and the tile can spell one product two ways.** A listing
  entry's URL said `MB132288M` while its tile said `data-pid="MB132288VG"`.
  Joined on the full sku they never meet, and 3 of 71 rows on one live run
  silently lost `image_url`, `in_stock`, `collection` and `color` while
  looking fine. The join falls back to the base id, and refuses when two
  tiles share one base rather than guessing.
- **The refusal is not a page and not a status code.** The edge kills the
  connection for a request whose User-Agent names an HTTP client library:
  `curl` sees an HTTP/2 INTERNAL_ERROR, `requests` sees a **ReadTimeout**,
  which is indistinguishable from a slow network. Measured refused: no UA,
  `curl/8.5.0`, `python-requests/2.31.0`, `Python-urllib/3.11`,
  `Go-http-client/2.0`. Measured served: `Mozilla/5.0`, `Wget/1.21`,
  `Scrapy/2.11`, and the literal string `foo`. The browser engines never meet
  it; `page_flow.classify_transport_error` names it for anything that does.
- **`akamai` is not a block marker.** The site is Akamai-fronted and
  references `akamaihd.net` on every page it serves, so a bare `akamai`
  marker would report a served catalogue as blocked. Counted on known-good
  pages before the set was written. `cf-turnstile` is absent too, for the
  reason it is absent across this family: 2Captcha's own Scraping Browser
  auto-solve extension injects it into every page it loads.
- **The result-count noun is localised and the number is not.** en-fi writes
  "280 Results" and en-us writes "281 **Items**". The first version matched a
  list of nouns and reported `total_results: null` on the very first US run;
  it now matches the digits and ignores the word.
- **The locale list from the site's own country selector is incomplete by
  exactly one** — it omits whichever market you are browsing from. Used as an
  allowlist it refused `en-fi`, the market the site geo-redirects a Finnish
  visitor to, with a reason that was false. The check is now the URL shape,
  and an unlisted locale warns rather than refuses.
- **The site never reports which ordering it applied.** Its `ruleId` and its
  checked sort radio are both the category's *default* on every request,
  whatever was asked for, while the results demonstrably change. The `sort`
  column records the request; the default goes in the sidecar under
  `site_default_sort`. An earlier version read `ruleId` as the applied sort
  and warned on every page of every run.

### Columns this site does not have

§9 of the family notes says a column null on every row should not exist, and
that removing one needs the measurement written down. Counted over 384 tiles
on eight categories plus two product pages:

- `original_price`, `discount_pct`, `lowest_price_30d` — 0 strike-price
  nodes, 0 `discount`, 0 `price-standard` anywhere. `/en-fi/sale` and
  `/en-fi/outlet` are both HTTP 404: Montblanc is a full-price house, so
  there is no reduction to disclose and no EU Omnibus 30-day low either.
- `rating`, `review_count` — 0 `aggregateRating`, 0 `ratingValue`,
  0 `reviewCount` on a listing, a bag page or a pen page. No review system.
- `ean`, `gtin`, `mpn` — absent from every JSON-LD block.
- `engraved` — `item_engraved` was False on 384 of 384 tiles, a constant.

### Tests

395 offline checks, no network and no engine library required. Fixtures are
cut from real captures taken 2026-09-17, and each was verified to parse to
identical values to its untrimmed original before being committed. Every new
check was controlled by planting the fault and confirming the suite went red —
which caught two checks that were passing for the wrong reason, one because
its fixture carried no carousel and one because its "escaped" fixture
contained a marker verbatim.

[0.1.2]: https://github.com/2scraper/montblanc-scraper/releases/tag/v0.1.2
[0.1.1]: https://github.com/2scraper/montblanc-scraper/releases/tag/v0.1.1
[0.1.0]: https://github.com/2scraper/montblanc-scraper/releases/tag/v0.1.0

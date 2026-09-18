# montblanc-scraper

[![release](https://img.shields.io/github/v/release/2scraper/montblanc-scraper?sort=semver)](https://github.com/2scraper/montblanc-scraper/releases)
[![tests](https://github.com/2scraper/montblanc-scraper/actions/workflows/tests.yml/badge.svg)](https://github.com/2scraper/montblanc-scraper/actions/workflows/tests.yml)
[![canary](https://github.com/2scraper/montblanc-scraper/actions/workflows/canary.yml/badge.svg)](https://github.com/2scraper/montblanc-scraper/actions/workflows/canary.yml)
[![python](https://img.shields.io/badge/python-3.9%20%E2%80%93%203.13-blue)](pyproject.toml)
[![licence](https://img.shields.io/badge/licence-MIT-green)](LICENSE)
[![engines](https://img.shields.io/badge/engines-Playwright%20%7C%20Selenium%20%7C%20Puppeteer-informational)](#engines)
[![runs without an account](https://img.shields.io/badge/runs%20without%20an%20account-yes-brightgreen)](#do-i-need-a-2captcha-account)

Scrapes **montblanc.com**: category listings, keyword searches, and product
pages with their full variant table. Playwright, Selenium or Puppeteer
locally, or the 2Captcha Scraping Browser API over CDP. JSON and CSV out, one
row schema, run metadata beside every file.

```bash
pip install -r requirements.txt -r requirements-playwright.txt
playwright install chromium

python playwright_scraper.py \
    --url "https://www.montblanc.com/en-us/writing-instruments" \
    --pages 3 --format both
```

---

## Do I need a 2Captcha account?

**No, and it would be dishonest to imply otherwise.**

Measured on **2026-09-17** from a bare datacenter address (Hetzner, Helsinki,
AS24940) with no key, no proxy and no browser fingerprinting:

| Page kind | Result |
|---|---|
| category listing | HTTP 200 |
| keyword search | HTTP 200 |
| product page | HTTP 200 |

No challenge of any kind appeared on any capture — zero reCAPTCHA, hCaptcha,
Turnstile, DataDome, PerimeterX, Incapsula, Kasada and AWS WAF markers, and no
`data-sitekey` anywhere on the site.

So what do the paid products actually buy here? One thing mainly, and it is a
real thing on this site:

- **A specific market.** Montblanc serves every market from one host with the
  locale in the URL path, and it **sets prices per market rather than
  converting them**. You can ask for any market from any address by putting
  the locale in the URL — but the *bare* host geo-redirects on the exit
  address, so a country exit is what makes a market's own default landing
  behaviour reproducible.
- **Volume from many addresses.** A long run from one IP concentrates traffic
  in a way a pool does not.
- **No browser infrastructure.** The Scraping Browser runs Chrome for you;
  `scraper_api_client.py` needs no local browser at all.

What they do **not** buy is access. Nothing is keeping you out today.

### The one thing that will bite an HTTP client

Montblanc's edge refuses a request whose `User-Agent` names an HTTP client
library — and it refuses by **killing the connection**, not by answering:

```
curl      curl: (92) HTTP/2 stream 1 was not closed cleanly: INTERNAL_ERROR
requests  ReadTimeout   ← looks exactly like a slow network
```

Measured on one URL, one address, one minute:

| User-Agent | Result |
|---|---|
| *(none)*, `curl/8.5.0`, `python-requests/2.31.0`, `Python-urllib/3.11`, `Go-http-client/2.0` | connection killed |
| `Mozilla/5.0 …`, `Wget/1.21`, `Scrapy/2.11`, and the literal string `foo` | HTTP 200 |

The three browser engines send a real browser's UA and never meet this. If you
script against the site yourself, **set a browser-shaped User-Agent** — no
proxy and no key will fix it, because your address is not what was rejected.
`page_flow.classify_transport_error` names this case so a run says so instead
of timing out silently.

---

## What a run produces

One real run, `--url https://www.montblanc.com/en-us/writing-instruments
--pages 3`, on **2026-09-17**:

```
70 rows across 3 pages
price      70/70      currency   70/70      category   70/70
in_stock   69/70      image_url  69/70      color      66/70
collection 61/70      size        8/70
price_source: 63 jsonld, 7 dom_range
in_stock:     49 in stock, 20 sold out, 1 unknown
prices:       USD 15.00 – 670.00
```

The sidecar (`<out>.meta.json`) recorded `total_results: 281`,
`pages_available: 12`, `locale: en-us`, `sort_requested: price-asc`,
`status: complete`.

Those coverage figures **vary between runs and between categories** — which
products page 2 holds depends on the ordering and on what Montblanc has in
stock that day — so read them as a shape, not as a contract. `collection`,
`color` and `size` are genuinely absent on some products rather than missed:
`size` is a watch case width or a belt length, so 11% is what a pen-heavy
category looks like.

`sample_output.json` and `sample_output.csv` are cut from that run.

---

## Traps that look like bugs

Every one of these was hit while building this, and each one looks like a
defect in the scraper until you know.

**A price of "From $515" is not a price.** Montblanc omits `offers.price` from
the JSON-LD for variant groups and renders a range instead (14 of 192 listing
rows measured). The number *is* recorded — it is true and useful — but it is a
**minimum over the group's variants**, so it carries
`price_source: "dom_range"`. Exclude those rows from a price comparison, or
open the product page with `--mode product` to get each variant's real price.

**A category path cannot be ported between markets.**
`/en-us/bags/backpacks` is a real page and `/de-de/bags/backpacks` is an HTTP
404 — German addresses it as `/de-de/lederwaren/…` and Japanese
percent-encodes its slugs. Swapping the locale segment is not enough.

**Two markets in the same currency can still disagree on price.** One backpack
on 2026-09-17: **EUR 2000** on `en-fi`, **EUR 1900** on `de-de`, GBP 1700 on
`en-gb`, USD 1990 on `en-us`. Prices are *set* per market, not converted, so
`currency` alone cannot tell you two rows are comparable — `locale` can.
`diff_runs.py` refuses to compare runs from different markets for this reason.

**The default ordering is a merchandising one.** Montblanc's own default is a
sales-velocity rule, and page 1 under it shares **0 of 24** products with page
1 under price-ascending. This scraper therefore **defaults to `--sort
price-asc`**, which disagrees with the site on purpose: it is the only
ordering stable enough for a multi-page run or a price diff. Pass `--sort
recommended` to reproduce what a visitor sees.

**The site never says which ordering it applied.** Its `ruleId` and its
checked sort radio are *both* the category's default on every request,
whatever you asked for — while the results demonstrably change. So the `sort`
column records what was **requested**, and the sidecar records the category's
default separately under `site_default_sort`. Neither claims to be what the
site did, because the site does not say.

**`in_stock` does not come from the structured data.**
`offers.availability` was `InStock` on **192 of 192** listing rows measured —
a template default. The tile's own `item_available` was `soldout` on **7 of
384**. The tile is what this scraper reads; where there is no tile (`--mode
product`) the column is left `null` rather than filled with the default.

**"0 products" might be our bug, not an empty category.** A page Montblanc
served that links to products and parses to zero rows reports
`stop_reason: parser_found_nothing` and does **not** count as a complete run
— because "0 products" alone would send you to check the URL when the thing
that moved is the parser. The canary fails on it by name.

**A product page is not a small listing.** It publishes `ProductGroup`, not
`ItemList`. Point the listing parser at one and it finds no products — and
then, before this was guarded, its fallback returned the *"you may also like"*
carousel as 18 products with prices, in silence. Use `--mode product`.

**`--mode product` emits one row per variant.** A sixteen-nib Meisterstück is
sixteen rows, each with its own sku and price. The group's own price (EUR
1000) is not any variant's price (EUR 850) — it is the price of the default
configuration.

**The ItemList can name a product the page renders no tile for.** Such an
entry carries `"image": null` and `"brand": null` in the JSON-LD too, so
`image_url`, `in_stock`, `collection` and `color` come back null and there is
nothing anywhere on the page to read them from. Measured: 1 row of 92 on a
four-page run (`MB127852M`). That is why `image_url` is reported but not
floored — a coverage threshold that fires on correct data teaches you to
ignore thresholds.

**`brand` is `"montblanc"` on every row.** It is a single-brand house. The
column is kept because the family schema has it in that position, not because
it varies.

### Things this site does NOT do

Measured and found absent, so the code does not carry the machinery for them.
Recorded because "we did not implement it" and "the site has none" are
different facts, and only the second one justifies the omission:

| Checked | Found | So |
|---|---|---|
| a captcha of any kind | **0** `data-sitekey`, `*_SITE_KEY`, `<captcha-*>`, reCAPTCHA / Turnstile / hCaptcha references on listing, product and home pages | nothing is wired here — not merely unrendered |
| `<link rel="next">` | **0** on every listing kind | §7's selector layer is unavailable, so pagination rests on `start`/`sz` (verified against the site's own sort links) and on the data running out |
| a cents dash (`349,– €`) | **0** across `en-fi`, `de-de`, `en-us` | the German-retail dash convention this family handles elsewhere is not used |
| instalment text in a price node | **0** Klarna / instalment / financing strings | no risk of reading a monthly payment as a price |
| struck-through / was-prices | **0** | no discount chain, hence no DOM price overlay |
| headless vs headful | **2/2 and 2/2** — 24 rows either way | the site does not discriminate, unlike some siblings; headless is the default because nothing argues against it |

---

## Modes

```bash
# a category listing (default)
python playwright_scraper.py \
    --url "https://www.montblanc.com/en-us/bags/backpacks" --pages 3

# or build the URL from a path and a market
python playwright_scraper.py --category "bags/backpacks" --locale en-gb --pages 2

# a keyword search — same markup, selected by query
python playwright_scraper.py --mode search --query "fountain pen" --locale en-us

# one product, with its whole variant table
python playwright_scraper.py --mode product \
    --url "https://www.montblanc.com/en-us/meisterstuck-gold-coated-legrand-fountain-pen-MB132460.html"
```

### Pagination

Montblanc paginates with `start`/`sz` on the listing URL itself, and page 1
prints the catalogue's own result count — so pages are **planned** from the
site's arithmetic rather than chased through next-links:

```
/en-us/writing-instruments?start=0&sz=24    →  "281 Items", 12 pages
                          ?start=24&sz=24   →  page 2
```

**There is no page cap.** `/en-fi/writing-instruments` states 280 results, and
`start=240` returned 24 rows, `start=264` the last 16, `start=288` zero —
240 + 24 + 16 = 280 exactly. A run that walks a category to the end holds the
whole category, and asking past the end is a served empty grid rather than an
error.

---

## Engines

| Engine | Use it when | Known limits |
|---|---|---|
| `playwright_scraper.py` | default | — |
| `puppeteer_scraper.py` | you already have pyppeteer | pyppeteer is effectively unmaintained; no `--concurrency` |
| `selenium_scraper.py` | you already have Selenium | cannot authenticate a remote CDP endpoint or a proxy |
| `scraper_api_client.py` | no local browser at all | one page per call |

All three local engines were run live against this site on 2026-09-17 and
returned **identical** output: the same 25 skus for `/en-fi/bags/backpacks`,
with identical prices, currencies, stock flags and titles, and the same
`status`, `stop_reason` and page counts.

**Install exactly one engine per virtualenv.** Playwright and pyppeteer
declare mutually unsatisfiable pins (`pyee` <12 vs ≥13), and pyppeteer and
selenium collide on `urllib3`. They do run side by side in practice, but
`pip check` reports the conflict and pip may resolve it by downgrading
something you wanted.

```bash
python -m venv .venv-playwright
.venv-playwright/bin/pip install -r requirements.txt -r requirements-playwright.txt
```

### Selenium's two limits

Neither is a bug in this code and neither can be fixed from here:

- **It cannot use an authenticated remote CDP endpoint.** Playwright's
  `connect_over_cdp` and pyppeteer's `browserWSEndpoint` take a full
  `ws://user:pass@host:port`; chromedriver's `debuggerAddress` takes a bare
  `host:port` with nowhere to put a password. A credentialed endpoint is
  refused with exit 2 rather than silently failing.
- **It cannot authenticate a proxy at all.** Credentials are stripped and a
  warning says so, so nobody believes a `user:pass` URL is doing something.

---

## Output

One row schema across all three modes — see `output_writer.Product`.

```
source scraped_at url sku title            ← the family prefix, in this order
brand price currency in_stock image_url category
price_source page position mode
locale base_sku collection sub_collection color size special_edition variant_of sort
```

Exit codes: `0` ok · `1` crash · `2` bad usage · `3` blocked · `4` zero
products · `5` remote API error · `6` partial.

**A run that finds nothing writes nothing** — `--allow-empty` opts out. A
failed run leaves the previous good output in place and writes no sidecar, so
a consumer never sees a `failed` status beside good data.

### Comparing two runs

```bash
python diff_runs.py --old monday.json --new tuesday.json
```

It **refuses** a comparison whose two runs differ in `mode`, `sort` or
`locale`, because each of those changes *which products are in the file*
rather than what happened to them — every line of such a diff would be an
artefact.

---

## Configuration

Credentials go in `.env` beside the scripts, never on a command line (a secret
in `argv` is readable by anything that can run `ps`). Copy `.env.example` and
fill in what you need:

```bash
cp .env.example .env
python3 env_config.py     # prints what was picked up, without printing secrets
```

Precedence, highest first: **explicit flag → exported environment variable →
`.env` → default.** Variables: `TWOCAPTCHA_KEY`, `MONTBLANC_CDP_ENDPOINT`,
`MONTBLANC_PROXY`, `MONTBLANC_URL`.

A placeholder still carrying `{...}` is treated as **unset**, so a copied
`.env.example` never authenticates as if it were configured.

> Scraping Browser profile credentials expire in about a day. Never paste a
> working `ws://…` endpoint into a repo, a README or a workflow — get a fresh
> one from your dashboard when you need it.

---

## Tests

```bash
python3 smoke_test.py       # the offline suite, no network, no engine needed
pytest                      # the same checks, wrapped
```

The suite passes with **no engine library installed** — every engine import is
guarded and the skip is *recorded*, because "skipped, engine absent" reads
identically to a real import error. CI installs each engine in its own venv
and fails if that engine's group reports a skip.

Fixtures are cut from real captures taken 2026-09-17 and each was verified to
parse to identical values to its untrimmed original before being committed.

`canary.yml` runs a real 3-page scrape daily and asserts `status == complete`,
a product floor, and price coverage. It needs no credentials, which is the
point: if Montblanc ever puts its catalogue behind a key or a challenge, the
badge goes red the next morning and this README's central claim is retested
without anyone remembering to.

---

## Legal and courtesy

This tool reads the same public pages a browser does. It does not log in, does
not bypass access controls, and does not touch personal data — Montblanc
publishes products, not people, and no capture in this repo contains a name, a
review or a session token.

Check `montblanc.com/robots.txt` and the site's terms yourself, keep
`--delay` sane, and do not point a concurrent run at one address. The default
is one page at a time.

---

## Licence

MIT — see [LICENSE](LICENSE).

Part of [2scraper](https://github.com/2scraper): open-source scrapers built on
[2Captcha](https://2captcha.com)'s captcha solving, Scraping Browser API,
proxies and fingerprints.

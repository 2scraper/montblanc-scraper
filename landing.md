# Montblanc Scraper by 2scraper

**Open-source Montblanc scraper for montblanc.com — four engines, your own infrastructure by default, 2Captcha's paid products only where they actually buy you something.**

Pull category listings, keyword searches and full product pages — name, sku, price, currency, stock, collection, colour, images and every variant of a product with its own price — straight from Montblanc into JSON or CSV.

[**View source on GitHub →**](https://github.com/2scraper/montblanc-scraper)

---

## What was actually measured

**You do not need an account, a key or a proxy to scrape this site.**

Measured on 2026-09-17 from a bare datacenter address (Hetzner, Helsinki) with no credentials configured at all: category listings, keyword searches and product pages **all answered HTTP 200**. No reCAPTCHA, hCaptcha, Turnstile, DataDome, PerimeterX, Incapsula, Kasada or AWS WAF markers appeared on any capture, and there is no `data-sitekey` anywhere on the site.

Say that plainly instead of selling around it. What the paid products below actually buy on this site is **a specific market**: Montblanc serves every market from one host with the locale in the URL path, and it **sets prices per market rather than converting them** — one backpack was EUR 2000 on `en-fi`, EUR 1900 on `de-de`, GBP 1700 on `en-gb` and USD 1990 on `en-us`. Two of those share a currency and still disagree. They also buy volume from many addresses, and not having to run a browser at all.

The repository's canary runs a real three-page scrape **daily, with no secrets**, and is expected to be green — which is what keeps the claim above from going stale quietly.

Full numbers are in the [repository README](https://github.com/2scraper/montblanc-scraper#readme).

## What you get

- Free, open-source scraper, one script per engine — **Playwright** (primary, with parallel page fetching), **Selenium** and **Puppeteer** (via pyppeteer), all producing the identical output schema and exit codes, plus a browserless client for 2Captcha's Scraping Browser API
- Three modes: a **category** listing, a keyword **search**, or a single **product** page — and that last one emits **one row per variant**, so a sixteen-nib Meisterstück is sixteen rows, each with its own sku and price
- Reads Montblanc's own structured data (`ItemList` on a listing, `ProductGroup` on a product) as the primary path, enriched from the site's own tile attributes, with a URL-pattern fallback — no fragile CSS-class scraping anywhere
- Pagination planned from the site's own printed result count, not chased through next-links, so pages can be fetched concurrently
- JSON and CSV with one stable row schema, plus a `.meta.json` sidecar per run recording the market, the ordering, the site's own totals and which pages failed by number
- `diff_runs.py` to compare two runs — and it **refuses** a comparison across markets, modes or orderings, because each of those changes which products are in the file rather than what happened to them
- 395 offline checks that run with no network and no browser installed

## Traps it already handles

- **A product page publishes `ProductGroup`, not `ItemList`** — point a listing parser at one and it silently returns the "you may also like" carousel as the page's products
- **Stock cannot be read from the structured data**: `availability` said InStock on 192 of 192 rows measured, while the tiles said sold-out on 7 of 384
- **A "From €515" price is a minimum over a variant group**, not a price, and is labelled as such
- **The edge refuses HTTP client libraries by killing the connection** — `requests` reports that as a read timeout, which looks exactly like a slow network and is not one

## Built on 2Captcha

Captcha solving, the Scraping Browser API, proxies and fingerprints — four separately-billed products behind one key. On this site they are optional, and the README says so in the first section rather than the last.

[**2captcha.com**](https://2captcha.com) · [**Source on GitHub**](https://github.com/2scraper/montblanc-scraper)

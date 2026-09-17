#!/usr/bin/env python3
"""
smoke_test.py — the offline suite for montblanc-scraper.

One file of plain functions with inline fixtures. No pytest, no conftest, no
fixtures directory (CLAUDE.md §10); `tests/test_smoke.py` wraps this as a
single pytest test so `pytest` works as an entry point without a second copy
of the checks.

    python3 smoke_test.py            run everything
    python3 smoke_test.py -v         print every check as it passes

It must pass with NO engine library installed at all: every
`import playwright_scraper` / `selenium_scraper` / `puppeteer_scraper` is
guarded and the skip is RECORDED, because "skipped, engine absent" reads
identically to a real import error. CI installs each engine in its own venv
and fails if that engine's group reports a skip.

The fixtures
------------
Every one is cut from a real capture taken 2026-09-17 and trimmed to the
parts the parser reads. Each was verified to parse to IDENTICAL values to
its untrimmed original before being committed (§15 step 3) — the trimming
script asserts field-by-field equality on every row, not merely that the row
count matched.

    LISTING_HTML         /en-fi/writing-instruments, 3 of its 24 products:
                         one ordinarily priced, two of the range-priced
                         variant groups whose JSON-LD carries no price.
    SOLDOUT_HTML         /en-fi/gifts, which is where the sold-out tiles are.
                         It exists so `in_stock` can be checked against a
                         page that really has both values on it.
    PRODUCT_HTML         a Meisterstück fountain pen: `ProductGroup`, three
                         of its sixteen variants.
    GRID_FRAGMENT_HTML   the `Search-UpdateGrid` fragment the site's own
                         front end calls — tiles with ZERO JSON-LD blocks.
    CHROMIUM_ERROR_HTML  Chromium's own network-error page, produced by
                         driving a real browser through a dead proxy. It
                         carries `<title>www.montblanc.com</title>` — the
                         site's own hostname — and no vendor marker at all,
                         which is why positive-asset detection is the only
                         thing that classifies it correctly (§18).

No personal data appears in any of them: Montblanc publishes products, not
people, and none of the captures carries a name, a review or a session token.
`check_fixtures_carry_no_session_material` guards that for the next capture.
"""

import argparse
import ast
import csv
import inspect
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, fields

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAILURES = []
PASSED = 0
SKIPS = []
VERBOSE = False


def check(name, condition, detail=""):
    """Record a check. `detail` may be any value — it is coerced.

    Coerced rather than required to be a string because a check whose detail
    is a list is the common case ("got %s", sorted(missing)), and a suite
    that raises TypeError while REPORTING a failure hides the failure it was
    about to report. Found by controlling the checks: two of them turned a
    clean red into an ERROR line that named the wrong problem.
    """
    global PASSED
    if not isinstance(detail, str):
        detail = repr(detail)
    if condition:
        PASSED += 1
        if VERBOSE:
            print("  ok   %s" % name)
    else:
        FAILURES.append("%s%s" % (name, (" — " + detail) if detail else ""))
        print("  FAIL %s%s" % (name, (" — " + detail) if detail else ""))


def equal(name, got, want):
    check(name, got == want, "got %r, want %r" % (got, want))


def skip(group, reason):
    SKIPS.append("%s: %s" % (group, reason))
    print("  SKIP %s — %s" % (group, reason))
LISTING_HTML = "<!doctype html><html><head><title>Montblanc</title>\n<script>var preloadedData = {\"pg_country\":\"FI\",\"pg_language\":\"en\",\"currency\":\"EUR\"};</script>\n<script type=\"application/ld+json\">{\"@context\": \"https://schema.org\", \"@type\": \"ItemList\", \"itemListElement\": [{\"@context\": \"https://schema.org\", \"@type\": \"ListItem\", \"position\": 1, \"item\": {\"@type\": \"Product\", \"name\": \"Meisterstück Bloom Gold-Coated Fountain Pen Set\", \"brand\": {\"@type\": \"Brand\", \"name\": \"montblanc\"}, \"url\": \"/en-fi/meisterstuck-bloom-gold-coated-fountain-pen-set-MB138571.html\", \"image\": [\"https://www.montblanc.com/dw/image/v2/BHDB_PRD/on/demandware.static/-/Sites-montblanc-master/default/dw9918751f/images/all-images/MB138571/CaR-3FdeR0SWARuWzN2cyQ_60af8759.png?sw=315&q=90\"], \"offers\": {\"@type\": \"Offer\", \"priceCurrency\": \"EUR\", \"price\": 3780, \"availability\": \"http://schema.org/InStock\"}}}, {\"@context\": \"https://schema.org\", \"@type\": \"ListItem\", \"position\": 2, \"item\": {\"@type\": \"Product\", \"name\": \"Meisterstück Platinum-Coated Classique Ballpoint\", \"brand\": {\"@type\": \"Brand\", \"name\": \"montblanc\"}, \"url\": \"/en-fi/meisterstuck-platinum-coated-classique-ballpoint-MB132446M.html\", \"image\": [\"https://www.montblanc.com/dw/image/v2/BHDB_PRD/on/demandware.static/-/Sites-montblanc-master/default/dwf029989e/images/all-images/MB132446/XEsB-x4KRs2NRNfNCsPVXw_bedd9fa8.png?sw=315&q=90\"], \"offers\": {\"@type\": \"AggregateOffer\", \"priceCurrency\": \"EUR\", \"lowPrice\": 515, \"highPrice\": 580, \"availability\": \"http://schema.org/InStock\"}}}, {\"@context\": \"https://schema.org\", \"@type\": \"ListItem\", \"position\": 3, \"item\": {\"@type\": \"Product\", \"name\": \"Meisterstück Gold-Coated Classique Ballpoint\", \"brand\": {\"@type\": \"Brand\", \"name\": \"montblanc\"}, \"url\": \"/en-fi/meisterstuck-gold-coated-classique-ballpoint-MB132453M.html\", \"image\": [\"https://www.montblanc.com/dw/image/v2/BHDB_PRD/on/demandware.static/-/Sites-montblanc-master/default/dwbbb08bc8/images/all-images/MB132453/2044823_ac3458fc.png?sw=315&q=90\"], \"offers\": {\"@type\": \"AggregateOffer\", \"priceCurrency\": \"EUR\", \"lowPrice\": 545, \"highPrice\": 610, \"availability\": \"http://schema.org/InStock\"}}}]}</script>\n</head><body>\n<div class=\"grid-header\">class=\"result-count\">\n        \n    <span>\n        280 Results\n    </span></div>\n<div class=\"grid\" data-page-size=\"24.0\" data-page-number=\"0.0\" data-cfg=\"&quot;ruleId&quot;:&quot;recommended_sv_30d&quot;}\">\n<div class=\"product\" data-pid=\"MB138571\"><div class=\"product-tile\" data-tracking-click-event=\"select_item\" data-tracking-event-payload=\"{&quot;ecommerce&quot;:{&quot;currency&quot;:&quot;EUR&quot;,&quot;productListItem&quot;:&quot;&quot;,&quot;productListId&quot;:&quot;&quot;,&quot;productListIndex&quot;:&quot;&quot;,&quot;items&quot;:[{&quot;item_id&quot;:&quot;MB138571&quot;,&quot;item_name&quot;:&quot;Meisterstück Bloom Gold-Coated Fountain Pen Set&quot;,&quot;item_brand&quot;:&quot;mtb&quot;,&quot;item_size&quot;:&quot;N/A&quot;,&quot;item_reference&quot;:&quot;MB138571&quot;,&quot;item_available&quot;:&quot;available&quot;,&quot;price&quot;:3780,&quot;item_engraved&quot;:false,&quot;item_embossed&quot;:false,&quot;item_personalized&quot;:false,&quot;quantity&quot;:1,&quot;product_image_url&quot;:&quot;https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/dw9918751f/images/all-images/MB138571/CaR-3FdeR0SWARuWzN2cyQ_60af8759.png&quot;,&quot;item_collection&quot;:&quot;Meisterstück&quot;,&quot;item_line&quot;:&quot;N/A&quot;,&quot;item_vertical&quot;:&quot;N/A&quot;,&quot;item_sellable&quot;:&quot;N/A&quot;,&quot;item_material_case&quot;:&quot;N/A&quot;,&quot;item_material_strap&quot;:&quot;N/A&quot;,&quot;item_material_leather&quot;:&quot;N/A&quot;,&quot;item_special_edition&quot;:false,&quot;item_dial&quot;:&quot;N/A&quot;,&quot;item_adjusted&quot;:&quot;N/A&quot;,&quot;item_list_name&quot;:&quot;listCategory.pagetemplate.:  - /on/demandware.store/Sites-MontblancROW-Site/en_FI/Search-Show&quot;,&quot;item_list_id&quot;:&quot;N/A&quot;,&quot;index&quot;:&quot;N/A&quot;,&quot;item_material_color&quot;:&quot;Red&quot;,&quot;item_sub_collection&quot;:&quot;N/A&quot;,&quot;item_variant&quot;:&quot;Red&quot;,&quot;item_category&quot;:&quot;Fountain Pens&quot;,&quot;item_category_id&quot;:&quot;fountain-pens&quot;,&quot;uniqueEventId&quot;:146}]},&quot;name&quot;:&quot;Product List Click&quot;,&quot;event&quot;:&quot;select_item&quot;,&quot;reference&quot;:&quot;Product&quot;}\"><a href=\"/en-fi/meisterstuck-bloom-gold-coated-fountain-pen-set-MB138571.html\" class=\"pdp-link\"></a><div class=\"price\">\n<span class=\"price-container\">\n<span class=\"sales\">\n            \n            € 3,780.00\n\n\n        </span>\n</span>\n</div></div></div>\n<div class=\"product\" data-pid=\"MB132446M\"><div class=\"product-tile\" data-tracking-click-event=\"select_item\" data-tracking-event-payload=\"{&quot;ecommerce&quot;:{&quot;currency&quot;:&quot;EUR&quot;,&quot;productListItem&quot;:&quot;&quot;,&quot;productListId&quot;:&quot;&quot;,&quot;productListIndex&quot;:&quot;&quot;,&quot;items&quot;:[{&quot;item_id&quot;:&quot;MB132446M&quot;,&quot;item_name&quot;:&quot;Meisterstück Platinum-Coated Classique Ballpoint&quot;,&quot;item_brand&quot;:&quot;mtb&quot;,&quot;item_size&quot;:&quot;N/A&quot;,&quot;item_reference&quot;:&quot;MB132446&quot;,&quot;item_available&quot;:&quot;available&quot;,&quot;price&quot;:&quot;515.00&quot;,&quot;item_engraved&quot;:false,&quot;item_embossed&quot;:false,&quot;item_personalized&quot;:false,&quot;quantity&quot;:1,&quot;product_image_url&quot;:&quot;https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/dwf029989e/images/all-images/MB132446/XEsB-x4KRs2NRNfNCsPVXw_bedd9fa8.png&quot;,&quot;item_collection&quot;:&quot;Meisterstück&quot;,&quot;item_line&quot;:&quot;N/A&quot;,&quot;item_vertical&quot;:&quot;N/A&quot;,&quot;item_sellable&quot;:&quot;N/A&quot;,&quot;item_material_case&quot;:&quot;N/A&quot;,&quot;item_material_strap&quot;:&quot;N/A&quot;,&quot;item_material_leather&quot;:&quot;N/A&quot;,&quot;item_special_edition&quot;:false,&quot;item_dial&quot;:&quot;N/A&quot;,&quot;item_adjusted&quot;:&quot;N/A&quot;,&quot;item_list_name&quot;:&quot;listCategory.pagetemplate.:  - /on/demandware.store/Sites-MontblancROW-Site/en_FI/Search-Show&quot;,&quot;item_list_id&quot;:&quot;N/A&quot;,&quot;index&quot;:&quot;N/A&quot;,&quot;item_material_color&quot;:&quot;Black&quot;,&quot;item_sub_collection&quot;:&quot;Precious Resin - Platinum-Coated&quot;,&quot;item_variant&quot;:&quot;Black&quot;,&quot;item_category&quot;:&quot;Ballpoint Pens&quot;,&quot;item_category_id&quot;:&quot;ballpoint-pens&quot;,&quot;uniqueEventId&quot;:465}]},&quot;name&quot;:&quot;Product List Click&quot;,&quot;event&quot;:&quot;select_item&quot;,&quot;reference&quot;:&quot;Product&quot;}\"><a href=\"/en-fi/meisterstuck-platinum-coated-classique-ballpoint-MB132446M.html\" class=\"pdp-link\"></a><div class=\"price\">\n<span class=\"range\">\n<span class=\"from-range-text\">From </span>\n<span class=\"value\" content=\"515.00\">\n        € 515.00\n\n\n    </span>\n</span>\n</div></div></div>\n<div class=\"product\" data-pid=\"MB132453M\"><div class=\"product-tile\" data-tracking-click-event=\"select_item\" data-tracking-event-payload=\"{&quot;ecommerce&quot;:{&quot;currency&quot;:&quot;EUR&quot;,&quot;productListItem&quot;:&quot;&quot;,&quot;productListId&quot;:&quot;&quot;,&quot;productListIndex&quot;:&quot;&quot;,&quot;items&quot;:[{&quot;item_id&quot;:&quot;MB132453M&quot;,&quot;item_name&quot;:&quot;Meisterstück Gold-Coated Classique Ballpoint&quot;,&quot;item_brand&quot;:&quot;mtb&quot;,&quot;item_size&quot;:&quot;N/A&quot;,&quot;item_reference&quot;:&quot;MB132453&quot;,&quot;item_available&quot;:&quot;available&quot;,&quot;price&quot;:&quot;545.00&quot;,&quot;item_engraved&quot;:false,&quot;item_embossed&quot;:false,&quot;item_personalized&quot;:false,&quot;quantity&quot;:1,&quot;product_image_url&quot;:&quot;https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/dwbbb08bc8/images/all-images/MB132453/2044823_ac3458fc.png&quot;,&quot;item_collection&quot;:&quot;Meisterstück&quot;,&quot;item_line&quot;:&quot;N/A&quot;,&quot;item_vertical&quot;:&quot;N/A&quot;,&quot;item_sellable&quot;:&quot;N/A&quot;,&quot;item_material_case&quot;:&quot;N/A&quot;,&quot;item_material_strap&quot;:&quot;N/A&quot;,&quot;item_material_leather&quot;:&quot;N/A&quot;,&quot;item_special_edition&quot;:false,&quot;item_dial&quot;:&quot;N/A&quot;,&quot;item_adjusted&quot;:&quot;N/A&quot;,&quot;item_list_name&quot;:&quot;listCategory.pagetemplate.:  - /on/demandware.store/Sites-MontblancROW-Site/en_FI/Search-Show&quot;,&quot;item_list_id&quot;:&quot;N/A&quot;,&quot;index&quot;:&quot;N/A&quot;,&quot;item_material_color&quot;:&quot;Black&quot;,&quot;item_sub_collection&quot;:&quot;Precious Resin - Gold-Coated&quot;,&quot;item_variant&quot;:&quot;Black&quot;,&quot;item_category&quot;:&quot;Ballpoint Pens&quot;,&quot;item_category_id&quot;:&quot;ballpoint-pens&quot;,&quot;uniqueEventId&quot;:882}]},&quot;name&quot;:&quot;Product List Click&quot;,&quot;event&quot;:&quot;select_item&quot;,&quot;reference&quot;:&quot;Product&quot;}\"><a href=\"/en-fi/meisterstuck-gold-coated-classique-ballpoint-MB132453M.html\" class=\"pdp-link\"></a><div class=\"price\">\n<span class=\"range\">\n<span class=\"from-range-text\">From </span>\n<span class=\"value\" content=\"545.00\">\n        € 545.00\n\n\n    </span>\n</span>\n</div></div></div>\n</div>\n<img src=\"https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/img0.png\">\n<img src=\"https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/img1.png\">\n<img src=\"https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/img2.png\">\n</body></html>"

SOLDOUT_HTML = "<!doctype html><html><head><title>Montblanc</title>\n<script>var preloadedData = {\"pg_country\":\"FI\",\"pg_language\":\"en\",\"currency\":\"EUR\"};</script>\n<script type=\"application/ld+json\">{\"@context\": \"https://schema.org\", \"@type\": \"ItemList\", \"itemListElement\": [{\"@context\": \"https://schema.org\", \"@type\": \"ListItem\", \"position\": 1, \"item\": {\"@type\": \"Product\", \"name\": \"Set with Fineliner, Notebook & Cufflinks\", \"url\": \"/en-fi/set-with-fineliner-notebook-cufflinks-MB1003B.html\", \"image\": [\"https://www.montblanc.com/dw/image/v2/BHDB_PRD/on/demandware.static/-/Sites-montblanc-master/default/dwb5ba24ee/images/all-images/MB1003B/MB1003B_1.png?sw=315&q=90\"], \"offers\": {\"@type\": \"Offer\", \"priceCurrency\": \"EUR\", \"price\": 100, \"availability\": \"http://schema.org/InStock\"}}}, {\"@context\": \"https://schema.org\", \"@type\": \"ListItem\", \"position\": 2, \"item\": {\"@type\": \"Product\", \"name\": \"Set with Medium Backpack & Mini Wallet\", \"url\": \"/en-fi/set-with-medium-backpack-mini-wallet-MB1009B.html\", \"image\": [\"https://www.montblanc.com/dw/image/v2/BHDB_PRD/on/demandware.static/-/Sites-montblanc-master/default/dw8e55b81b/images/all-images/MB1009B/MB1009B_1.png?sw=315&q=90\"], \"offers\": {\"@type\": \"Offer\", \"priceCurrency\": \"EUR\", \"price\": 380, \"availability\": \"http://schema.org/InStock\"}}}]}</script>\n</head><body>\n<div class=\"grid-header\">class=\"result-count\">\n        \n    <span>\n        739 Results\n    </span></div>\n<div class=\"grid\" data-page-size=\"24.0\" data-page-number=\"0\" data-cfg=\"&quot;ruleId&quot;:&quot;recommended_sv_30d&quot;}\">\n<div class=\"product\" data-pid=\"MB1003B\"><div class=\"product-tile\" data-tracking-click-event=\"select_item\" data-tracking-event-payload=\"{&quot;ecommerce&quot;:{&quot;currency&quot;:&quot;EUR&quot;,&quot;productListItem&quot;:&quot;&quot;,&quot;productListId&quot;:&quot;&quot;,&quot;productListIndex&quot;:&quot;&quot;,&quot;items&quot;:[{&quot;item_id&quot;:&quot;MB1003B&quot;,&quot;item_name&quot;:&quot;Set with Fineliner, Notebook &amp; Cufflinks&quot;,&quot;item_brand&quot;:&quot;&quot;,&quot;item_size&quot;:&quot;N/A&quot;,&quot;item_reference&quot;:&quot;MB1003B&quot;,&quot;item_available&quot;:&quot;soldout&quot;,&quot;price&quot;:&quot;100.00&quot;,&quot;item_engraved&quot;:false,&quot;item_embossed&quot;:false,&quot;item_personalized&quot;:false,&quot;quantity&quot;:1,&quot;product_image_url&quot;:&quot;https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/dwb5ba24ee/images/all-images/MB1003B/MB1003B_1.png&quot;,&quot;item_collection&quot;:&quot;N/A&quot;,&quot;item_line&quot;:&quot;N/A&quot;,&quot;item_vertical&quot;:&quot;N/A&quot;,&quot;item_sellable&quot;:&quot;N/A&quot;,&quot;item_material_case&quot;:&quot;N/A&quot;,&quot;item_material_strap&quot;:&quot;N/A&quot;,&quot;item_material_leather&quot;:&quot;N/A&quot;,&quot;item_special_edition&quot;:&quot;N/A&quot;,&quot;item_dial&quot;:&quot;N/A&quot;,&quot;item_adjusted&quot;:&quot;N/A&quot;,&quot;item_list_name&quot;:&quot;listCategory.pagetemplate.:  - /on/demandware.store/Sites-MontblancROW-Site/en_FI/Search-Show&quot;,&quot;item_list_id&quot;:&quot;N/A&quot;,&quot;index&quot;:&quot;N/A&quot;,&quot;item_material_color&quot;:&quot;N/A&quot;,&quot;item_sub_collection&quot;:&quot;N/A&quot;,&quot;item_variant&quot;:&quot;N/A&quot;,&quot;item_category&quot;:&quot;Back to Work&quot;,&quot;item_category_id&quot;:&quot;back-to-work&quot;,&quot;uniqueEventId&quot;:495}]},&quot;name&quot;:&quot;Product List Click&quot;,&quot;event&quot;:&quot;select_item&quot;,&quot;reference&quot;:&quot;Product&quot;}\"><a href=\"/en-fi/set-with-fineliner-notebook-cufflinks-MB1003B.html\" class=\"pdp-link\"></a><div class=\"price\">\n<span>\n<span class=\"sales\">\n<span class=\"value\">€ 1.045,00</span>\n</span>\n</span>\n</div></div></div>\n<div class=\"product\" data-pid=\"MB1009B\"><div class=\"product-tile\" data-tracking-click-event=\"select_item\" data-tracking-event-payload=\"{&quot;ecommerce&quot;:{&quot;currency&quot;:&quot;EUR&quot;,&quot;productListItem&quot;:&quot;&quot;,&quot;productListId&quot;:&quot;&quot;,&quot;productListIndex&quot;:&quot;&quot;,&quot;items&quot;:[{&quot;item_id&quot;:&quot;MB1009B&quot;,&quot;item_name&quot;:&quot;Set with Medium Backpack &amp; Mini Wallet&quot;,&quot;item_brand&quot;:&quot;&quot;,&quot;item_size&quot;:&quot;N/A&quot;,&quot;item_reference&quot;:&quot;MB1009B&quot;,&quot;item_available&quot;:&quot;soldout&quot;,&quot;price&quot;:&quot;380.00&quot;,&quot;item_engraved&quot;:false,&quot;item_embossed&quot;:false,&quot;item_personalized&quot;:false,&quot;quantity&quot;:1,&quot;product_image_url&quot;:&quot;https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/dw8e55b81b/images/all-images/MB1009B/MB1009B_1.png&quot;,&quot;item_collection&quot;:&quot;N/A&quot;,&quot;item_line&quot;:&quot;N/A&quot;,&quot;item_vertical&quot;:&quot;N/A&quot;,&quot;item_sellable&quot;:&quot;N/A&quot;,&quot;item_material_case&quot;:&quot;N/A&quot;,&quot;item_material_strap&quot;:&quot;N/A&quot;,&quot;item_material_leather&quot;:&quot;N/A&quot;,&quot;item_special_edition&quot;:&quot;N/A&quot;,&quot;item_dial&quot;:&quot;N/A&quot;,&quot;item_adjusted&quot;:&quot;N/A&quot;,&quot;item_list_name&quot;:&quot;listCategory.pagetemplate.:  - /on/demandware.store/Sites-MontblancROW-Site/en_FI/Search-Show&quot;,&quot;item_list_id&quot;:&quot;N/A&quot;,&quot;index&quot;:&quot;N/A&quot;,&quot;item_material_color&quot;:&quot;N/A&quot;,&quot;item_sub_collection&quot;:&quot;N/A&quot;,&quot;item_variant&quot;:&quot;N/A&quot;,&quot;item_category&quot;:&quot;Back to Work&quot;,&quot;item_category_id&quot;:&quot;back-to-work&quot;,&quot;uniqueEventId&quot;:745}]},&quot;name&quot;:&quot;Product List Click&quot;,&quot;event&quot;:&quot;select_item&quot;,&quot;reference&quot;:&quot;Product&quot;}\"><a href=\"/en-fi/set-with-medium-backpack-mini-wallet-MB1009B.html\" class=\"pdp-link\"></a><div class=\"price\">\n<span>\n<span class=\"sales\">\n<span class=\"value\">€ 2.110,00</span>\n</span>\n</span>\n</div></div></div>\n</div>\n<img src=\"https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/img0.png\">\n<img src=\"https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/img1.png\">\n<img src=\"https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/img2.png\">\n</body></html>"

PRODUCT_HTML = "<!doctype html><html><head><title>Montblanc</title>\n<script>var preloadedData = {\"pg_country\":\"FI\",\"pg_language\":\"en\",\"currency\":\"EUR\"};</script>\n<script type=\"application/ld+json\">{\"@context\": \"http://schema.org/\", \"@type\": \"ProductGroup\", \"name\": \"Meisterstück Gold-Coated LeGrand Fountain Pen\", \"description\": \"The Meisterstück LeGrand in deep black precious resin with gold-coated details, surmounted by the white star emblem and finished with a handcrafted gold nib, evolves into Montblanc’s design icon.\", \"sku\": \"MB132460\", \"brand\": {\"@type\": \"Brand\", \"name\": \"montblanc\"}, \"image\": [\"https://www.montblanc.com/dw/image/v2/BHDB_PRD/on/demandware.static/-/Sites-montblanc-master/default/dwdb3c668a/images/all-images/MB132460/cZirSB-URi2MKg3AktxGdw_6cd5fb48.png?sw=1250&q=90\"], \"offers\": {\"url\": \"https://www.montblanc.com/en-fi/meisterstuck-gold-coated-legrand-fountain-pen-MB132460.html\", \"@type\": \"Offer\", \"priceCurrency\": \"EUR\", \"price\": \"1000.00\", \"availability\": \"http://schema.org/InStock\"}, \"variesBy\": \"https://schema.org/size\", \"hasVariant\": [{\"@context\": \"http://schema.org/\", \"@type\": \"Product\", \"name\": \"Meisterstück Gold-Coated Classique Fountain Pen\", \"description\": \"The Meisterstück LeGrand in deep black precious resin with gold-coated details, surmounted by the white star emblem and finished with a handcrafted gold nib, evolves into Montblanc’s design icon.\", \"sku\": \"MB132464\", \"brand\": {\"@type\": \"Brand\", \"name\": \"montblanc\"}, \"image\": [\"https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/dwb24eabda/images/all-images/MB132580/QZHURSXbpEu0N--PYY-UnQ_2d65f2c5.png\"], \"offers\": {\"url\": \"https://www.montblanc.com/en-fi/meisterstuck-gold-coated-legrand-fountain-pen-MB132460.html\", \"@type\": \"Offer\", \"priceCurrency\": \"EUR\", \"price\": \"850.00\", \"availability\": \"http://schema.org/InStock\"}, \"url\": \"https://www.montblanc.com/en-fi/meisterstuck-gold-coated-classique-fountain-pen-MB132464.html\", \"size\": \"MTB_WRITING_INSTRUMENT_M\", \"color\": \"BLACK\", \"material\": \"\"}, {\"@context\": \"http://schema.org/\", \"@type\": \"Product\", \"name\": \"Meisterstück Gold-Coated Classique Fountain Pen\", \"description\": \"The Meisterstück LeGrand in deep black precious resin with gold-coated details, surmounted by the white star emblem and finished with a handcrafted gold nib, evolves into Montblanc’s design icon.\", \"sku\": \"MB132579\", \"brand\": {\"@type\": \"Brand\", \"name\": \"montblanc\"}, \"image\": [\"https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/dwb24eabda/images/all-images/MB132580/QZHURSXbpEu0N--PYY-UnQ_2d65f2c5.png\"], \"offers\": {\"url\": \"https://www.montblanc.com/en-fi/meisterstuck-gold-coated-legrand-fountain-pen-MB132460.html\", \"@type\": \"Offer\", \"priceCurrency\": \"EUR\", \"price\": \"850.00\", \"availability\": \"http://schema.org/InStock\"}, \"url\": \"https://www.montblanc.com/en-fi/meisterstuck-gold-coated-classique-fountain-pen-MB132579.html\", \"size\": \"BB\", \"color\": \"BLACK\", \"material\": \"\"}, {\"@context\": \"http://schema.org/\", \"@type\": \"Product\", \"name\": \"Meisterstück Gold-Coated Classique Fountain Pen\", \"description\": \"The Meisterstück LeGrand in deep black precious resin with gold-coated details, surmounted by the white star emblem and finished with a handcrafted gold nib, evolves into Montblanc’s design icon.\", \"sku\": \"MB132578\", \"brand\": {\"@type\": \"Brand\", \"name\": \"montblanc\"}, \"image\": [\"https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/dwb24eabda/images/all-images/MB132580/QZHURSXbpEu0N--PYY-UnQ_2d65f2c5.png\"], \"offers\": {\"url\": \"https://www.montblanc.com/en-fi/meisterstuck-gold-coated-legrand-fountain-pen-MB132460.html\", \"@type\": \"Offer\", \"priceCurrency\": \"EUR\", \"price\": \"850.00\", \"availability\": \"http://schema.org/InStock\"}, \"url\": \"https://www.montblanc.com/en-fi/meisterstuck-gold-coated-classique-fountain-pen-MB132578.html\", \"size\": \"OBB\", \"color\": \"BLACK\", \"material\": \"\"}]}</script>\n<script type=\"application/ld+json\">{\"@context\": \"https://schema.org/\", \"@type\": \"BreadcrumbList\", \"itemListElement\": [{\"@type\": \"ListItem\", \"position\": 1, \"name\": \"Home\", \"item\": \"https://www.montblanc.com/en-fi\"}, {\"@type\": \"ListItem\", \"position\": 2, \"name\": \"Writing Instruments\", \"item\": \"https://www.montblanc.com/en-fi/writing-instruments\"}, {\"@type\": \"ListItem\", \"position\": 3, \"name\": \"Fountain Pens\", \"item\": \"https://www.montblanc.com/en-fi/writing-instruments/fountain-pens\"}, {\"@type\": \"ListItem\", \"position\": 4, \"name\": \"Meisterstück Gold-Coated LeGrand Fountain Pen\", \"item\": \"https://www.montblanc.com/en-fi/meisterstuck-gold-coated-legrand-fountain-pen-MB132460.html\"}]}</script>\n</head><body><div class=\"product-detail\" data-pid=\"MB132460\"></div>\n<section class=\"recommendations\"><h2>You may also like</h2>\n<div class=\"product\" data-pid=\"MB136801\"><div class=\"product-tile\" data-tracking-click-event=\"select_item\" data-tracking-event-payload=\"{&quot;ecommerce&quot;:{&quot;currency&quot;:&quot;EUR&quot;,&quot;productListItem&quot;:&quot;&quot;,&quot;productListId&quot;:&quot;&quot;,&quot;productListIndex&quot;:&quot;&quot;,&quot;items&quot;:[{&quot;item_id&quot;:&quot;MB136801&quot;,&quot;item_name&quot;:&quot;Ink Bottle, The Legend of Zodiacs The Goat, Red, 50 ml&quot;,&quot;item_brand&quot;:&quot;mtb&quot;,&quot;item_size&quot;:&quot;50 ml&quot;,&quot;item_reference&quot;:&quot;MB136801&quot;,&quot;item_available&quot;:&quot;available&quot;,&quot;price&quot;:55,&quot;item_engraved&quot;:false,&quot;item_embossed&quot;:false,&quot;item_personalized&quot;:false,&quot;quantity&quot;:1,&quot;product_image_url&quot;:&quot;https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/dw982fb1ef/images/all-images/MB136801/uccMwncRStSfW8nKV_0jtg_0983294b.png&quot;,&quot;item_collection&quot;:&quot;Fountain Pen&quot;,&quot;item_line&quot;:&quot;N/A&quot;,&quot;item_vertical&quot;:&quot;N/A&quot;,&quot;item_sellable&quot;:&quot;N/A&quot;,&quot;item_material_case&quot;:&quot;N/A&quot;,&quot;item_material_strap&quot;:&quot;N/A&quot;,&quot;item_material_leather&quot;:&quot;N/A&quot;,&quot;item_special_edition&quot;:false,&quot;item_dial&quot;:&quot;N/A&quot;,&quot;item_adjusted&quot;:&quot;N/A&quot;,&quot;item_list_name&quot;:&quot;Product pages:  - /on/demandware.store/Sites-MontblancROW-Site/en_FI/Product-Show&quot;,&quot;item_list_id&quot;:&quot;N/A&quot;,&quot;index&quot;:&quot;N/A&quot;,&quot;item_material_color&quot;:&quot;Red&quot;,&quot;item_sub_collection&quot;:&quot;Ink Bottle&quot;,&quot;item_variant&quot;:&quot;Red&quot;,&quot;item_category&quot;:&quot;Ink bottles&quot;,&quot;item_category_id&quot;:&quot;ink-bottles&quot;,&quot;uniqueEventId&quot;:32}]},&quot;name&quot;:&quot;Product List Click&quot;,&quot;event&quot;:&quot;select_item&quot;,&quot;reference&quot;:&quot;Product&quot;}\"><a href=\"/en-fi/ink-bottle-the-legend-of-zodiacs-the-goat-red-50-ml-MB136801.html\" class=\"pdp-link\"></a><div class=\"product-price price\">\n<span>\n<span class=\"sales\">\n<span class=\"value\" content=\"55.00\"></span>\n        \n\n        € 55.00\n    </span>\n</span>\n</div></div></div>\n<div class=\"product\" data-pid=\"MB136803\"><div class=\"product-tile\" data-tracking-click-event=\"select_item\" data-tracking-event-payload=\"{&quot;ecommerce&quot;:{&quot;currency&quot;:&quot;EUR&quot;,&quot;productListItem&quot;:&quot;&quot;,&quot;productListId&quot;:&quot;&quot;,&quot;productListIndex&quot;:&quot;&quot;,&quot;items&quot;:[{&quot;item_id&quot;:&quot;MB136803&quot;,&quot;item_name&quot;:&quot;Ink Bottle, Writers Edition Homage to Bram Stoker, Purple, 50 ml&quot;,&quot;item_brand&quot;:&quot;mtb&quot;,&quot;item_size&quot;:&quot;50 ml&quot;,&quot;item_reference&quot;:&quot;MB136803&quot;,&quot;item_available&quot;:&quot;available&quot;,&quot;price&quot;:55,&quot;item_engraved&quot;:false,&quot;item_embossed&quot;:false,&quot;item_personalized&quot;:false,&quot;quantity&quot;:1,&quot;product_image_url&quot;:&quot;https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/dwdbb0f518/images/all-images/MB136803/Cb0wAF_vSGOpnDByn-jCDg_6fec0c03.png&quot;,&quot;item_collection&quot;:&quot;Fountain Pen&quot;,&quot;item_line&quot;:&quot;N/A&quot;,&quot;item_vertical&quot;:&quot;N/A&quot;,&quot;item_sellable&quot;:&quot;N/A&quot;,&quot;item_material_case&quot;:&quot;N/A&quot;,&quot;item_material_strap&quot;:&quot;N/A&quot;,&quot;item_material_leather&quot;:&quot;N/A&quot;,&quot;item_special_edition&quot;:false,&quot;item_dial&quot;:&quot;N/A&quot;,&quot;item_adjusted&quot;:&quot;N/A&quot;,&quot;item_list_name&quot;:&quot;Product pages:  - /on/demandware.store/Sites-MontblancROW-Site/en_FI/Product-Show&quot;,&quot;item_list_id&quot;:&quot;N/A&quot;,&quot;index&quot;:&quot;N/A&quot;,&quot;item_material_color&quot;:&quot;Purple&quot;,&quot;item_sub_collection&quot;:&quot;Ink Bottle&quot;,&quot;item_variant&quot;:&quot;Purple&quot;,&quot;item_category&quot;:&quot;Ink bottles&quot;,&quot;item_category_id&quot;:&quot;ink-bottles&quot;,&quot;uniqueEventId&quot;:807}]},&quot;name&quot;:&quot;Product List Click&quot;,&quot;event&quot;:&quot;select_item&quot;,&quot;reference&quot;:&quot;Product&quot;}\"><a href=\"/en-fi/ink-bottle-writers-edition-homage-to-bram-stoker-purple-50-ml-MB136803.html\" class=\"pdp-link\"></a><div class=\"product-price price\">\n<span>\n<span class=\"sales\">\n<span class=\"value\" content=\"55.00\"></span>\n        \n\n        € 55.00\n    </span>\n</span>\n</div></div></div>\n</section>\n<img src=\"https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/i0.png\">\n<img src=\"https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/i1.png\">\n<img src=\"https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/i2.png\">\n</body></html>"

GRID_FRAGMENT_HTML = "<div class=\"product-item-wrapper\" data-position=\"25.0\"><div class=\"product\" data-pid=\"MB223111VG\"><div class=\"product-tile\" data-tracking-click-event=\"select_item\" data-tracking-event-payload=\"{&quot;ecommerce&quot;:{&quot;currency&quot;:&quot;EUR&quot;,&quot;productListItem&quot;:&quot;&quot;,&quot;productListId&quot;:&quot;&quot;,&quot;productListIndex&quot;:&quot;&quot;,&quot;items&quot;:[{&quot;item_id&quot;:&quot;MB223111VG&quot;,&quot;item_name&quot;:&quot;Montblanc Panorama Medium Backpack&quot;,&quot;item_brand&quot;:&quot;mtb&quot;,&quot;item_size&quot;:&quot;N/A&quot;,&quot;item_reference&quot;:&quot;MB223111VG&quot;,&quot;item_available&quot;:&quot;soldout&quot;,&quot;price&quot;:1790,&quot;item_engraved&quot;:false,&quot;item_embossed&quot;:false,&quot;item_personalized&quot;:false,&quot;quantity&quot;:1,&quot;product_image_url&quot;:&quot;https://www.montblanc.com/on/demandware.static/-/Sites-montblanc-master/default/dwdd7ed333/images/all-images/MB223111/0N8L85BORFWgngIKcZIHUQ_0219c0da.png&quot;,&quot;item_collection&quot;:&quot;Technical Fabric&quot;,&quot;item_line&quot;:&quot;N/A&quot;,&quot;item_vertical&quot;:&quot;N/A&quot;,&quot;item_sellable&quot;:&quot;N/A&quot;,&quot;item_material_case&quot;:&quot;N/A&quot;,&quot;item_material_strap&quot;:&quot;N/A&quot;,&quot;item_material_leather&quot;:&quot;Fabric&quot;,&quot;item_special_edition&quot;:false,&quot;item_dial&quot;:&quot;N/A&quot;,&quot;item_adjusted&quot;:&quot;N/A&quot;,&quot;item_list_name&quot;:&quot;listCategory.pagetemplate.otherpages:  - /on/demandware.store/Sites-MontblancROW-Site/en_FI/Search-UpdateGrid&quot;,&quot;item_list_id&quot;:&quot;N/A&quot;,&quot;index&quot;:&quot;N/A&quot;,&quot;item_material_color&quot;:&quot;khaki&quot;,&quot;item_sub_collection&quot;:&quot;City Bags&quot;,&quot;item_variant&quot;:&quot;khaki&quot;,&quot;item_category&quot;:&quot;Backpacks&quot;,&quot;item_category_id&quot;:&quot;backpacks&quot;,&quot;uniqueEventId&quot;:260}]},&quot;name&quot;:&quot;Product List Click&quot;,&quot;event&quot;:&quot;select_item&quot;,&quot;reference&quot;:&quot;Product&quot;}\"><a href=\"/en-fi/montblanc-panorama-medium-backpack-MB223111VG.html\" class=\"pdp-link\"></a><div class=\"price\">\n<span class=\"price-container\">\n<span class=\"sales\">\n            \n            € 1,790.00\n\n\n        </span>\n</span>\n</div></div></div></div>\n<img src=\"https://www.montblanc.com/on/demandware.static/x0.png\">\n<img src=\"https://www.montblanc.com/on/demandware.static/x1.png\">\n<img src=\"https://www.montblanc.com/on/demandware.static/x2.png\">"

CHROMIUM_ERROR_HTML = "<html><head><title>www.montblanc.com</title></head><body>: Contacting the system admin Checking the proxy address ERR_PROXY_CONNECTION_FAILED <button id=\"details-button\" class=\"secondary-button text-button </body></html>"


# ---------------------------------------------------------------------------
# The parser, asserted on VALUES rather than on coverage
# ---------------------------------------------------------------------------
# A column can be 100% populated and entirely wrong (CLAUDE.md §10), so every
# check below pins a figure from the real capture rather than counting
# non-nulls.

LISTING_URL = "https://www.montblanc.com/en-fi/writing-instruments?start=0&sz=24"
GIFTS_URL = "https://www.montblanc.com/en-fi/gifts?start=0&sz=24"
PRODUCT_URL = ("https://www.montblanc.com/en-fi/"
               "meisterstuck-gold-coated-legrand-fountain-pen-MB132460.html")


def check_listing_parses():
    from product_parser import parse_listing
    page = parse_listing(LISTING_HTML, LISTING_URL, page=1, sort="price-asc")
    rows = page.rows
    equal("listing: row count", len(rows), 3)

    by_sku = {r.sku: r for r in rows}
    equal("listing: skus", sorted(by_sku), ["MB132446M", "MB132453M", "MB138571"])

    row = by_sku["MB138571"]
    equal("listing: price is the JSON-LD's own number", row.price, 3780.0)
    equal("listing: currency is the written ISO code", row.currency, "EUR")
    equal("listing: price_source names the node", row.price_source, "jsonld")
    equal("listing: brand", row.brand, "montblanc")
    equal("listing: locale comes from the URL", row.locale, "en-fi")
    check("listing: url is absolute", row.url.startswith("https://www.montblanc.com/"),
          row.url)
    check("listing: url ends at the product", row.url.endswith("-MB138571.html"), row.url)


def check_a_range_is_not_a_price():
    """The 'From 515' case, which is the site's own second price shape.

    14 of 192 listing rows measured had NO `offers.price` in the JSON-LD, and
    every one of them was a variant group rendering a range. The number is
    recorded — it is true and useful — but a consumer comparing prices has to
    be able to exclude it, so it carries its own `price_source`.
    """
    from product_parser import parse_products
    rows = {r.sku: r for r in parse_products(LISTING_HTML, LISTING_URL)}

    ranged = rows["MB132446M"]
    equal("range: the minimum is recorded", ranged.price, 515.0)
    equal("range: price_source says it is a range", ranged.price_source, "dom_range")
    equal("range: currency still resolves", ranged.currency, "EUR")

    equal("range: the second one too", rows["MB132453M"].price, 545.0)
    equal("range: and its source", rows["MB132453M"].price_source, "dom_range")

    # The guard that matters: a range must never be indistinguishable from a
    # price. If this ever starts returning "dom" the column silently begins
    # claiming that a group's cheapest variant IS the product's price.
    check("range: no ranged row claims price_source 'jsonld'",
          all(r.price_source != "jsonld" for r in rows.values()
              if r.sku.endswith("M")),
          [r.price_source for r in rows.values() if r.sku.endswith("M")])


def check_stock_comes_from_the_tile_not_the_jsonld():
    """The measurement that decided where `in_stock` is read from.

    `offers.availability` was `InStock` on 192 of 192 listing rows across four
    categories, while the tiles' own `item_available` was `soldout` on 7 of
    384. Read from the JSON-LD this column is a constant; read from the tile
    it is a fact. This fixture is cut from a capture that HAS sold-out rows,
    which is the only way the check can fail honestly.
    """
    from product_parser import parse_products, _ld_offer, _ld_in_stock, ld_blocks
    from product_parser import _item_list_products

    rows = parse_products(SOLDOUT_HTML, GIFTS_URL)
    check("stock: the fixture has rows", len(rows) >= 2, len(rows))
    check("stock: at least one row is out of stock",
          any(r.in_stock is False for r in rows),
          [(r.sku, r.in_stock) for r in rows])

    # And the JSON-LD on those SAME rows says in stock — which is the whole
    # point. If this ever starts agreeing, the site changed and the comment
    # in output_writer should be re-measured rather than trusted.
    nodes = _item_list_products(ld_blocks(SOLDOUT_HTML))
    ld_says = {_ld_in_stock(_ld_offer(n)) for n in nodes}
    check("stock: the JSON-LD claims in-stock for all of them",
          ld_says == {True},
          "JSON-LD availability values: %r (if this is no longer {True}, "
          "re-measure output_writer's claim)" % (ld_says,))


def check_unknown_availability_is_none_not_true():
    """An allowlist, so a value the site adds later reads as unknown."""
    from product_parser import _TILE_STOCK
    equal("stock: available -> True", _TILE_STOCK.get("available"), True)
    equal("stock: soldout -> False", _TILE_STOCK.get("soldout"), False)
    equal("stock: anything else -> unknown", _TILE_STOCK.get("preorder"), None)


def check_a_detail_page_is_not_a_listing():
    """CLAUDE.md §20, stated as the failure rather than guarded against.

    A product page publishes `ProductGroup` and no `ItemList`, so the listing
    parser finds no structured data — and its tile fallback would then return
    the 'you may also like' carousel as the page's products. Measured on the
    real pen page before the guard existed: 18 rows, every one of them a
    neighbour, with prices, in silence.
    """
    from product_parser import parse_products, parse_product_detail

    # The fixture CARRIES a recommendation tile, which is what makes this
    # check able to fail (§21: a guard is only as good as the fixture it runs
    # against). Controlled by disabling the guard: without the carousel in
    # the fixture the check stayed GREEN with the fault planted and proved
    # nothing.
    from product_parser import _parse_tiles_only
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(PRODUCT_HTML, "html.parser")
    stealable = _parse_tiles_only(soup, PRODUCT_HTML, base_url=PRODUCT_URL,
                                  page=1, mode="category", sort=None,
                                  locale="en-fi", locale_cur="EUR")
    check("detail: the fixture carries neighbour tiles the fallback WOULD take",
          len(stealable) >= 2,
          "without them the next check passes for the wrong reason — "
          "controlled by disabling both guards, and it stayed green until "
          "this fixture carried a real carousel (§21)")
    check("detail: and they are NOT the product itself",
          all(r.sku != "MB132460" for r in stealable),
          [r.sku for r in stealable])

    equal("detail: the listing parser returns NOTHING on a product page",
          len(parse_products(PRODUCT_HTML, PRODUCT_URL)), 0)
    equal("detail: and returns nothing even with no base_url to check",
          len(parse_products(PRODUCT_HTML, "")), 0)
    equal("detail: the detail parser returns NOTHING on a listing",
          len(parse_product_detail(LISTING_HTML, LISTING_URL)), 0)


def check_product_detail_is_one_row_per_variant():
    from product_parser import parse_product_detail
    rows = parse_product_detail(PRODUCT_HTML, PRODUCT_URL)
    equal("detail: one row per variant", len(rows), 3)
    equal("detail: variants have distinct skus", len({r.sku for r in rows}), 3)
    equal("detail: every row names its group",
          {r.variant_of for r in rows}, {"MB132460"})
    equal("detail: mode", {r.mode for r in rows}, {"product"})
    equal("detail: the variant price, not the group's",
          sorted({r.price for r in rows}), [850.0])
    equal("detail: category from the breadcrumb",
          {r.category for r in rows}, {"Fountain Pens"})


def check_a_variant_url_is_its_own_not_the_groups():
    """§4's `offers.url` trap, inverted — and it is the quiet kind.

    On this site EVERY variant's `offers.url` is the GROUP's page, while its
    own `url` is the variant's. Reading `offers.url` makes all sixteen rows
    of a pen point at one address while their titles, skus and prices all
    look correct.
    """
    from product_parser import parse_product_detail, ld_blocks, _ld_offer
    rows = parse_product_detail(PRODUCT_HTML, PRODUCT_URL)

    equal("variant urls: all distinct", len({r.url for r in rows}), len(rows))
    check("variant urls: none is the group's page",
          all(not r.url.endswith("-MB132460.html") for r in rows),
          [r.url for r in rows])

    # Pin the trap itself, so a future reader can see WHY the code prefers
    # `url`: the offers.url really is the group's, on every variant.
    group = [b for b in ld_blocks(PRODUCT_HTML)
             if b.get("@type") == "ProductGroup"][0]
    offer_urls = {_ld_offer(v).get("url") for v in group.get("hasVariant") or []}
    equal("variant urls: every offers.url IS the group's (the trap)",
          len(offer_urls), 1)


def check_the_internal_nib_code_is_unwrapped():
    """`MTB_WRITING_INSTRUMENT_M` is the site's constant for exactly one value.

    Every other nib width comes back as a clean two-letter code, so writing
    the long form through leaves the M nib as the one variant that will not
    join with its siblings.
    """
    from product_parser import _variant_value
    equal("variant value: the internal constant is unwrapped",
          _variant_value("MTB_WRITING_INSTRUMENT_M"), "M")
    equal("variant value: an ordinary code is untouched",
          _variant_value("OBB"), "OBB")
    equal("variant value: a human string is untouched",
          _variant_value("Tweed Blue"), "Tweed Blue")
    equal("variant value: N/A is absence", _variant_value("N/A"), None)


def check_the_tile_join_survives_a_different_variant_suffix():
    """Measured: the ItemList and the tile can spell one product two ways.

        JSON-LD url   …-MB132288M.html
        tile          data-pid="MB132288VG"

    Joined on the full sku those never meet, and the row silently loses
    `image_url`, `in_stock`, `collection` and `color` — 3 of 71 rows on one
    live run, while the row itself looked fine.
    """
    from product_parser import base_id, _by_base, _join_tile

    equal("join: base id strips a variant suffix", base_id("MB132288M"), "MB132288")
    equal("join: and the other suffix", base_id("MB132288VG"), "MB132288")
    equal("join: a bare id is its own base", base_id("MB220310"), "MB220310")
    equal("join: a non-id is None", base_id("not-an-id"), None)

    tiles = {"MB132288VG": "the tile"}
    by_base = _by_base(tiles)
    equal("join: exact sku wins",
          _join_tile("MB132288VG", tiles, by_base), "the tile")
    equal("join: base id is the fallback",
          _join_tile("MB132288M", tiles, by_base), "the tile")

    # AMBIGUITY IS REFUSED. Two variants of one product on one page means
    # picking either would be a guess, so the row keeps the JSON-LD's answer
    # and the tile columns stay None (§8).
    two = {"MB132288VG": "first", "MB132288XX": "second"}
    equal("join: two tiles sharing a base are not guessed between",
          _join_tile("MB132288M", two, _by_base(two)), None)


def check_the_range_rows_still_get_their_tile_columns():
    """The regression the base-id join exists to prevent, end to end."""
    from product_parser import parse_products
    rows = {r.sku: r for r in parse_products(LISTING_HTML, LISTING_URL)}
    for sku in ("MB132446M", "MB132453M"):
        row = rows[sku]
        check("join: %s kept its image" % sku, row.image_url is not None, row.image_url)
        check("join: %s kept its category" % sku, row.category is not None, row.category)


def check_a_tiles_only_page_still_parses():
    """The `Search-UpdateGrid` fragment: 24 tiles, ZERO JSON-LD blocks.

    §18: which page kind server-renders its grid decides what "unknown"
    means, and on this site the two listing kinds disagree. A parser that
    only reads structured data returns nothing here and calls a served page
    empty.
    """
    from product_parser import parse_products, ld_blocks, detect_page_state
    equal("fragment: it really has no JSON-LD", len(ld_blocks(GRID_FRAGMENT_HTML)), 0)
    rows = parse_products(GRID_FRAGMENT_HTML,
                          "https://www.montblanc.com/en-fi/bags/backpacks")
    equal("fragment: the tile path still yields a row", len(rows), 1)
    equal("fragment: with its id", rows[0].sku, "MB223111VG")
    equal("fragment: and its price", rows[0].price, 1790.0)
    equal("fragment: read from the DOM", rows[0].price_source, "dom")
    equal("fragment: classified as content, not empty",
          detect_page_state(GRID_FRAGMENT_HTML, 200, "")[0], "content")


def check_page_and_position_are_threaded_through():
    from product_parser import parse_products
    p1 = parse_products(LISTING_HTML, LISTING_URL, page=1)
    p2 = parse_products(LISTING_HTML, LISTING_URL, page=2)
    equal("page: page 1 rows say page 1", {r.page for r in p1}, {1})
    equal("page: page 2 rows say page 2", {r.page for r in p2}, {2})
    equal("position: restarts at 1 on each page",
          [r.position for r in p1], [r.position for r in p2])
    pairs = {(r.page, r.position) for r in p1 + p2}
    equal("page+position: unique across a two-page run", len(pairs), len(p1) + len(p2))


def check_the_sites_own_arithmetic_is_read():
    from product_parser import (parse_listing, total_results, pages_available,
                                applied_page_size, applied_page_number,
                                page_currency)
    equal("arithmetic: result-count", total_results(LISTING_HTML), 280)
    equal("arithmetic: page size", applied_page_size(LISTING_HTML), 24)
    equal("arithmetic: page number is converted from 0-based",
          applied_page_number(LISTING_HTML), 1)
    equal("arithmetic: pages", pages_available(280, 24), 12)
    equal("arithmetic: currency from the page config",
          page_currency(LISTING_HTML), "EUR")

    # An unknown count must stay unknown rather than collapsing to zero: a
    # later slice prints no count, and reading that as 0 would cap every run
    # after page 1 at no pages at all.
    equal("arithmetic: no count means unknown, not zero",
          pages_available(None, 24), None)
    equal("arithmetic: a page with no count reports None",
          total_results("<html><body>nothing</body></html>"), None)

    page = parse_listing(LISTING_HTML, LISTING_URL, page=1, sort="price-asc")
    equal("arithmetic: ListingPage carries it", page.total_results, 280)
    equal("arithmetic: and the page count", page.pages_available, 12)


US_RESULT_COUNT_HTML = (
    '<div class="grid-header"><div class="result-count">'
    '<span> 281 Items </span></div></div>')


def check_the_result_count_survives_a_localised_noun():
    """§15's second-country rule, catching a real bug.

    The noun beside the number is localised and the number is not. The first
    version of this parser matched a LIST of nouns — Results, Ergebnisse,
    Résultats — and the first run against the US market reported
    `total_results: None`, because en-us writes "281 Items".

    A noun list is a list of the locales someone happened to think of.
    """
    from product_parser import total_results
    equal("count: the en-fi noun ('Results')", total_results(LISTING_HTML), 280)
    equal("count: the en-us noun ('Items')",
          total_results(US_RESULT_COUNT_HTML), 281)
    # Any noun at all, including one nobody has seen yet.
    equal("count: a noun this repo has never met",
          total_results('<div class="result-count"><span> 42 Tuotetta </span></div>'),
          42)
    equal("count: and a grouped number",
          total_results('<div class="result-count"><span> 1,234 Items </span></div>'),
          1234)
    equal("count: no element means unknown, never zero",
          total_results("<html><body>nothing</body></html>"), None)


def check_the_sort_recorded_is_the_one_ASKED_for():
    """The site does not publish which ordering it applied — measured.

    `ruleId` and the checked sort radio are BOTH the category's default on
    every request, whatever `srule` was sent, while the results demonstrably
    change. So the honest column is the request, and the default is recorded
    separately under a name that says what it is.

    The first version of this repo read `ruleId` as "the applied sort". It
    warned on every page of every run and stamped the wrong ordering on every
    row.
    """
    from product_parser import (parse_listing, default_sort_rule, sort_from_url,
                                SORTS, DEFAULT_SORT, SITE_DEFAULT_SORT)

    equal("sort: the page states the CATEGORY DEFAULT",
          default_sort_rule(LISTING_HTML), "recommended")
    equal("sort: which is the site's own default",
          SITE_DEFAULT_SORT, "recommended")
    equal("sort: this repo deliberately disagrees", DEFAULT_SORT, "price-asc")

    equal("sort: read back from the URL that was sent",
          sort_from_url(LISTING_URL + "&srule=FA_price-ascending"), "price-asc")
    equal("sort: no srule means the site chose", sort_from_url(LISTING_URL), None)

    asked = LISTING_URL + "&srule=FA_price-descending"
    page = parse_listing(asked, LISTING_URL, page=1)  # html arg first
    # Parse the real fixture with the asked-for URL:
    page = parse_listing(LISTING_HTML, asked, page=1)
    equal("sort: the ROW records what was asked for",
          {r.sort for r in page.rows}, {"price-desc"})
    equal("sort: and the page records the site's default separately",
          page.site_default_sort, "recommended")
    check("sort: the two are not confused",
          page.sort != page.site_default_sort, (page.sort, page.site_default_sort))


def check_url_building_and_pagination():
    from product_parser import (page_url, page_number_from_url, search_url,
                                category_url, PAGE_SIZE)

    base = "https://www.montblanc.com/en-fi/bags/backpacks"
    equal("page_url: page 1", page_url(base, 1),
          base + "?start=0&sz=24")
    equal("page_url: page 3", page_url(base, 3),
          base + "?start=48&sz=24")
    equal("page_url: honours a page size", page_url(base, 3, 48),
          base + "?start=96&sz=48")
    check("page_url: adds the sort rule",
          "srule=FA_price-ascending" in page_url(base, 2, 24, "price-asc"),
          page_url(base, 2, 24, "price-asc"))

    # REPLACES rather than appends, and keeps the filters a URL already
    # carries — dropping those would silently widen the run.
    filtered = base + "?prefn1=ProductLabel&prefv1=New&start=0&sz=24"
    built = page_url(filtered, 2)
    check("page_url: keeps existing refinements", "prefn1=ProductLabel" in built, built)
    equal("page_url: does not duplicate start", built.count("start="), 1)
    equal("page_url: does not duplicate sz", built.count("sz="), 1)
    check("page_url: advanced the offset", "start=24" in built, built)

    equal("page_number_from_url: round-trips", page_number_from_url(page_url(base, 5)), 5)
    equal("page_number_from_url: no start means unknown",
          page_number_from_url(base), None)

    try:
        page_url(base, 0)
        check("page_url: refuses page 0", False, "no exception")
    except ValueError:
        check("page_url: refuses page 0", True)

    s = search_url("fountain pen", "en-gb")
    check("search_url: locale in the path", "/en-gb/search" in s, s)
    check("search_url: query is encoded", "q=fountain%20pen" in s or "q=fountain+pen" in s, s)

    c = category_url("bags/backpacks", "en-us", sort="price-asc")
    check("category_url: builds under the locale",
          c.startswith("https://www.montblanc.com/en-us/bags/backpacks"), c)


def check_supported_urls_are_refused_with_a_true_reason():
    from product_parser import is_supported_url, locale_is_known, KNOWN_LOCALES

    ok, why = is_supported_url("https://www.montblanc.com/en-fi/bags/backpacks")
    check("url: a real listing is accepted", ok, why)

    ok, why = is_supported_url("https://example.com/en-us/x")
    check("url: another host is refused", not ok)
    check("url: and the reason names the host", "example.com" in why, why)
    check("url: with no rotting count in it",
          not any(ch.isdigit() for ch in why), why)

    ok, why = is_supported_url("https://www.montblanc.com/")
    check("url: the bare host is refused", not ok)
    check("url: because it has no locale", "locale" in why.lower(), why)

    # The reason must not repeat the URL: every caller already prefixes it,
    # and two copies of the address in one sentence reads like a bug.
    for bad in ("https://www.montblanc.com/", "ftp://montblanc.com/en-us/x",
                "https://example.com/en-us/x"):
        ok, why = is_supported_url(bad)
        check("url: the refusal for %r does not repeat the address" % bad,
              bad not in why, why)

    # THE ONE THAT COST A LIVE RUN. `en-fi` is a real market that the site
    # geo-redirects Finnish visitors to, and it is absent from the country
    # selector's own list because that list omits whichever locale it was
    # fetched in. An allowlist refused it with a reason that was FALSE.
    ok, why = is_supported_url("https://www.montblanc.com/en-fi/bags")
    check("url: en-fi is accepted (it is real, and it was once refused)", ok, why)
    check("url: en-fi is in the known set now", locale_is_known("en-fi"))

    # A locale not in the snapshot is ACCEPTED, because the snapshot is known
    # to be incomplete. It is the caller's job to warn, not to refuse.
    ok, why = is_supported_url("https://www.montblanc.com/pt-br/bolsas")
    check("url: an unlisted but well-formed locale is accepted", ok, why)
    check("url: while reporting that it was not in the snapshot",
          not locale_is_known("pt-br"))
    check("url: the snapshot is a tuple of locales", len(KNOWN_LOCALES) > 50)


def check_path_shapes():
    from product_parser import (is_product_url, is_search_url, sku_from_url,
                                category_from_url, locale_from_url)

    prod = ("https://www.montblanc.com/en-fi/"
            "montblanc-companion-rectangular-backpack--MB222875VG.html")
    check("path: a product page is recognised", is_product_url(prod))
    check("path: a listing is not", not is_product_url(
        "https://www.montblanc.com/en-fi/bags/backpacks"))
    equal("path: sku from the URL", sku_from_url(prod), "MB222875VG")
    equal("path: a plain id", sku_from_url(
        "https://www.montblanc.com/en-fi/x-MB220310.html"), "MB220310")
    equal("path: the M-suffixed groups", sku_from_url(
        "https://www.montblanc.com/en-fi/x-MB132446M.html"), "MB132446M")
    equal("path: no id means None", sku_from_url(
        "https://www.montblanc.com/en-fi/bags"), None)

    equal("path: locale", locale_from_url(prod), "en-fi")
    equal("path: category is the LAST segment", category_from_url(
        "https://www.montblanc.com/en-fi/bags/backpacks"), "backpacks")
    equal("path: a product page has no category", category_from_url(prod), None)
    equal("path: a non-catalogue route is not a category", category_from_url(
        "https://www.montblanc.com/en-fi/customer-service/contact-us.html"), None)

    search = "https://www.montblanc.com/en-fi/search?q=fountain+pen"
    check("path: a search URL is recognised", is_search_url(search))
    equal("path: and its 'category' is the query",
          category_from_url(search), "fountain pen")

    # Japanese slugs are percent-encoded, and the id at the end is still
    # ASCII — so the sku pattern has to survive a URL that is mostly escapes.
    jp = ("https://www.montblanc.com/ja-jp/"
          "%E3%83%A2%E3%83%B3%E3%83%96%E3%83%A9%E3%83%B3-MB223087.html")
    equal("path: a percent-encoded Japanese product still yields its sku",
          sku_from_url(jp), "MB223087")
    equal("path: and its locale", locale_from_url(jp), "ja-jp")


def check_prices_in_every_convention_the_site_uses():
    from product_parser import _prices_in, _normalize_amount

    equal("price: EUR with a space", _prices_in("€ 2,000.00")[0], [2000.0])
    equal("price: and its currency", _prices_in("€ 2,000.00")[1], "EUR")
    equal("price: USD with no space", _prices_in("$1,990.00", "USD")[0], [1990.0])
    equal("price: GBP", _prices_in("£1,700.00")[1], "GBP")
    # JPY has no decimals and groups with a comma, so "49,500" must read as
    # a thousands grouping rather than as 49.5. That falls out of the family
    # rule (exactly three trailing digits) without a special case.
    equal("price: JPY without decimals", _prices_in("¥ 49,500")[0], [49500.0])
    equal("price: the European convention still works",
          _normalize_amount("1.234,56"), 1234.56)
    equal("price: and the space-grouped one", _normalize_amount("1 234,56"), 1234.56)

    # A bare dollar sign cannot name itself: Montblanc sells in CAD, AUD,
    # NZD, SGD, HKD and TWD too. Without a market to resolve it, the currency
    # is None rather than a plausible "USD" (§4's ladder, rung 4 vs 5).
    equal("price: a bare $ with no market resolves to nothing",
          _prices_in("$1,990.00")[1], None)
    equal("price: with a market it resolves to that market",
          _prices_in("$1,990.00", "CAD")[1], "CAD")

    # An allowlist, not a bare [A-Z]{3}: a nib width beside a number must not
    # become a currency.
    equal("price: a nib width is not a currency", _prices_in("EF 850")[1], None)
    equal("price: a real ISO code is", _prices_in("CHF 850")[1], "CHF")


def check_fixtures_carry_no_session_material():
    """§10: a real page dump carries the session that fetched it.

    A sibling repo committed three `sessionId` values, their CSRF tokens and
    a real customer's display name, profile permalink and review text. None
    of that granted anything — the session material was anonymous and
    expired — and it still did not belong in a public repo.

    Montblanc is the easy case: it publishes products, not people, and no
    capture here carries a name, a review or a login. So this guards the
    SHAPE rather than any literal, which is the half that keeps working when
    the next capture is taken (§10: guard with PATTERNS, not the old
    values).
    """
    fixtures = {
        "LISTING_HTML": LISTING_HTML,
        "SOLDOUT_HTML": SOLDOUT_HTML,
        "PRODUCT_HTML": PRODUCT_HTML,
        "GRID_FRAGMENT_HTML": GRID_FRAGMENT_HTML,
        "CHROMIUM_ERROR_HTML": CHROMIUM_ERROR_HTML,
    }
    patterns = {
        "a session id": r"sessionId|session_id|JSESSIONID|dwsid",
        "a CSRF token": r"csrf[_-]?token|anti-?csrftoken",
        "a bearer token": r"[Bb]earer\s+[A-Za-z0-9._-]{16,}",
        "a 32-hex key": r"\b[0-9a-f]{32}\b",
        "an email address": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "credentials in a URL": r"[a-z]+://[^\s/@]+:[^\s/@]+@",
    }
    for fixture_name, text in fixtures.items():
        for label, pattern in patterns.items():
            hit = re.search(pattern, text)
            check("%s carries no %s" % (fixture_name, label), hit is None,
                  "matched %r — scrub the capture before committing it"
                  % (hit.group(0)[:40] if hit else ""))


def check_page_states_on_real_captures():
    from product_parser import detect_page_state

    equal("state: a listing is content",
          detect_page_state(LISTING_HTML, 200, "")[0], "content")
    equal("state: a product page is content",
          detect_page_state(PRODUCT_HTML, 200, "")[0], "content")
    equal("state: the tiles-only fragment is content",
          detect_page_state(GRID_FRAGMENT_HTML, 200, "")[0], "content")

    # A page the site served that has no products on it — a hub, or a `start=`
    # past the end. An ANSWER, not a block: reporting it as blocked sends the
    # reader hunting for a proxy problem that does not exist.
    served_empty = ("<html><body>"
                    + '<img src="https://www.montblanc.com/on/demandware.static/a.png">' * 3
                    + "</body></html>")
    equal("state: served with no products is empty, not blocked",
          detect_page_state(served_empty, 200, "")[0], "empty")

    equal("state: a 403 is blocked", detect_page_state("<html/>", 403, "")[0], "blocked")
    equal("state: a 500 is unknown, not blocked",
          detect_page_state("<html/>", 503, "")[0], "unknown")
    equal("state: an empty body is unknown",
          detect_page_state("", None, "")[0], "unknown")

    # CHROMIUM'S OWN ERROR PAGE — the case inverted detection is for (§18).
    # It carries the SITE'S OWN HOSTNAME in the title, so a title check calls
    # it a real page, and no vendor marker of any kind. Only "was this built
    # out of Montblanc's own assets?" answers correctly.
    equal("state: Chromium's network-error page is blocked",
          detect_page_state(CHROMIUM_ERROR_HTML, None, "")[0], "blocked")
    check("state: and it really does wear the site's hostname",
          "www.montblanc.com" in CHROMIUM_ERROR_HTML)


def check_markers_do_not_match_a_page_montblanc_serves():
    """§18: count every candidate on a page you KNOW is good, first.

    This is the check that changed the marker set. Montblanc is Akamai-
    fronted and references `akamaihd.net` on every page it serves, so a bare
    `akamai` marker reports a served catalogue as a block — the exact
    tokopedia-scraper failure that put the rule in CLAUDE.md.
    """
    from product_parser import BOT_CHALLENGE_MARKERS, detect_bot_challenge

    good = {"listing": LISTING_HTML, "product": PRODUCT_HTML,
            "grid fragment": GRID_FRAGMENT_HTML, "sold-out listing": SOLDOUT_HTML}
    for label, html_text in good.items():
        hit = detect_bot_challenge(html_text)
        check("markers: none fires on a %s Montblanc served" % label,
              hit is None, "matched %r" % hit)

    # The two that are deliberately ABSENT, each for its own measured reason.
    lowered = [m.lower() for m in BOT_CHALLENGE_MARKERS]
    check("markers: bare 'akamai' is not a marker (it is on every good page)",
          "akamai" not in lowered and "akamaihd" not in lowered, lowered)
    check("markers: cf-turnstile is not a marker (§8/§19: the Scraping "
          "Browser's own extension injects it)",
          "cf-turnstile" not in lowered, lowered)
    check("markers: challenges.cloudflare.com is the one kept instead",
          "challenges.cloudflare.com" in lowered, lowered)


def check_a_marker_survives_both_encodings():
    """§20: the same refusal page reaches a parser spelled two ways.

    An edge entity-escapes the punctuation; a browser parses it and
    serialises it back out plain. A literal marker matches the browser
    engines and silently misses the HTTP client.
    """
    from product_parser import detect_bot_challenge, BOT_CHALLENGE_MARKERS

    plain = "<html><body>refused: https://errors.edgesuite.net</body></html>"
    escaped = ("<html><body>refused: "
               "https&#58;&#47;&#47;errors&#46;edgesuite&#46;net</body></html>")

    # The escaped fixture deliberately carries NOTHING but the escaped form —
    # no `Reference&#32;#`, which is itself a listed marker and would match
    # without any unescaping at all. Controlled by removing the unescape
    # step: with the richer fixture the check stayed GREEN, passing for the
    # wrong reason (§21). This is the fixture that actually exercises it.
    check("encodings: the fixture does not contain a marker verbatim",
          not any(m.lower() in escaped.lower() for m in BOT_CHALLENGE_MARKERS),
          "the escaped fixture matches a marker without unescaping, so the "
          "next check cannot fail")

    check("encodings: the plain spelling is caught", detect_bot_challenge(plain))
    check("encodings: the entity-escaped one too", detect_bot_challenge(escaped))


def check_positive_asset_detection():
    from product_parser import references_own_assets, _MIN_ASSET_REFERENCES

    for label, html_text in (("listing", LISTING_HTML), ("product", PRODUCT_HTML),
                             ("fragment", GRID_FRAGMENT_HTML)):
        n = references_own_assets(html_text)
        check("assets: a %s references the site's own host" % label,
              n >= _MIN_ASSET_REFERENCES, n)

    equal("assets: Chromium's error page references them zero times",
          references_own_assets(CHROMIUM_ERROR_HTML), 0)
    # The threshold has to sit below the SMALLEST real response, which is the
    # grid fragment (24 references on the untrimmed capture).
    check("assets: the threshold is above 1 and well below a real page",
          1 < _MIN_ASSET_REFERENCES <= 5, _MIN_ASSET_REFERENCES)


def check_the_transport_refusal_is_classified():
    """Montblanc's real refusal is not a page, so it needs its own classifier.

    Measured 2026-09-17: a request whose User-Agent names an HTTP client
    library gets the connection killed. `curl` reports an HTTP/2
    INTERNAL_ERROR; `requests` reports a ReadTimeout, which is
    indistinguishable from a slow network unless you know.
    """
    import page_flow

    equal("transport: a read timeout is a refusal on this site",
          page_flow.classify_transport_error(
              TimeoutError("Read timed out. (read timeout=30)")), "refused")
    equal("transport: so is an HTTP/2 stream reset",
          page_flow.classify_transport_error(
              Exception("curl: (92) HTTP/2 stream 1 was not closed cleanly: "
                        "INTERNAL_ERROR (err 2)")), "refused")
    # A DEAD PROXY IS NOT A TIMEOUT (§8): the two want opposite responses, so
    # they must not collapse into one name.
    equal("transport: a dead proxy is its own thing",
          page_flow.classify_transport_error(
              Exception("net::ERR_PROXY_CONNECTION_FAILED")), "proxy")
    equal("transport: anything else is an ordinary error",
          page_flow.classify_transport_error(
              Exception("net::ERR_NAME_NOT_RESOLVED")), "error")


def check_the_user_agent_denylist_is_known_before_it_bites():
    """The pre-flight that turns a 30-second timeout into a sentence."""
    import page_flow
    for ua in ("", "curl/8.5.0", "python-requests/2.31.0", "Python-urllib/3.11",
               "Go-http-client/2.0"):
        check("UA: %r is known to be refused" % (ua or "<none>",),
              page_flow.ua_will_be_refused(ua))
    # Measured as SERVED, so they must not be refused pre-emptively.
    for ua in ("Mozilla/5.0 (X11; Linux x86_64) Chrome/140.0.0.0",
               "Wget/1.21", "Scrapy/2.11", "foo"):
        check("UA: %r is served, so it is not flagged" % ua,
              not page_flow.ua_will_be_refused(ua))


def check_state_policy():
    import page_flow

    equal("policy: content is parsed", page_flow.should_parse("content"), True)
    equal("policy: content is not retried", page_flow.should_retry("content"), False)

    # An empty page is an ANSWER. It must not be retried and must not be
    # reported as blocked.
    equal("policy: empty is parsed", page_flow.should_parse("empty"), True)
    equal("policy: empty is not retried", page_flow.should_retry("empty"), False)
    equal("policy: empty is not blocked", page_flow.counts_as_blocked("empty"), False)

    # A refusal offers nothing to solve, so it must not spend money.
    equal("policy: blocked never pays a solver",
          page_flow.should_solve("blocked"), False)
    equal("policy: blocked is blocked", page_flow.counts_as_blocked("blocked"), True)

    # A rendered widget IS a test, and is the one state that pays.
    equal("policy: a captcha may be solved", page_flow.should_solve("captcha"), True)

    equal("policy: unknown waits rather than spending",
          (page_flow.should_retry("unknown"), page_flow.should_solve("unknown")),
          (True, False))
    # An unrecognised state must fall back to the cautious one rather than
    # raising, so a new state name cannot crash a run mid-flight.
    equal("policy: an unknown state name falls back to 'unknown'",
          page_flow.should_solve("something-new"), False)


def check_pagination_addressability_is_asked_per_url():
    import page_flow
    check("pagination: a category listing is addressable",
          page_flow.pagination_is_addressable(
              "https://www.montblanc.com/en-fi/bags/backpacks"))
    check("pagination: a search listing is addressable",
          page_flow.pagination_is_addressable(
              "https://www.montblanc.com/en-fi/search?q=pen"))
    check("pagination: a PRODUCT page is not — it is one page",
          not page_flow.pagination_is_addressable(
              "https://www.montblanc.com/en-fi/x-MB1.html"))


def check_pagination_is_planned_from_the_sites_own_number():
    import page_flow
    equal("plan: clamped to what the site says exists",
          page_flow.pages_to_plan(50, 12), 12)
    equal("plan: a smaller request is honoured", page_flow.pages_to_plan(3, 12), 3)
    # An unknown count is not a zero: where page 1 stated nothing, the run
    # discovers the end from the data (§7 layer 3).
    equal("plan: unknown means the request stands",
          page_flow.pages_to_plan(5, None), 5)
    equal("plan: never less than one page", page_flow.pages_to_plan(0, 12), 1)
    equal("plan: from a result COUNT rather than a page count",
          page_flow.plan_from_total(50, 280, 24), 12)


def check_policy_constants_have_a_consumer():
    """§17: a policy constant nothing reads is the same defect as dead code.

    `RETRY_ON_BLOCKED` carried a paragraph of measured justification in a
    sibling repo and NO engine consulted it, so setting it False changed
    nothing while the prose read like enforcement.
    """
    import page_flow
    sources = {}
    for name in ("playwright_scraper", "puppeteer_scraper", "selenium_scraper"):
        path = os.path.join(HERE, name + ".py")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                sources[name] = f.read()
    check("policy: the engines are readable", len(sources) == 3, sorted(sources))

    for const in ("RETRY_ON_BLOCKED", "BLOCK_RETRIES_WITHOUT_POOL",
                  "SOLVES_PER_PAGE"):
        readers = [n for n, s in sources.items() if const in s]
        check("policy: %s is read by an engine" % const, readers, "no consumer")


def check_row_schema():
    from output_writer import Product, ROW_CLASS_BY_MODE, UNIQUE_BY_SKU_MODES
    names = [f.name for f in fields(Product)]
    equal("the family prefix is byte-identical and in order (§9)",
          names[:5], ["source", "scraped_at", "url", "sku", "title"])

    # The commerce columns this site DOES have. Montblanc is a shop, so the
    # family's price fields are present rather than dropped.
    for present in ("brand", "price", "currency", "in_stock", "image_url",
                    "category", "price_source"):
        check("the commerce column %r is present" % present, present in names)

    # And the ones measured ABSENT, each with its count in output_writer's
    # docstring. §9 says removing a column needs the measurement written
    # down; this pins that they stay removed until someone re-measures.
    for gone in ("original_price", "discount_pct", "lowest_price_30d",
                 "rating", "review_count", "ean", "gtin", "engraved"):
        check("the column %r is absent, not null-forever" % gone,
              gone not in names,
              "if this is back, output_writer's measurement should be too")

    # Site-specific columns go at the END of the row (§9), after the family's.
    equal("site-specific columns come last",
          names[-9:], ["locale", "base_sku", "collection", "sub_collection",
                       "color", "size", "special_edition", "variant_of", "sort"])

    equal("every mode maps to a row class",
          sorted(ROW_CLASS_BY_MODE), ["category", "product", "search"])
    equal("every mode is one row per sku",
          sorted(UNIQUE_BY_SKU_MODES), ["category", "product", "search"])
    equal("all three modes share one class",
          len({c for c in ROW_CLASS_BY_MODE.values()}), 1)


def check_csv_and_json_writers():
    from output_writer import Product, write_csv, write_json
    import product_parser as P
    rows = P.parse_listing(LISTING_HTML, LISTING_URL).rows
    check("the fixture produced rows to write", len(rows) > 0, len(rows))
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = os.path.join(tmp, "out.csv")
        write_csv(rows, csv_path, row_cls=Product)
        with open(csv_path, encoding="utf-8") as f:
            reader = list(csv.reader(f))
        equal("CSV header matches the dataclass, in order",
              reader[0], [f.name for f in fields(Product)])
        equal("CSV holds every row", len(reader) - 1, len(rows))
        check("no Python list repr leaked into the CSV",
              not any(cell.startswith("[") for row in reader[1:] for cell in row))

        empty_csv = os.path.join(tmp, "empty.csv")
        write_csv([], empty_csv, row_cls=Product)
        with open(empty_csv, encoding="utf-8") as f:
            header = list(csv.reader(f))
        equal("an EMPTY csv still carries its header", len(header), 1)
        equal("...and it is the right one", header[0],
              [f.name for f in fields(Product)])

        json_path = os.path.join(tmp, "out.json")
        write_json(rows, json_path)
        loaded = json.load(open(json_path, encoding="utf-8"))
        equal("JSON holds every row", len(loaded), len(rows))
        equal("JSON keys are the dataclass fields, in order",
              list(loaded[0].keys()), [f.name for f in fields(Product)])
        equal("JSON and CSV agree on the column ORDER",
              list(loaded[0].keys()), reader[0])


def check_exit_codes():
    import output_writer as O
    equal("0 ok / 1 crash / 2 usage / 3 blocked / 4 empty / 5 api / 6 partial",
          (O.EXIT_BLOCKED, O.EXIT_NO_PRODUCTS, O.EXIT_API_ERROR, O.EXIT_PARTIAL),
          (3, 4, 5, 6))
    check("page_cap_reached is a COMPLETE stop reason",
          "page_cap_reached" in O.COMPLETE_STOP_REASONS)
    check("single_page_mode is complete by construction",
          "single_page_mode" in O.COMPLETE_STOP_REASONS)
    check("no_new_products is complete",
          "no_new_products" in O.COMPLETE_STOP_REASONS)


def check_a_run_that_finds_nothing_writes_nothing():
    """Never replace last night's good output with []."""
    from output_writer import save
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "out")
        with open(prefix + ".json", "w", encoding="utf-8") as f:
            f.write('[{"sku": "yesterday"}]')
        code = save([], prefix, "json", allow_empty=False)
        equal("an empty run exits 4", code, 4)
        equal("...and leaves the previous good file alone",
              open(prefix + ".json", encoding="utf-8").read(),
              '[{"sku": "yesterday"}]')
        code = save([], prefix, "json", allow_empty=True)
        equal("--allow-empty WRITES the empty file...", 
              json.load(open(prefix + ".json", encoding="utf-8")), [])
        # ...and still reports exit 4. Pinned deliberately (§10: pin a known
        # behaviour rather than half-guarding it): "zero businesses" is true
        # whether or not the file was written, and a caller that wanted the
        # file still wants to know the result was empty.
        equal("...and still reports exit 4, because it IS empty", code, 4)


def check_page_and_position_are_unique_across_pages():
    """One line, and the column is worthless without it: `position` restarts
    at 1 on every page."""
    import product_parser as P
    page1 = P.parse_listing(LISTING_HTML, LISTING_URL, page=1).rows
    page2 = P.parse_listing(LISTING_HTML, LISTING_URL, page=2).rows
    pairs = [(r.page, r.position) for r in page1 + page2]
    equal("page+position is unique across a multi-page run",
          len(set(pairs)), len(pairs))
    equal("page 2's rows really say page 2",
          sorted({r.page for r in page2}), [2])


def check_sidecar_shape():
    from output_writer import run_meta
    meta = run_meta(status="complete", stop_reason="page_cap_reached",
                    pages_requested=50, pages_completed=12, pages_failed=[],
                    products=280, mode="category", source="montblanc.com",
                    start_url="https://www.montblanc.com/en-fi/writing-instruments",
                    final_url="https://www.montblanc.com/en-fi/writing-instruments?start=264&sz=24",
                    extra={"total_results": 280, "pages_available": 12,
                           "page_size": 24, "locale": "en-fi",
                           "sort_requested": "price-asc",
                           "site_default_sort": "recommended"})
    for key in ("status", "stop_reason", "pages_requested", "pages_completed",
                "pages_failed", "mode", "source"):
        check("the sidecar records %r" % key, key in meta)
    equal("the sidecar carries the site's own total", meta["total_results"], 280)
    equal("...and the market the prices belong to", meta["locale"], "en-fi")
    equal("...and which ordering was asked for", meta["sort_requested"], "price-asc")
    equal("...and what a visitor would have got instead",
          meta["site_default_sort"], "recommended")
    equal("pages_failed is a LIST of numbers, not a count",
          isinstance(meta["pages_failed"], list), True)

    # No `capped_by_site` here, and the ABSENCE is the finding: this site
    # imposes no page cap, so a complete run really is the whole category.
    check("no capped_by_site on a site that does not cap",
          "capped_by_site" not in meta, sorted(meta))


# ---------------------------------------------------------------------------
# The engines — the five checks CLAUDE.md §17 says to steal
# ---------------------------------------------------------------------------

ENGINES = ("playwright_scraper", "selenium_scraper", "puppeteer_scraper")
DRIVER_IMPORTS = {
    "playwright_scraper": "playwright",
    "selenium_scraper": "selenium",
    "puppeteer_scraper": "pyppeteer",
}


def _import_engine(name):
    try:
        return __import__(name)
    except ImportError as e:
        skip(name, "engine library absent (%s)" % e)
        return None


def check_engines_import_their_driver_at_module_level():
    """For the guarded imports above to MEAN anything.

    A sibling repo imported `launch`/`connect` inside the launch path, so the
    module imported cleanly with no pyppeteer installed: the group never
    skipped, and the CI job that exists to fail on unexpected skips could not
    have caught a broken import. It also let CI run against a stub version
    for a while without anything noticing. This drifts back silently, so it
    is asserted with an `ast` walk rather than trusted.
    """
    for module, driver in DRIVER_IMPORTS.items():
        path = os.path.join(HERE, module + ".py")
        if not os.path.exists(path):
            check("%s exists" % module, False)
            continue
        tree = ast.parse(open(path, encoding="utf-8").read())
        top_level = set()
        for node in tree.body:          # module level ONLY
            if isinstance(node, ast.Import):
                top_level.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level.add(node.module.split(".")[0])
        check("%s imports %s at MODULE level" % (module, driver),
              driver in top_level,
              "top-level imports: %s" % sorted(top_level))


def check_shared_calls_bind_against_the_real_signature():
    """§17's check #1, and the one that earns its keep.

    A sibling repo shipped `classify(html, url=…)` in two of three engines
    against a callee taking `status` second, and BOTH crashed on their first
    fetch — invisible to import, --help, compileall, the undefined-name walk
    and 400+ green assertions, because none of those calls a function the way
    a live run does.

    This walks every engine's AST for calls into the shared modules and binds
    each one against the callee's real signature.
    """
    import page_flow
    import product_parser
    import output_writer
    targets = {"page_flow": page_flow, "product_parser": product_parser,
               "output_writer": output_writer}
    bound = 0
    for module in ENGINES + ("scraper_api_client",):
        path = os.path.join(HERE, module + ".py")
        if not os.path.exists(path):
            continue
        source = open(path, encoding="utf-8").read()
        tree = ast.parse(source)
        # Which shared names this file imported directly (`from x import y`).
        direct = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in targets:
                for alias in node.names:
                    direct[alias.asname or alias.name] = (
                        targets[node.module], alias.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            owner = attr = None
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id in targets:
                    owner, attr = targets[func.value.id], func.attr
            elif isinstance(func, ast.Name) and func.id in direct:
                owner, attr = direct[func.id]
            if owner is None:
                continue
            # A name that is NOT THERE is the loudest possible failure and
            # this check used to swallow it: `getattr(..., None)` returned
            # None, `not callable(None)` was true, and the call was skipped.
            # Three calls into a page_flow API that does not exist in this
            # repo -- comparable(), next_page_selector(),
            # next_page_candidates(), all of them Tokopedia's, all arriving
            # with copied code -- sat in two engines under a green run of
            # this very function. Absent is not "nothing to bind".
            if not hasattr(owner, attr):
                check("%s.%s exists (called from %s:%d)"
                      % (getattr(owner, "__name__", owner), attr,
                         module + ".py", node.lineno),
                      False,
                      "the engine calls a name the shared module does not "
                      "define; a live run reaches this as AttributeError")
                continue
            callee = getattr(owner, attr)
            if not callable(callee) or inspect.isclass(callee):
                continue
            try:
                signature = inspect.signature(callee)
            except (TypeError, ValueError):
                continue
            positional = [inspect.Parameter.empty] * len(node.args)
            keywords = {}
            for kw in node.keywords:
                if kw.arg is None:          # **kwargs — cannot be checked here
                    keywords = None
                    break
                keywords[kw.arg] = inspect.Parameter.empty
            if keywords is None:
                continue
            try:
                signature.bind(*positional, **keywords)
                bound += 1
            except TypeError as e:
                check("%s:%d %s.%s(...) binds against its real signature"
                      % (module, node.lineno, owner.__name__, attr),
                      False, "%s; signature is %s" % (e, signature))
    check("every shared-module call in every engine binds (%d checked)" % bound,
          bound > 40, "only %d calls were checked — is the walk finding them?"
          % bound)


def _argparse_flags(module_name):
    """Every --flag a module's parser defines, without running the CLI."""
    path = os.path.join(HERE, module_name + ".py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    # Only calls on the argparse parser itself. A browser's option object
    # also has `add_argument`, and counting Chrome's own switches
    # (`--no-sandbox`, `--window-size=…`) as CLI flags made this check
    # compare nonsense.
    parsers = {"p"}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr in ("add_argument_group",
                                             "add_mutually_exclusive_group")):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    parsers.add(target.id)
    flags = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in parsers):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                        and arg.value.startswith("--"):
                    flags.add(arg.value)
    return flags


# The family's flag contract (CLAUDE.md §9), plus this repo's own additions.
CONTRACT_FLAGS = {
    "--url", "--pages", "--category", "--format", "--out", "--delay",
    "--retries", "--retry-delay", "--concurrency", "--proxy", "--proxy-file",
    "--proxy-rotate", "--proxy-shuffle", "--proxy-block-retries",
    "--twocaptcha-key", "--captcha-api", "--solve-captcha", "--min-score",
    "--cdp-endpoint", "--allow-empty", "--dump-html",
}
# The five flags CLAUDE.md §9 omitted for months while nearly every repo in
# the family shipped them, plus this site's own additions.
#
# `--fingerprint`, `--fp-tags`, `--fp-country` and `--mode` are in 17 of the
# 18 repos counted; `--locale` is in 16. Re-derive rather than trusting this
# comment (§13):
#
#   grep -ohE '"--[a-z0-9-]+"' */playwright_scraper.py | sort | uniq -c | sort -rn
FAMILY_FLAGS = {"--fingerprint", "--fp-tags", "--fp-country", "--locale",
                "--mode"}

# Site-specific, and each one earns its place:
#   --sort        the ordering decides WHICH products are in the file
#   --query       builds a /search URL without hand-assembling one
#   --page-size   the site's `sz`, which it honours up to at least 96
SITE_FLAGS = {"--sort", "--query", "--page-size"}


def check_engine_flag_sets():
    """§17's check #2: against the contract AND against each other, both ways.

    A missing flag fails; so does closing a difference the README documents.
    """
    sets = {}
    for module in ENGINES:
        if not os.path.exists(os.path.join(HERE, module + ".py")):
            continue
        sets[module] = _argparse_flags(module)
    for module, flags in sets.items():
        missing = (CONTRACT_FLAGS | FAMILY_FLAGS | SITE_FLAGS) - flags
        check("%s defines every contract flag" % module, not missing,
              "missing %s" % sorted(missing))
    # The ONE documented difference: pyppeteer downloads its own Chromium
    # and could not launch it on the development machine, so it needs a way
    # to point at another one. Its twins have no equivalent because they do
    # not ship a browser. Listed here so that closing the difference — or
    # growing a second one — fails the build (§17).
    DOCUMENTED_DIFFERENCES = {"puppeteer_scraper": {"--chromium-path"}}
    names = sorted(sets)
    for i in range(len(names) - 1):
        a, b = names[i], names[i + 1]
        only_a = sets[a] - sets[b] - DOCUMENTED_DIFFERENCES.get(a, set())
        only_b = sets[b] - sets[a] - DOCUMENTED_DIFFERENCES.get(b, set())
        check("%s and %s define the same flags" % (a, b),
              not only_a and not only_b,
              "only in %s: %s; only in %s: %s"
              % (a, sorted(only_a), b, sorted(only_b)))


BANNED_FLAGS = ("--antidetect", "--country-code")


def check_banned_and_removed_flags():
    """Scoped to the ENGINES.

    `--country` is absent here — the flag CLAUDE.md §10 bans outright — and
    `--locale` takes its place, because on Montblanc the market really is a
    property of the URL rather than of the browser.

    That makes the ban's REASON bite harder than usual: the locale is a path
    segment, so a `--locale` that disagreed with a `--url` would silently
    read a different market, and on this site a different market is a
    different PRICE (EUR 2000 on en-fi against EUR 1900 on de-de for one
    backpack). So the flag is refused alongside --url rather than merged,
    and this check pins that refusal exists in every engine.
    """
    for module in ENGINES:
        path = os.path.join(HERE, module + ".py")
        if not os.path.exists(path):
            continue
        source = open(path, encoding="utf-8").read()
        for flag in BANNED_FLAGS:
            check("%s does not define %s" % (module, flag),
                  '"%s"' % flag not in source)
        check("%s does not define the banned --country" % module,
              '"--country"' not in source,
              "§10 bans it: it could disagree with the URL")
        check("%s refuses --locale alongside --url" % module,
              "--url already carries its locale" in source,
              "the refusal that keeps --locale from contradicting the URL "
              "is missing — on this site that means a different price")


def check_undefined_names_in_every_module():
    """§10: compileall proves a file PARSES, not that its names RESOLVE.

    A live run of a sibling repo's pyppeteer engine died with NameError on a
    line reached only while fetching, after an import had been removed — the
    module imported cleanly, --help worked, compileall passed and CI was
    green. Kept COARSE (pooled bindings, no scope tracking) so it
    under-reports rather than inventing problems.
    """
    import builtins
    modules = [f for f in sorted(os.listdir(HERE))
               if f.endswith(".py") and f != "smoke_test.py"]
    for filename in modules:
        tree = ast.parse(open(os.path.join(HERE, filename), encoding="utf-8").read())
        # Module-level dunders exist without being assigned anywhere.
        defined = set(dir(builtins)) | {"__file__", "__name__", "__doc__",
                                        "__package__", "__spec__"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    defined.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                defined.add(node.id)
            elif isinstance(node, ast.arg):
                defined.add(node.arg)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                defined.add(node.name)
            elif isinstance(node, ast.alias) and node.asname:
                defined.add(node.asname)
        used = {n.id for n in ast.walk(tree)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        unresolved = sorted(used - defined)
        check("%s: every name resolves" % filename, not unresolved,
              "%s" % unresolved)


def _import_graph(entrypoint):
    """Every local module an entrypoint reaches, transitively."""
    local = {f[:-3] for f in os.listdir(HERE) if f.endswith(".py")}
    seen, queue = set(), [entrypoint]
    while queue:
        name = queue.pop()
        if name in seen or name not in local:
            continue
        seen.add(name)
        tree = ast.parse(open(os.path.join(HERE, name + ".py"),
                              encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                queue.extend(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                queue.append(node.module.split(".")[0])
    return seen


def check_dockerfile_copies_everything_the_entrypoint_imports():
    """§10: all three repos in this family shipped an image that died with
    ModuleNotFoundError on every invocation, --help included, because
    proxy_pool.py was missing from the COPY list. CI never built the image;
    this check needs no Docker."""
    path = os.path.join(HERE, "Dockerfile")
    if not os.path.exists(path):
        check("Dockerfile exists", False)
        return
    dockerfile = open(path, encoding="utf-8").read()
    # Only the COPY instructions, continuations included — a comment above
    # them naming a file is not a file the image carries.
    copy_lines, joining = [], False
    for line in dockerfile.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if joining or stripped.upper().startswith("COPY "):
            copy_lines.append(stripped)
            joining = stripped.endswith("\\")
    copied = set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\.py", " ".join(copy_lines)))
    entry = re.search(r'(?:CMD|ENTRYPOINT)\s*\[?\s*"?(?:python3?"?,\s*"?)?'
                      r'([A-Za-z_][A-Za-z0-9_]*)\.py', dockerfile)
    entrypoint = entry.group(1) if entry else "playwright_scraper"
    needed = _import_graph(entrypoint)
    missing = sorted(needed - copied)
    check("the Dockerfile COPYs every module %s.py imports" % entrypoint,
          not missing, "missing %s" % missing)
    for unwanted in ("smoke_test", "test_smoke"):
        check("the image does not carry %s.py" % unwanted,
              unwanted not in copied)


def check_env_example_documents_exactly_what_the_loader_reads():
    import env_config
    path = os.path.join(HERE, ".env.example")
    if not os.path.exists(path):
        check(".env.example exists", False)
        return
    documented = set(re.findall(r"^\s*#?\s*([A-Z][A-Z0-9_]+)\s*=", 
                                open(path, encoding="utf-8").read(), re.M))
    read = set(env_config.ENV_KEYS)
    check("every variable the loader reads is documented",
          not (read - documented), "undocumented: %s" % sorted(read - documented))
    check("every documented variable is actually read",
          not (documented - read), "unread: %s" % sorted(documented - read))


def check_a_copied_env_example_reads_as_UNSET():
    """§17: `cp .env.example .env` followed by a run must not connect.

    The placeholder check was a literal set in a sibling repo, and the two
    credentialled URLs are documented the way the vendor documents them —
    `ws://{login}-zone-…:{password}@cb.2captcha.com:9222` — so neither
    literal matched, the run connected with the string `{login}-zone-…` as
    its username, and got a 401 a long way from its cause.
    """
    import env_config
    example = os.path.join(HERE, ".env.example")
    if not os.path.exists(example):
        check(".env.example exists", False)
        return
    text = open(example, encoding="utf-8").read()
    values = dict(re.findall(r"^([A-Z][A-Z0-9_]+)=(.*)$", text, re.M))
    check("the example actually sets every variable",
          set(values) == set(env_config.ENV_KEYS),
          "example has %s, loader reads %s"
          % (sorted(values), sorted(env_config.ENV_KEYS)))
    # Every CREDENTIAL must read as unset. The default TARGET must not: it is
    # a real, usable URL, and blanking it would remove the one setting this
    # file exists to make convenient (§17's check #3 says exactly this — the
    # credentials unset, the non-credential default still usable).
    CREDENTIALS = {"TWOCAPTCHA_KEY", "MONTBLANC_CDP_ENDPOINT", "MONTBLANC_PROXY"}
    before = dict(os.environ)
    try:
        for name, raw in values.items():
            os.environ[name] = raw
            got = env_config.env_value(name)
            if name in CREDENTIALS:
                check("a copied .env.example leaves %s unset" % name,
                      got is None, "got %r" % got)
            else:
                check("...while %s stays a usable default" % name,
                      got == raw.strip(), "got %r" % got)
    finally:
        os.environ.clear()
        os.environ.update(before)
    # And the counter-check: a real credential must still come through, or
    # the placeholder rule would have made the loader useless. Deliberately
    # NOT 32 hex characters — that is the shape of a real 2captcha key, and
    # this repo's own credential scan (rightly) fails on one.
    try:
        os.environ["TWOCAPTCHA_KEY"] = "not-a-real-key-but-a-real-value"
        equal("a real value is still read",
              env_config.env_value("TWOCAPTCHA_KEY"),
              "not-a-real-key-but-a-real-value")
    finally:
        os.environ.clear()
        os.environ.update(before)


def check_credential_scan_is_one_implementation_invoked_from_both():
    """§17: two sources of truth, one dead and one holed.

    `.github/ci_checks.py` sat in three repos invoked by NOTHING, while
    tests.yml carried an inline grep doing a narrower version of the same job
    — one that matched only ws:// and wss://, so an http://user:pass@
    credential would have sailed past CI.
    """
    script = os.path.join(HERE, ".github", "ci_checks.py")
    check("the credential scan exists as a script", os.path.exists(script))
    if not os.path.exists(script):
        return
    workflow = os.path.join(HERE, ".github", "workflows", "tests.yml")
    if os.path.exists(workflow):
        text = open(workflow, encoding="utf-8").read()
        check("CI INVOKES the script rather than reimplementing it",
              "ci_checks.py" in text)
    result = subprocess.run([sys.executable, script, "--all"], cwd=HERE,
                            capture_output=True, text=True)
    check("the credential scan passes on this repo's own tree",
          result.returncode == 0,
          (result.stdout + result.stderr)[-600:])


BANNED_WORDING = (
    "cloud browser", "antidetect browser", "2scraper Antidetect Browser",
    "gate.2prx.com", "ANTIDETECT_LOCAL_API",
)


def check_ci_calls_the_shared_checks_rather_than_restating_them():
    """§17: one implementation, invoked from both — asserted, not assumed.

    This repo's FIRST CI run failed on exactly this. `tests.yml` carried
    INLINE reimplementations of the `--help` and sample-output checks that
    `.github/ci_checks.py` already implements. The inline sample check still
    imported `output_writer.Business` — a class this repo renamed to
    `Product` — so the job died with ImportError while `ci_checks.py` passed
    on the same tree. One copy had been updated and the other had not, and
    nothing in the repo could see the difference.

    The guard triggers on the whole `.github` directory being absent, never
    on a file inside it being missing (§22): two suites in this family run
    INSIDE the Docker image, which deliberately COPYs no `.github/`, so a
    check that reads a workflow file is correct in the repo and red in the
    image. A check that quietly starts passing once its input disappears is
    the failure mode this one is guarding against, so the escape is the
    directory, not the file.
    """
    github_dir = os.path.join(HERE, ".github")
    if not os.path.isdir(github_dir):
        skip("ci wiring", "no .github/ directory (this is the Docker image, "
                          "which deliberately carries no CI material)")
        return

    workflow = os.path.join(github_dir, "workflows", "tests.yml")
    script = os.path.join(github_dir, "ci_checks.py")
    check("ci_checks.py exists", os.path.exists(script))
    check("tests.yml exists", os.path.exists(workflow))
    if not (os.path.exists(workflow) and os.path.exists(script)):
        return

    text = open(workflow, encoding="utf-8").read()
    for flag in ("--help-check", "--sample-check", "--secret-check"):
        check("tests.yml invokes ci_checks.py %s" % flag,
              "ci_checks.py" in text and flag in text,
              "the workflow must CALL the shared check, not restate it")

    # The positive direction is not enough on its own: the workflow could
    # call the script AND still carry a stale inline copy beside it, which is
    # exactly the state that broke the first run. So assert the tell-tales of
    # a reimplementation are gone.
    # Deliberately NOT keyed on the filename. `sample_output.json` appears
    # legitimately in the docker job, which asserts the IMAGE does not carry
    # it — so a filename tell-tale fails on a correct workflow, which is its
    # own kind of check nobody can read. What actually distinguishes a
    # reimplementation is inline Python that imports the row model or
    # dataclass machinery to rebuild the expected column list.
    for tell in ("from output_writer import", "asdict("):
        check("tests.yml does not reimplement the sample check (%r)" % tell,
              tell not in text,
              "an inline copy drifts from the shared one silently")


def check_banned_wording():
    """§12: enforced by this test rather than by review."""
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in
                   (".git", "__pycache__", ".pytest_cache", "node_modules")]
        for filename in files:
            if not filename.endswith((".py", ".md", ".yml", ".yaml", ".txt",
                                      ".toml", ".html", ".example")):
                continue
            path = os.path.join(root, filename)
            text = open(path, encoding="utf-8", errors="replace").read().lower()
            for phrase in BANNED_WORDING:
                if phrase.lower() in text and filename != "smoke_test.py":
                    check("%s contains no %r" % (
                        os.path.relpath(path, HERE), phrase), False)
    check("banned-wording scan ran", True)


def check_concurrency_with_the_browser_stubbed():
    """§10: a live run cannot always reach this machinery.

    Page 1 is fetched alone and decides how many pages there are, so a
    blocked page 1 means the workers never start. Driven directly instead,
    with the browser replaced.
    """
    engine = _import_engine("playwright_scraper")
    if engine is None:
        return

    class Args:
        delay = 0
        retries = 1
        retry_delay = 0
        out = "unused"
        mode = "search"
        sort = "a-z"
        pages = 50

    fetched = []
    import threading
    lock = threading.Lock()

    def fake_fetch(session, args, pool, page_num, url):
        with lock:
            fetched.append(page_num)
        outcome = engine.PageOutcome(page_num=page_num, url=url)
        # Page 6 is the end of this listing: no rows, but a served page.
        outcome.products = [] if page_num >= 6 else [object()] * 15
        outcome.state = "empty" if page_num >= 6 else "content"
        return outcome

    class FakeSession:
        def __init__(self, *a, **k):
            self.pool = None
        def open(self):
            return self
        def close(self):
            pass

    class FakePlaywright:
        def __enter__(self):
            return None
        def __exit__(self, *a):
            return False

    real_fetch = engine._fetch_one_page
    real_session = engine._BrowserSession
    real_pw = engine.sync_playwright
    engine._fetch_one_page = fake_fetch
    engine._BrowserSession = FakeSession
    engine.sync_playwright = lambda: FakePlaywright()
    try:
        specs = [(n, "u%d" % n) for n in range(2, 51)]
        results, unattempted, exhausted = engine._fetch_pages_concurrently(
            Args(), None, specs, 4)
    finally:
        engine._fetch_one_page = real_fetch
        engine._BrowserSession = real_session
        engine.sync_playwright = real_pw

    check("every page fetched was fetched exactly once",
          len(fetched) == len(set(fetched)), "%r" % sorted(fetched))
    check("dispatch STOPPED at the end of the listing", exhausted)
    check("...so the 49 queued pages cost far fewer fetches",
          len(fetched) < 15, "fetched %d of 49" % len(fetched))
    check("unattempted pages are REPORTED, not counted as failed",
          len(unattempted) > 0 and all(isinstance(n, int) for n in unattempted))
    equal("attempted + unattempted covers the whole queue",
          len(set(fetched)) + len(unattempted), 49)
    equal("outcomes are restorable to page order",
          [o.page_num for o in sorted(results, key=lambda o: o.page_num)],
          sorted(o.page_num for o in results))


def check_a_dead_worker_neither_hangs_nor_loses_its_siblings():
    engine = _import_engine("playwright_scraper")
    if engine is None:
        return

    class Args:
        delay = 0
        retries = 1
        retry_delay = 0
        out = "unused"
        mode = "search"
        sort = "a-z"
        pages = 10

    def exploding_fetch(session, args, pool, page_num, url):
        if page_num == 3:
            raise RuntimeError("worker died")
        outcome = engine.PageOutcome(page_num=page_num, url=url)
        outcome.products = [object()] * 15
        outcome.state = "content"
        return outcome

    class FakeSession:
        def __init__(self, *a, **k):
            self.pool = None
        def open(self):
            return self
        def close(self):
            pass

    class FakePlaywright:
        def __enter__(self):
            return None
        def __exit__(self, *a):
            return False

    real_fetch, real_session, real_pw = (engine._fetch_one_page,
                                         engine._BrowserSession,
                                         engine.sync_playwright)
    engine._fetch_one_page = exploding_fetch
    engine._BrowserSession = FakeSession
    engine.sync_playwright = lambda: FakePlaywright()
    try:
        specs = [(n, "u%d" % n) for n in range(2, 8)]
        results, unattempted, exhausted = engine._fetch_pages_concurrently(
            Args(), None, specs, 3)
    finally:
        engine._fetch_one_page = real_fetch
        engine._BrowserSession = real_session
        engine.sync_playwright = real_pw

    check("the run returned rather than hanging", True)
    check("the dead worker's siblings still delivered their pages",
          len(results) >= 3, "%d results" % len(results))
    check("page 3 is not reported as a success",
          3 not in [o.page_num for o in results])


def check_worker_pools_start_on_different_exits():
    engine = _import_engine("playwright_scraper")
    if engine is None:
        return
    from proxy_pool import ProxyPool
    pool = ProxyPool(["http://a:1", "http://b:2", "http://c:3"], rotate="per-run")
    firsts = [engine._worker_pool(pool, i).current for i in range(3)]
    equal("three workers start on three different exits",
          len(set(firsts)), 3)
    equal("a missing pool stays missing", engine._worker_pool(None, 0), None)


def check_fingerprint_kwargs_are_ones_the_driver_accepts():
    """§10: an unknown key in new_context(**kwargs) is a TypeError at launch,
    on the PAID path, at runtime."""
    engine = _import_engine("playwright_scraper")
    if engine is None:
        return
    try:
        from fingerprint_client import playwright_context_kwargs
    except ImportError as e:
        skip("fingerprint", str(e))
        return
    sample = {"id": "x", "country": "US",
              "userAgent": "Mozilla/5.0 Chrome/140.0.0.0",
              "screen": {"width": 1920, "height": 1080},
              "timezone": "America/New_York", "language": "en-US",
              "devicePixelRatio": 2}
    kwargs = playwright_context_kwargs(sample)
    from playwright.sync_api import sync_playwright  # noqa: F401
    import playwright.sync_api as pw_api
    signature = inspect.signature(pw_api.Browser.new_context)
    unknown = [k for k in kwargs if k not in signature.parameters]
    check("every fingerprint kwarg is one new_context accepts", not unknown,
          "unknown: %s" % unknown)


def check_every_engine_exposes_the_same_public_surface():
    for module in ENGINES:
        engine = _import_engine(module)
        if engine is None:
            continue
        for name in ("scrape", "parse_args", "PageOutcome", "_fetch_one_page",
                     "_parse_for_mode", "_target_url"):
            check("%s.%s exists" % (module, name), hasattr(engine, name))
        outcome = engine.PageOutcome(page_num=1, url="u")
        for field_name in ("state", "total_available", "pages_available",
                           "sort_applied", "products", "blocked_by",
                           "load_failed", "final_url"):
            check("%s.PageOutcome carries %r" % (module, field_name),
                  hasattr(outcome, field_name))
        check("%s.PageOutcome.ok is True for a fresh outcome" % module,
              outcome.ok)
        equal("%s shares CORE_FIELDS with its twins" % module,
              tuple(engine.CORE_FIELDS),
              ("title", "url", "sku", "currency", "image_url"))
        equal("%s shares PRICE_COVERAGE_FLOOR with its twins" % module,
              engine.PRICE_COVERAGE_FLOOR, 95)
        equal("%s shares CORE_FIELD_FLOOR with its twins" % module,
              engine.CORE_FIELD_FLOOR, 99)


def check_engines_do_not_evaluate_a_string_in_the_browser():
    """§18: a site whose CSP omits `unsafe-eval` kills wait_for_function with
    an EvalError and takes the run down with exit 1. BBB has not been
    measured for that, and the cheap habit costs nothing where it would have
    been allowed."""
    for module in ENGINES:
        path = os.path.join(HERE, module + ".py")
        if not os.path.exists(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read())
        called = {node.func.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)}
        for banned in ("wait_for_function", "waitForFunction", "waitFor"):
            check("%s never CALLS %s" % (module, banned), banned not in called,
                  "poll through page_flow.wait_for_count instead")


def check_credentials_never_reach_a_log():
    """§8: an EXCEPTION MESSAGE is a log, and the masker must be GLOBAL.

    A Playwright connection error repeats the endpoint five times (the
    message plus a four-line call log), so a masker handling only the first
    occurrence prints the password four times and looks like it is working.
    """
    for module in ENGINES:
        engine = _import_engine(module)
        if engine is None:
            continue
        masked = engine._mask_credentials(
            "tried ws://u:supersecret@h1:9222 and ws://u:supersecret@h2:9222 "
            "and again ws://u:supersecret@h1:9222")
        check("%s masks EVERY occurrence" % module,
              "supersecret" not in masked, masked)
        check("%s keeps the host and port, which are the useful half" % module,
              "h1:9222" in masked and "h2:9222" in masked, masked)
    from proxy_pool import mask
    masked = mask("http://user:secret@exit.example.com:2334")
    check("proxy_pool.mask hides the password", "secret" not in masked)
    check("proxy_pool.mask keeps the exit", "exit.example.com:2334" in masked)


def check_sample_output_matches_the_schema():
    from output_writer import Product
    expected = [f.name for f in fields(Product)]
    json_path = os.path.join(HERE, "sample_output.json")
    csv_path = os.path.join(HERE, "sample_output.csv")
    if not os.path.exists(json_path):
        check("sample_output.json exists", False)
        return
    rows = json.load(open(json_path, encoding="utf-8"))
    check("sample_output.json holds rows", bool(rows))
    equal("sample_output.json keys match the schema, in order",
          list(rows[0].keys()), expected)
    check("sample_output.json is from a real run (montblanc.com rows)",
          all(r["source"] == "montblanc.com" for r in rows))
    check("...and every row carries the market its price belongs to",
          all(r.get("locale") for r in rows),
          "a price with no market is a number with no units")
    check("...and a currency wherever there is a price",
          all(r.get("currency") for r in rows if r.get("price") is not None))
    check("...and carries no fabrication markers",
          not any("example" in (r.get("url") or "").lower() or
                  "lorem" in (r.get("title") or "").lower() for r in rows))
    if os.path.exists(csv_path):
        header = next(csv.reader(open(csv_path, encoding="utf-8")))
        equal("sample_output.csv header matches the schema", header, expected)


def check_readme_numbers_are_not_stale():
    """§17's check #4: diff every numeric claim against what is on disk.

    Only the figures that MUST hold are pinned: a number that legitimately
    varies between runs is written as a range in the README and not checked
    here.
    """
    path = os.path.join(HERE, "README.md")
    if not os.path.exists(path):
        check("README.md exists", False)
        return
    readme = open(path, encoding="utf-8").read()
    import product_parser as P
    # The site's own default page size, which the README quotes when it
    # explains pagination. There is no page CAP to pin here — unlike the
    # sibling repo, this site imposes none — so what is checkable is the
    # page size and the arithmetic built on it.
    if "sz=24" in readme or "24 products per page" in readme:
        equal("the README's page size matches PAGE_SIZE", P.PAGE_SIZE, 24)
    if "280 results across 12" in readme or "12 pages of 24" in readme:
        equal("the README's 280/12 arithmetic holds",
              P.pages_available(280, 24), 12)
    from output_writer import Product
    column_count = len(fields(Product))
    claimed = re.findall(r"(\d+)\s+columns", readme)
    for number in claimed:
        equal("the README's column count matches the schema",
              int(number), column_count)


_TREE_BEFORE = None


def _tree_state():
    result = subprocess.run(["git", "status", "--porcelain"], cwd=HERE,
                            capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return sorted(line for line in result.stdout.splitlines()
                  if not line.endswith(".pyc"))


def check_no_test_mutates_the_working_tree():
    """§10: one suite used its own file as a fake chromedriver and chmod'd it
    to 755, leaving a mode change in git status.

    Compares the tree against how it looked when the suite STARTED, not
    against a clean checkout — otherwise this is permanently red while
    anyone is editing, and a check that is always red teaches everyone to
    ignore checks.
    """
    if _TREE_BEFORE is None:
        skip("git status", "not a git repository")
        return
    after = _tree_state()
    changed = sorted(set(after) - set(_TREE_BEFORE))
    check("the suite itself changed nothing in the working tree",
          not changed, "%s" % changed)


def check_captcha_capability_claims_match_the_code():
    """§19: the most expensive bug this family can ship is a SENTENCE.

    It fails in both directions and this family has shipped both:

      * saying a captcha CANNOT be solved, when the true statement is that
        THIS REPO does not implement the task type. 2Captcha solves
        enterprise reCAPTCHA and Cloudflare Turnstile and has for years, so
        such a sentence tells a reader not to buy something that works.
      * saying this repo DOES solve something it builds no task type for --
        which is what the README said here: it billed the Managed Challenge
        solve to `--twocaptcha-key`, while the only thing that clears one is
        `Captcha.setAutoSolve` over `--cdp-endpoint`.

    Neither is visible to any other check: nothing fails, nothing crashes,
    and the output is correct.
    """
    readme = open(os.path.join(HERE, "README.md"), encoding="utf-8").read()
    solver = open(os.path.join(HERE, "captcha_solver.py"), encoding="utf-8").read()
    low = readme.lower()

    # Conclusions about the PRODUCT. "Unsolvable" is a property of a PAGE —
    # it means the page carries no widget — and never of a vendor. 2Captcha
    # solves enterprise reCAPTCHA and Cloudflare Turnstile and has for years,
    # so a sentence saying otherwise tells a reader not to buy something that
    # works, and nothing else in this suite can see it.
    for phrase in ("cannot be solved", "can't be solved", "neither is solvable",
                   "is not solvable", "solver is inapplicable", "no solver can",
                   "2captcha cannot", "no captcha service"):
        check("README: no %r -- write 'this repo does not implement X'" % phrase,
              phrase not in low)

    # The pairing that matters ON THIS SITE.
    #
    # The sibling repo pins "the README must say TurnstileTaskProxyless is
    # not built here", because BBB renders Cloudflare Managed Challenges and
    # a reader could reasonably expect a key to clear one. Montblanc renders
    # NO challenge at all, so the equivalent hazard is the opposite one: this
    # README's central claim is that no key is needed, and a claim like that
    # rots the moment the site switches a bot manager on.
    #
    # So the claim has to be PAIRED with the thing that would retest it — the
    # daily canary — rather than left as a sentence nobody re-measures (§13).
    claims_no_key = ("no, and it would be dishonest" in low
                     or "do i need a 2captcha account" in low)
    if claims_no_key:
        check("README pairs 'no key needed' with the canary that retests it",
              "canary" in low,
              "a claim that the site is ungated goes stale silently; the "
              "daily canary is what turns it back into a measurement")
        check("...and dates the measurement it rests on",
              re.search(r"20\d\d-\d\d-\d\d", readme) is not None,
              "§13: a number describing a living thing needs its moment")

    # And in the other direction: if the README names a task type, the solver
    # had better build it. A capability claim citing no task type is a guess
    # wearing a fact's clothes; one citing a task type nobody implemented is
    # worse.
    for task in ("TurnstileTaskProxyless", "RecaptchaV2EnterpriseTaskProxyless",
                 "RecaptchaV3TaskProxyless"):
        if task.lower() in low:
            check("README names %s, so the solver must build it" % task,
                  task in solver,
                  "the README credits a task type this repo does not send")

    # Whatever the README credits with clearing the challenge must be a thing
    # the engines actually do.
    if "setautosolve" in low:
        srcs = ""
        for name in ("playwright_scraper.py", "selenium_scraper.py",
                     "puppeteer_scraper.py"):
            path = os.path.join(HERE, name)
            if os.path.exists(path):
                srcs += open(path, encoding="utf-8").read()
        check("README credits Captcha.setAutoSolve, and an engine calls it",
              "Captcha.setAutoSolve" in srcs)
def check_no_statement_is_unreachable():
    """A statement sitting after a return/raise/break/continue in the SAME
    block, which therefore can never run.

    Narrow on purpose: it makes no claim about reachability in general, only
    about a block whose control flow has already left. Measured across the
    eighteen repos of this family on 2026-09-16 it reported six problems and
    zero false positives.

    `check_undefined_names_in_every_module` cannot see this class at all, by
    design -- it pools every binding in the file rather than tracking scopes,
    so a name used inside dead code passes as long as anything else in the
    module binds it. What was hiding in that blind spot here, and in five
    sibling repos, byte for byte: a function whose `def` line had been lost,
    leaving its docstring and body absorbed into the end of the function
    above it. Present since this repo's first commit, invisible to import,
    `--help`, `compileall`, and every green run of this suite.
    """
    for filename in sorted(f for f in os.listdir(HERE) if f.endswith(".py")):
        tree = ast.parse(open(os.path.join(HERE, filename),
                              encoding="utf-8").read())
        dead = []
        for node in ast.walk(tree):
            for field in ("body", "orelse", "finalbody"):
                block = getattr(node, field, None)
                if not isinstance(block, list):
                    continue
                for i, stmt in enumerate(block[:-1]):
                    if isinstance(stmt, (ast.Return, ast.Raise,
                                         ast.Continue, ast.Break)):
                        dead.append(block[i + 1].lineno)
                        break
        check("%s: no statement the control flow can never reach" % filename,
              not dead, "first at line %d" % min(dead) if dead else "")


CHECKS = [v for k, v in sorted(globals().items()) if k.startswith("check_")]


def main():
    global VERBOSE
    parser = argparse.ArgumentParser(description="montblanc-scraper offline suite")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    VERBOSE = args.verbose

    global _TREE_BEFORE
    _TREE_BEFORE = _tree_state()

    for fn in CHECKS:
        if VERBOSE:
            print("\n== %s" % fn.__name__)
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — a broken check is a failure
            import traceback
            FAILURES.append("%s raised %s: %s" % (fn.__name__, type(e).__name__, e))
            print("  ERROR %s raised %s: %s" % (fn.__name__, type(e).__name__, e))
            if VERBOSE:
                traceback.print_exc()

    print("\n%d checks passed, %d failed, %d group(s) skipped."
          % (PASSED, len(FAILURES), len(SKIPS)))
    for line in SKIPS:
        print("  skipped: %s" % line)
    if FAILURES:
        print("\nFailures:")
        for line in FAILURES:
            print("  - %s" % line)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

set dotenv-load
set positional-arguments  # recipes pass their arguments through as "$@", quoting intact

# List recipes
default:
    @just --list

# Install dependencies and generate the BAML client
setup:
    uv sync
    baml generate

# Regenerate baml_sdk/ after editing baml_src/*.baml
generate:
    baml generate

# Run every seed parser (e.g. `just collect --seed longreads --limit 12`)
collect *args:
    uv run python -m steps.collect "$@"

# Haiku reads gaps (missing film, author, outlet) out of each seed's own text
fill *args:
    uv run python -m steps.fill "$@"

# Wikipedia → Wikidata → IMDb ID, year, box office; IMDb ratings dump; OMDb if keyed
enrich *args:
    uv run python -m steps.enrich "$@"

# Wayback, then archive.ph (add --excerpts to save article text for classify)
archive *args:
    uv run python -m steps.archive "$@"

# Unit tests, then the hand-labeled BAML tests (live Haiku calls) — run before a full classify pass
test:
    uv run pytest -q
    baml test

# Classify every unclassified row with Haiku
classify *args: test
    uv run python -m steps.classify "$@"

# Haiku + web search finds article URLs no seed linked (checks the film's Wikipedia citations first)
discover *args:
    uv run python -m steps.discover "$@"

# One-sentence synopsis: IMDb → Rotten Tomatoes → generated from Wikipedia → generated from the article
synopsis *args:
    uv run python -m steps.synopsis "$@"

# Trace credited books back to the article they grew out of (same-author lookup, then web search)
origin *args:
    uv run python -m steps.origin "$@"

# Scope from data/publications.csv; unknown outlets go to data/outlet_queue.csv
scope *args:
    uv run python -m steps.scope "$@"

# Fidelity from credit wording (saved IMDb credits, else Wikipedia); data/overrides.csv last
fidelity *args:
    uv run python -m steps.fidelity "$@"

# Per-field accuracy against data/gold.csv → eval.json (shown in the page footer)
eval:
    uv run python -m steps.eval

# Merge rows that name the same (film, origin work) differently
dedupe *args:
    uv run python -m steps.dedupe "$@"

# Write adaptations.csv and adaptations.html (add --queue to print the review queue)
export *args:
    uv run python -m steps.export "$@"

# The film pipeline, end to end
all:
    just collect
    just _rest

# The TV seeds (manual_tv.csv + Wikipedia's podcast adaptations), run separately and less often
tv:
    just collect --tv
    just _rest

_rest:
    just fill
    just enrich
    just classify
    just origin
    just dedupe
    just scope
    just fidelity
    just discover
    just archive --excerpts
    just synopsis
    just eval
    just export

# The human review queue (unreviewed rows with no IMDb link, or an article with no archive)
queue:
    uv run python -m steps.export --queue

# Lint
lint:
    uv run ruff check .

# The canonical URL (steps/export.py SITE too)
site := "https://longformadaptations.jaan.io/"

# Publish the review page to longformadaptations.jaan.io (also longformadaptations.pages.dev) (Cloudflare Pages; wrangler.jsonc): page + CSV
publish: export
    rm -rf dist && mkdir -p dist/data/v0.1
    cp adaptations.html dist/index.html
    sed 's/currentColor/black/' assets/icon.svg > dist/icon.svg
    printf 'User-agent: *\nAllow: /\nSitemap: {{site}}sitemap.xml\n' > dist/robots.txt
    printf '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{{site}}</loc><lastmod>%s</lastmod></url></urlset>\n' "$(date +%F)" > dist/sitemap.xml
    cp adaptations.csv dist/adaptations.csv
    for d in dist/data dist/data/v0.1; do cp adaptations.csv data/publications.csv data/gold.csv eval.json CHANGELOG.md $d/; done
    # --force: create a classic Pages project (pages.dev); newer wrangler otherwise makes a Worker
    npx --yes wrangler@4 pages project list | grep -q 'longformadaptations ' || npx --yes wrangler@4 pages project create longformadaptations --production-branch main --force
    npx --yes wrangler@4 pages deploy --branch main --commit-dirty=true

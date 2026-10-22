# Longhorns v0.1

Every journalism piece named in a fixed set of curated lists that became a film or TV
title, linked to Wikipedia / Wikidata / IMDb / Rotten Tomatoes, with an archived copy of the
article. A row counts as journalism by its **origin**, the first publication in the chain,
whatever the credits name; books, short fiction and podcasts are kept as labeled negatives.
See [spec.md](spec.md) for the design and [CHANGELOG.md](CHANGELOG.md) for v0.1.

**Principle (v0.1):** the model never decides anything a table or Wikidata already knows.
Scope comes from `data/publications.csv`, medium and year from Wikidata, fidelity from credit
wording, merges from titles and authors; Haiku fills blanks and explains chains.

Outputs: `longhorns.sqlite` (table `adaptations`), `adaptations.csv`, `adaptations.html`
(the review page at https://longformadaptations.pages.dev: an Articles tab listing journalism,
local and short-fiction rows by score, and a Sources tab at `/#sources`), and `eval.json`.

Deploys are automatic: every push to `main` runs `.github/workflows/deploy.yml`, which tests,
re-exports the page from the committed `longhorns.sqlite` and deploys it (`just publish`). The
pipeline itself runs on a laptop (it needs `.env` and `raw/`); commit its outputs to ship them.

## Run

```sh
just setup     # uv sync + baml generate
just all       # the film seeds through every step (below)
just tv        # the TV seeds through the same steps
just publish   # export + deploy by hand (a push to main does this automatically)
```

Every step is idempotent, fills only blank columns it owns (`--force` to overwrite; reviewed
rows are left alone), and takes `--limit N`. In pipeline order:

| Step | Command | What it does |
| --- | --- | --- |
| collect | `just collect [--seed NAME] [--tv]` | Runs each parser in `seeds/` (raw pages cached under `raw/<seed>/`); hygiene rules exclude rows for good (`excluded` table) |
| fill | `just fill` | Haiku (`FillGaps`) reads missing film/author/outlet out of each seed's own text; fiction podcasts are dropped and podcasts traced to a print origin |
| enrich | `just enrich` | Wikipedia → Wikidata: IMDb ID, **medium (P31/P136) and year (P577, else P580)**, box office; IMDb rating and votes; Rotten Tomatoes page, Tomatometer and review count; OMDb fallbacks |
| classify | `just classify [--refresh]` | Runs `just test`, then one `ClassifyAdaptation` call per row: source, origin and credited types, chain; scope only as a suggestion for outlets missing from the table |
| origin | `just origin` | Traces credited books to the article they grew out of: same-film/same-author lookup, then one `TraceOrigin` web search; the row then describes the print origin |
| dedupe | `just dedupe` | One row per (film_qid, origin work); alternate titles go to `source_aliases`, old ids to `aliases` |
| scope | `just scope` | `publication_scope` from `data/publications.csv`; unknown outlets → `data/outlet_queue.csv` and a review flag |
| fidelity | `just fidelity` | From credit wording: saved IMDb full credits, else the film's Wikipedia wording; the model may only downgrade; `data/overrides.csv` last (Disputed) |
| discover | `just discover` | One web search per journalism row without a URL; only hits on the outlet's own domain or an archive (`source_url_origin=search`) |
| archive | `just archive [--excerpts]` | Wayback availability API (timestamp = publication date), CDX fallback, then archive.ph |
| synopsis | `just synopsis` | IMDb plot → RT synopsis → generated from the Wikipedia lead → generated from the article |
| eval | `just eval` | Per-field accuracy against `data/gold.csv` → `eval.json`, shown in the page footer |
| export | `just export [--queue]` | CSV + HTML; `--queue` prints the review queue |

`just queue` lists rows needing a human: review flags (outlet missing, no source credit,
model could not verify, …), no IMDb link, or an article with no archive. Set `reviewed = 1`
in SQLite once you've checked a row.

Hand-kept data: `data/publications.csv` (outlets), `data/overrides.csv` (any field, e.g.
Disputed fidelity), `data/gold.csv` (eval labels; set `verified_by` as you confirm rows),
`seeds/manual.csv` and `seeds/manual_tv.csv` (rows).

## Seeds

| Seed | Status |
| --- | --- |
| `wikipedia_list` | Parsed from the rendered page (film link, external article link, en-dash fields) |
| `wikipedia_category` | 137 films; infobox `based_on` + lead fed to the fill step |
| `longform`, `longreads`, `tetw`, `thr` | Parsed from HTML (Longreads needs a Chrome-TLS client, `curl_cffi`; THR slides are embedded JSON) |
| `imdb_list` | IMDb blocks bots: pages saved from a browser into `raw/imdb/*.html` (a user list and a poll) |
| `manual` | `seeds/manual.csv`, same columns as the table; an `archive_url` given here wins (`archive_status=manual`) |
| `manual_tv`, `wikipedia_podcast_adaptations` | TV seeds, run separately with `just tv`; podcasts traced to a print origin by Haiku + web search |

## Differences from the spec

- **BAML 0.17 syntax** (`baml-bridge`, matching `../loitering` and `../procurement`): clients are
  values, tests are `test`/`testset` blocks, `baml test`/`baml generate` replace `baml-cli`.
  Generated client is `baml_sdk/` (gitignored, ~7 MB; `just generate` rebuilds it).
- **Model**: `claude-haiku-5-5` at low effort (the spec's `claude-haiku-4-5` is a generation old;
  5.5 is $0.10 / $0.50 per MTok). A full fill + classify pass is a few cents.
- **`fetch.py`**, not `http.py`, which would shadow the stdlib `http` package. Retries 5xx/429
  twice with backoff, and falls back to Chrome-TLS impersonation on 403/202 bot walls.
- **selectolax** (lexbor) instead of BeautifulSoup; **just** instead of a Makefile.
- **Extra steps**: `fill` (the spec's "Haiku fills gaps", made its own step) and `dedupe` (the
  spec's primary key alone can't see that "The Great Escape" and "How the CIA Used a Fake
  Sci-Fi Flick…" are the same Bearman piece).
- **Extra columns**: `publication_normalized` (Haiku's canonical outlet name, keeping the
  seed's `publication` as written) and `seed_context` (the seed's own words about the row).
- Wikimedia lookups are batched (50 titles/entities per call via `query`/`wbgetentities`)
  instead of one `action=parse` / `EntityData` call per film.
- `seed` accumulates every list that named a row (`wikipedia_list,longreads,tetw`).

## Environment

`.env`: `ANTHROPIC_API_KEY` (required), `CONTACT_EMAIL` (added to the Wikimedia
User-Agent, per their API etiquette), `OMDB_API_KEY` (optional; enables Rotten Tomatoes
scores and the domestic box-office fallback; free tier is 1,000 requests/day).

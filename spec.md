# Longhorns Scraper — Minimal Prototype Spec

Oct 20, 2026 · @jaan

## Goal and scope

v0 produces one table: every journalism piece named in a fixed set of curated lists, each row linked to its film's Wikipedia page and IMDb ID, with an archived copy of the article and a Haiku-assigned source classification. Books that appear in those lists are kept as rows and labeled `book` so the classifier learns the negative class; they are not sought out.

- In: non-fiction articles from newspapers, magazines, alt-weeklies, wire services and online-only outlets that became films or TV. Fictional short stories published in periodicals are tagged `short_fiction` and kept (same negative-example logic).
- In: the local/regional distinction as a first-class field, since no existing source tracks it.
- Out for v0: discovering options not in the seed lists, dead options, trade-press scanning, podcasts, TV-only sources. Section 9 covers these.

Done for v0 = one CSV plus one SQLite file, roughly 250 to 350 rows after de-duplication, every row with at least one archive URL and a classification, total LLM spend under $5, runnable end to end with one command on a laptop.

## Seed sources

Start with the two Wikipedia pages (structured, free, scrapeable with one HTTP call each); add the editorial lists once the pipeline runs end to end. Each seed gets its own small parser that yields raw rows; nothing downstream knows which seed a row came from except via the `seed` field.

| Priority | Source | Rows (approx.) | Structure | Parser notes |
| --- | --- | --- | --- | --- |
| 1 | [Wikipedia list](https://en.wikipedia.org/wiki/List_of_films_based_on_magazine_articles) | 55 | film – "article", publication, date – author | Best schema. Parse the list items with regex; the article title is usually a link to the piece. |
| 1 | [Wikipedia category](https://en.wikipedia.org/wiki/Category:Films_based_on_newspaper_and_magazine_articles) | 129 | film titles only | Use the MediaWiki API (`list=categorymembers`), then read each film's infobox `based_on` field via `action=parse`. Haiku fills gaps from the article lead. |
| 2 | [Longform Guide to Adaptations](https://longform.org/posts/the-longform-guide-to-adaptations) | \~15 | article, author, publication, date, film | Clean; includes the Saturday Night Fever fabrication note. |
| 2 | [Longreads 25](https://longreads.com/2020/02/28/movies-based-on-articles/) | 25 | film (year): Based on "article" by author (publication, year) + link | Links to the articles themselves. |
| 2 | [TETW: 40 articles that became films](https://tetw.org/Great_Articles_that_Became_Films) | 40 | article by author (film) | Grouped by genre; article titles link out. |
| 3 | [Hollywood Reporter gallery](https://www.hollywoodreporter.com/gallery/movies-are-based-magazine-articles-786327/) | 25 | one slide per film | Paginated gallery; fetch via Wayback if the live page blocks. |
| 3 | [IMDb user list ls540838468](https://www.imdb.com/list/ls540838468) | 28 | film + annotation "article", publication, date – author | Gives the IMDb ID for free. Scrape the HTML; no API. |
| 3 | Reddit u/JackieRose29 list (186 entries) | 186 | unknown | Blocked from automated fetch. Export manually to `seeds/manual.csv`; likely the largest single seed. |
| 4 | [The Optionist](https://theoptionist.substack.com/), [IP List](https://popshift.org/ip-list/), [BookToMovies](https://booktomovies.com/) | — | forward-looking / paid / books | Not scraped in v0. Listed so the schema's `status` field anticipates them. |

Anything a seed cannot be parsed from is left blank and filled by the enrich step, never guessed by the parser.

## Data model

One SQLite table, `adaptations`, one row per (source work, film) pair. A film adapted from a three-part series is one row; a film with two distinct sources (article plus book) is two rows. Primary key is a hash of normalized `film_title` + `source_title`.

| Field | Type | Set by | Notes |
| --- | --- | --- | --- |
| id | text | collect | sha1 of normalized film + source title |
| seed | text | collect | which list it came from; `manual` for curated rows |
| film\_title | text | collect | as written in the seed |
| film\_year | int | collect / enrich | release year; always filled, it is the main search key with title |
| source\_title | text | collect | article headline or book title |
| source\_author | text | collect |  |
| publication | text | collect | as written; normalized later by Haiku |
| source\_date | text | collect | ISO if known, else year, else blank |
| source\_url | text | collect / manual | the original article URL if the seed links it |
| archive\_url | text | archive | Wayback or archive.is snapshot of `source_url` |
| archive\_status | text | archive | `wayback`, `archive_is`, `manual`, `none` |
| wikipedia\_url | text | enrich | the film's page |
| wikidata\_qid | text | enrich |  |
| imdb\_id | text | enrich | tt-number via Wikidata P345 |
| imdb\_rating | real | enrich | 0 to 10, one decimal, from IMDb's free ratings dump |
| rt\_score | int | enrich | Tomatometer %, from OMDb when present; blank otherwise |
| box\_office\_usd | int | enrich | worldwide gross in USD from Wikidata P2142; OMDb domestic gross as fallback, flagged in `box_office_note` |
| box\_office\_note | text | enrich | `worldwide`, `domestic`, or blank |
| source\_type | enum | classify | see section 7; `book` and `short_fiction` are the negative classes |
| publication\_scope | enum | classify | national / regional / local / alt\_weekly / trade / online\_only / wire |
| adaptation\_fidelity | enum | classify | direct / loose / inspired\_by / disputed |
| classify\_confidence | real | classify | 0 to 1 |
| classify\_notes | text | classify | one line from Haiku |
| status | text | — | `produced` for all v0 rows; reserved for `optioned`, `in_development`, `dead` |
| reviewed | bool | manual | set true once a human has eyeballed the row |

Keep raw scraped HTML on disk under `raw/<seed>/` so parsers can be re-run without re-fetching.

## Pipeline

&#91;embedded content: pipeline · 5 steps, one manual input\]

Each step reads the SQLite table, fills only the columns it owns where they are blank, and writes back, so any step can be re-run alone and a manual edit is never overwritten.

1. **Collect** runs every parser in `seeds/`, writes raw HTML to `raw/`, upserts rows. `seeds/manual.csv` is just another parser.
2. **Archive** fills `archive_url` for every row with a `source_url` (section 5).
3. **Enrich** fills `wikipedia_url`, `wikidata_qid`, `imdb_id`, `film_year` plus IMDb rating, Tomatometer and box office (section 6).
4. **Classify** sends each row's fields plus the first \~1,500 characters of the archived article (when fetched) to one BAML function (section 7).
5. **Export** dumps `adaptations.csv` and keeps `longhorns.sqlite`; a `reviewed=false` filter gives the next human queue.

Plain Python, `httpx`, `beautifulsoup4`, `sqlite3`, `baml-py`. No scheduler, no queue, no framework; a `Makefile` or `uv run` entry points per step.

## Archiving

Look up existing snapshots first; never trigger new captures in v0. A manual URL always wins over a looked-up one.

1. **Manual override.** If `seeds/manual.csv` (or a later edit to the table) already carries an `archive_url`, use it and set `archive_status=manual`.
2. **Wayback availability API** (free, no key, JSON): `GET https://archive.org/wayback/available?url=<source_url>&timestamp=<source_date as YYYYMMDD>`. The `timestamp` nudges it to the snapshot closest to publication, which matters for pieces later paywalled or rewritten. Store `archived_snapshots.closest.url` → `archive_status=wayback`. Documented at [archive.org/help/wayback\_api.php](https://archive.org/help/wayback_api.php).
3. **archive.is lookup.** No official API. `GET https://archive.ph/newest/<source_url>` returns a redirect to the newest snapshot if one exists, or a 404. Follow once, store the final URL → `archive_status=archive_is`. Rate-limit to one request per 5 seconds and accept that this is brittle; a failure just leaves the field blank.
4. Nothing found → `archive_status=none`; these rows show up in the human queue so you can paste a URL by hand.

For classification context, fetch the Wayback snapshot's text (same URL with `id_` after the timestamp returns the raw page) and keep the first \~1,500 characters in `raw/articles/<id>.txt`. Skip article fetching entirely on the first run; metadata alone is enough to validate the pipeline.

## Linking to Wikipedia and IMDb

No paid API is needed. Wikidata stores IMDb IDs as property P345 on nearly every film with a Wikipedia page, so the chain is Wikipedia title → Wikidata QID → IMDb ID, all free and keyless.

1. **Wikipedia page.** Rows from the two Wikipedia seeds already have the film's page URL. For other seeds, call the MediaWiki search API: `GET https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=<film_title> <film_year> film&format=json`, take the top hit, and store its title. If the top hit's title does not contain the film title (case-insensitive), leave blank for human review rather than guess.
2. **Wikidata QID.** `GET https://en.wikipedia.org/w/api.php?action=query&prop=pageprops&titles=<title>&format=json` → `pageprops.wikibase_item`.
3. **IMDb ID, year, box office.** `GET https://www.wikidata.org/wiki/Special:EntityData/<QID>.json`: `claims.P345` is the tt-number, `P577` the release date for `film_year`, `P2142` the box office (take the value with no `P3005` region qualifier, or the largest, as worldwide gross; note the unit QID is USD before storing). Build `https://www.imdb.com/title/<tt>/`.
4. **IMDb rating.** Download `https://datasets.imdbws.com/title.ratings.tsv.gz` once (\~7 MB, free, no key, refreshed daily) and join on `imdb_id` for `averageRating`. One file replaces hundreds of page scrapes.
5. **Rotten Tomatoes and box-office fallback.** One [OMDb](https://www.omdbapi.com/) call per row by IMDb ID (`?i=<tt>&apikey=`; free tier is 1,000 requests/day, enough for the whole table in one run). Take `Ratings[Source="Rotten Tomatoes"]` for `rt_score` and `BoxOffice` (domestic) only when Wikidata had no P2142. RT scores are missing for many older films; leave blank rather than scrape rottentomatoes.com, which blocks bots.
6. Only if gaps remain: the IMDb user-list seed carries IDs directly.

Set a descriptive `User-Agent` with a contact email on every Wikimedia request (their API etiquette asks for it) and cache every response under `raw/wiki/` keyed by URL so re-runs cost nothing.

TMDb's free API is a reasonable later addition for posters and keywords but is not needed to link rows.

## BAML classification with Haiku

One BAML function, one call per row, typed output straight into the table. BAML owns the prompt, the schema and the parsing; Python only loops. Keep the schema small enough that Haiku is reliable and a human can audit disagreements.

```baml
// baml_src/clients.baml
client<llm> Haiku {
  provider anthropic
  options {
    model "claude-haiku-4-5"
    api_key env.ANTHROPIC_API_KEY
    max_tokens 400
    temperature 0
  }
}

// baml_src/classify.baml
enum SourceType {
  News_Article        @description("reported newspaper story or series")
  Magazine_Feature    @description("long-form magazine or online-magazine feature")
  Column_Or_Essay     @description("first-person essay, column, op-ed")
  Wire_Or_Brief       @description("short news item, wire copy")
  Short_Fiction       @description("fiction published in a periodical; NEGATIVE class")
  Book                @description("book-length work, even if excerpted; NEGATIVE class")
  Podcast_Or_Other
  Unknown
}

enum PublicationScope {
  National
  Regional        @description("state or multi-city, e.g. Texas Monthly")
  Local           @description("single city or town daily/weekly")
  Alt_Weekly      @description("e.g. Miami New Times, LA Weekly")
  Trade
  Online_Only
  Wire
  Unknown
}

enum Fidelity {
  Direct      @description("credited 'based on the article'")
  Loose       @description("credited but substantially fictionalized")
  Inspired_By @description("uncredited or 'inspired by' only")
  Disputed    @description("source later shown fabricated or contested")
}

class Classification {
  source_type SourceType
  publication_scope PublicationScope
  publication_normalized string @description("canonical outlet name, e.g. 'The New York Times Magazine'")
  fidelity Fidelity
  confidence float @description("0 to 1")
  notes string @description("one sentence; name any doubt")
}

function ClassifyAdaptation(
  film_title: string,
  film_year: int?,
  source_title: string,
  source_author: string?,
  publication: string?,
  source_date: string?,
  excerpt: string?
) -> Classification {
  client Haiku
  prompt #"
    You classify the written source a film was adapted from.
    Books and short fiction are the negative classes: label them
    as such rather than forcing them into a journalism category.
    If the publication is a city or alt-weekly paper, say so;
    the local/national distinction is the point of this dataset.

    Film: {{ film_title }} ({{ film_year }})
    Source: "{{ source_title }}" by {{ source_author }}
    Publication: {{ publication }}, {{ source_date }}
    {% if excerpt %}Excerpt: {{ excerpt }}{% endif %}

    {{ ctx.output_format }}
  "#
}
```

Python side after `baml-cli generate`:

```python
from baml_client import b
c = b.ClassifyAdaptation(film_title=row.film_title, film_year=row.film_year,
                         source_title=row.source_title, source_author=row.source_author,
                         publication=row.publication, source_date=row.source_date,
                         excerpt=row.excerpt)
# c is a Pydantic model; write its fields back to the row
```

**Cost.** \~300 rows × \~700 input tokens (with excerpt) × \~150 output tokens. At Haiku 4.5's list prices this is on the order of $0.50 for a full run, so re-running everything after a prompt tweak is free in practice. Check the model string against the current Anthropic model list before the first run.

**Testing.** Put 10 to 15 hand-labeled rows in `baml_src/classify.baml` as BAML `test` blocks (Pain & Gain → Alt\_Weekly, Spotlight → Local, Into the Wild → Book since the film credits the book, Saturday Night Fever → Disputed). Run `baml-cli test` before each full classify pass.

## Repo layout and run commands

Flat and boring on purpose. Each pipeline step is one file with a `main()`; shared helpers live in `db.py` and `http.py`.

```
longhorns/
  pyproject.toml          # deps: httpx, beautifulsoup4, baml-py, python-dotenv
  .env                    # ANTHROPIC_API_KEY, CONTACT_EMAIL (for User-Agent)
  baml_src/
    clients.baml
    classify.baml         # enums, class, function, test blocks
  baml_client/            # generated; commit it
  seeds/
    manual.csv            # hand-curated rows; same columns as the table
    wikipedia_list.py
    wikipedia_category.py
    longform.py
    longreads.py
    tetw.py
    imdb_list.py
  steps/
    collect.py
    archive.py
    enrich.py
    classify.py
    export.py
  db.py                   # sqlite schema + upsert that only fills blanks
  http.py                 # cached GET with User-Agent and per-host rate limit
  raw/                    # fetched HTML/JSON, gitignored
  longhorns.sqlite
  adaptations.csv
```

Run order, each idempotent:

```
uv sync && baml-cli generate
uv run python -m steps.collect           # ~2 min, two Wikipedia seeds first
uv run python -m steps.archive           # Wayback then archive.is
uv run python -m steps.enrich            # Wikipedia → Wikidata → IMDb
baml-cli test && uv run python -m steps.classify
uv run python -m steps.export
```

- `http.py` caches every GET to `raw/` by URL hash, sleeps 1 s between requests to the same host (5 s for archive.ph), and retries once on 5xx.
- `db.py` upsert rule: a non-blank existing value is never replaced unless `--force` is passed. Manual edits to the SQLite file survive re-runs.
- A `--seed <name>` flag on collect and a `--limit N` flag on every step keep the first runs to a dozen rows.
- The `reviewed` column plus `sqlite3 longhorns.sqlite 'select * from adaptations where reviewed=0 and (imdb_id is null or archive_status="none")'` is the whole review UI for v0.

## Out of scope for v0, and the expansion path

v0 answers "what has already been adapted, and from how local a source." Everything below is deliberately deferred; the schema already has room for it (`status`, `seed`, `source_type`).

| Later step | What it adds | Why not now |
| --- | --- | --- |
| Trade-press option discovery | Deadline, Variety, THR search for phrases like "optioned the article", "rights to the \[publication\] story"; rows enter as `status=optioned` | Needs a second Haiku function to extract (film, article, outlet) from prose and heavy de-duplication |
| Dead-option tracking | Re-check `optioned` rows yearly; flip to `dead` after N years with no production news | Only meaningful once option rows exist; this is the dataset nobody else has |
| The Optionist archive and the IP List | Forward-looking "rights available" rows with `status=available` | Optionist is paywalled; IP List is 25 rows a year and hand-copyable |
| Local-news discovery | Scan local-paper award lists (state press associations, Livingston Awards) and match titles against option announcements | Discovery, not indexing; different problem |
| Wikidata SPARQL seed | Films whose P144 "based on" target is a periodical article | Sparse today; cheap to add as `seeds/wikidata.py` once the parsers pattern is settled |
| The Numbers "Based on Factual Book/Article" filter | Budgets and box office per row | Adds a money dimension for the essay; needs the classifier to split books from articles, which v0 already does |
| Article full text and story-arc features | Feed whole archived articles to a model for the DSPy / story-arc experiments in the notes | Wait until the archive step has a real hit rate |
| TV series | Inventing Anna, Love & Death, Landman and the like | Same schema, different seeds; add when film is stable |

The one expansion worth doing early: once v0 runs, add 20 to 30 local-newspaper cases by hand to `seeds/manual.csv`, because that is the class the public lists under-represent and the class the essay is about.

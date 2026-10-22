# Changelog

## Page — 2026-10-22

- Renamed to **Longform Adaptations**. The page uses two text sizes (16/13px Inter), black and
  white with a remembered dark mode, a mobile menu, and the Noun Project "document filming"
  icon (redrawn heavier, square corners), which is also the favicon and the og:image.
- **Articles** lists journalism and local / regional / alt-weekly rows (books, short fiction —
  Philip K. Dick, Stephen King and the rest —, podcasts and unsourced rows are in the CSV only). The columns are Article, Film Adaptation,
  IMDb rating, IMDb votes, RT Critics Score, RT reviews and box office. Origin, credit, scope,
  fidelity, chain, seeds, flags and the classifier note moved into an **AI Notes** popover.
  Search is the only filter. Each title shows its year · director (Wikidata P57, else OMDb). Below 960px each row becomes a card, so nothing scrolls sideways.
- **Default order: a score (/100).** IMDb rating and Tomatometer are each shrunk toward the
  table mean by vote / review count (IMDb's weighted rating, m = median count), turned into
  percentiles, and averaged with RT counted twice.
- **Hand fixes.** Moneyball's article gets its outlet (NYT Magazine) by override, and blank
  dates now come from dated article URLs. Sing Sing gets the Esquire URL (2024 republication of
  the October 2005 piece). Two second-source rows were deleted by hand, each id redirected to the
  kept row: Sing Sing / "Breakin' the Mummy's Code" (the play) and Live Free or Die Hard /
  "Nothing Lasts Forever" (the 1979 novel behind the first Die Hard).
- **Sources** (formerly Statistics) starts with a table of every seed list URL, with how many
  rows each listed and how many of those are journalism.

## v0.1 — 2026-10-20

Principle: the model never decides anything a table or Wikidata already knows. Lookups set
fields; Haiku fills blanks and explains chains. (Addendum 2.)

- **Origin vs credit.** New `origin_type` (the first publication in the chain) and
  `credited_source_type` (what the screen credits name). A row is a journalism positive iff its
  origin is journalism, so a film credited to a book that grew out of a magazine piece (*Into
  the Wild*, *Adaptation*, *Black Hawk Down*) now counts. Credited books are traced to their
  print origin by a same-film/same-author lookup, then one web search (`just origin`).
- **Publications table.** `data/publications.csv` (91 outlets: scope, city, kind, own domains)
  sets `publication_scope`. City and state magazines with national circulation (New York,
  Texas Monthly, Washingtonian) are now **regional** (v0 had New York as local); metro dailies
  local; alt-weeklies alt_weekly. Outlets missing from the table go to `data/outlet_queue.csv`.
- **Medium and year from Wikidata** (P31, with P136 for documentaries; P577, else P580). The
  classifier no longer sets either. Fixes *Dr. Death*, *Archive 81*, *Bernie* (2011, not 2001).
- **Dedupe on (film_qid, origin work).** Ids are `sha1(qid | origin title)`; alternate titles
  go to `source_aliases`. A film with two different sources keeps two rows, grouped in the UI.
- **Fidelity from credit wording** (`just fidelity`): saved IMDb full credits, else the film's
  Wikipedia wording; the model may only downgrade direct → loose. Disputed is hand-set in
  `data/overrides.csv` (*Saturday Night Fever*, *Bloodsport*).
- **Gold set and eval.** `data/gold.csv` (50 rows, drafted, awaiting hand verification);
  `just eval` writes `eval.json`, shown in the page footer. Confidence left the UI. Notes saying
  the model "could not verify" or that a source "may be fabricated" flag a row for review and
  change nothing.
- **Seed hygiene.** Indian-cinema category pages without an infobox `based_on` are excluded;
  fiction podcasts are dropped from the TV seed (Wikidata genre, else a Haiku yes/no). Rows with
  no source named are shown as *unsourced*, outside the headline counts.
- **URL discovery.** One web search per journalism row; only hits on the outlet's own domain
  or an archive are kept (`source_url_origin=search`, renamed from `source_url_via`).
- **Published data** at `/data/` and `/data/v0.1/`: adaptations.csv, publications.csv,
  gold.csv, eval.json, this changelog.
- **New seed: Texas Monthly, "How Texas Monthly Went Hollywood"** (June 2023), read with a
  reusable prose extractor (`ExtractProseAdaptations`, three passes unioned and cached; article
  headlines read from the linked pages). Adds *How to Create a Sex Scandal* (2023 docuseries,
  inspired by Michael Hall's "The Girl Who Told the Truth"); *Love & Death* and *Land Man*
  (= *Landman*) merge into existing rows. The page says Landman is based on Christian Wallace's
  article *and* the Boomtown podcast, which contradicts the curated "podcast-only" label: the row
  is flagged `origin-contested` for a human.
- **Hand overrides** (`data/overrides.csv`) apply after collect and again last; used for two seed
  typos/mislabels (*Blackhawk Down* → its film page, *Dark Waters* → NYT Magazine), the Land Man
  working title, and Disputed fidelity.
- **IMDb fallback**: rows no Wikipedia page links get OMDb's exact title + year match (never
  without a year).

- **Page layout.** The main page is the article list only; statistics, breakdowns, seeds, eval
  and data links moved to a Statistics tab (`/#stats`). The list opens on journalism + local /
  regional / alt-weekly + short fiction (no books or podcasts); a View menu switches to all rows,
  journalism, local, negatives or unsourced.

Result (2026-10-20): 204 rows; **137 journalism positives** by origin (v0: 111 by source type),
44 negatives, 21 unsourced; 35 local/regional/alt-weekly. Eval against the drafted gold set:
origin 96%, scope 100%, medium 100%, year 100%, fidelity 74%.

## v0 — 2026-10-20

Seed lists (Wikipedia list and category, Longform, Longreads, TETW, THR, IMDb list and poll),
TV seeds (manual + Wikipedia podcast adaptations), Wikidata/IMDb/RT/OMDb enrichment,
Wayback/archive.ph archiving, Haiku classification, synopses, review page.

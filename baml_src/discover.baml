// Find the published article behind a row that no seed linked. Haiku searches the web and
// reports only what it found; Python stores the URL (and splits Wayback links) and the
// archive step then looks for a snapshot of it.

enum LinkKind {
    Original
        @description("the publisher's own page for the piece (any URL era, even if now paywalled)"),
    Reprint
        @description("a full copy elsewhere: the author's site, a syndication, a reprint archive"),
    Archive @description("a snapshot on web.archive.org or archive.ph of the original"),
}

class ArticleLink {
    url: string? @description("the article's URL exactly as found; null if nothing clearly matches"),
    kind: LinkKind?,
    headline: string? @description("the piece's headline as published, if you found it"),
    publication: string?,
    date: string? @description("ISO date if shown (1998-01-15 or 1998-01), else the year"),
    evidence: string @description("one sentence: what you found and why it is (or isn't) this piece"),
}

function FindArticleUrl(
    film_title: string,
    film_year: int?,
    source_title: string?,
    source_author: string?,
    publication: string?,
    source_date: string?,
    cited_links: string[],
) -> ArticleLink {
    client: HaikuSearchOnce
    prompt: `
        A film or TV series was adapted from a published article (or newspaper series).
        Find that article online.

        First check the links cited by the film's Wikipedia article (below): if one of them
        is the piece itself, return it and stop. Only if none is, search the web, and answer
        only from what you found.

        How to search: try the headline in quotes with the outlet's name, then the outlet's
        domain (e.g. site:vanityfair.com) with the headline or the author and topic.

        Rules:
        - Prefer the publisher's own URL; otherwise a full reprint; otherwise an archive copy.
        - A search result on the outlet's own domain whose title matches the headline is
          enough: you don't need to open the page, and a paywall doesn't matter.
        - It must be the piece itself, by this author in this outlet: not a review of the film,
          not a later interview, not a Wikipedia or IMDb page, not a book listing.
        - For a multi-part series, give the first part.
        - Copy the URL exactly from a search result. If no result is the piece, return null.

        Film: ${film_title} (${year_text(film_year)})
        Article: ${source_title ?? "headline unknown"}
        Author: ${source_author ?? "unknown"}
        Publication: ${publication ?? "unknown"}, ${source_date ?? "date unknown"}

        Links cited by the film's Wikipedia article:
        ${cited_links.join("\n")}

        After searching, reply with only the JSON object below: no prose, no citations.
        ${ctx.output_format}
    `
}

test "parse a hand-written link" {
    let l = FindArticleUrl$parse(
        `
            {"url": "https://www.texasmonthly.com/articles/96-minutes/", "kind": "Original", "headline": "96 Minutes",
                      "publication": "Texas Monthly", "date": "2006-08", "evidence": "Texas Monthly's page for Colloff's piece."}
        `,
    );
    assert.equal(l.kind, LinkKind.Original);
    assert.equal(l.headline, "96 Minutes")
}

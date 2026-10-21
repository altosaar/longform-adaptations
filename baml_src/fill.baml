// Fill what a seed parser could not: a seed gives a film page's infobox and lead, or a
// list's prose blurb, and the model reads out the film and its written source(s).
// Only fields stated in the text are returned; unknowns stay null for human review.

class SourceWork {
    title: string @description("headline of the article, or title of the book/story, as written"),
    author: string?,
    publication: string? @description("newspaper, magazine or site that published it; null for a book"),
    date: string? @description("ISO date if stated (1998-01-15 or 1998-01), else the year, else null"),
}

class FilmSources {
    film_title: string? @description("the film or TV title, without '(2012 film)' disambiguation"),
    film_year: int? @description("release year of the film"),
    sources: SourceWork[] @description(
        "the written works the film is credited as based on or inspired by; usually one",
    ),
}

function FillGaps(known: string, context: string) -> FilmSources {
    client: Haiku
    prompt: `
        A dataset row links a film to the written work it was adapted from. Some fields
        are missing. Read the context and return the film and its written source(s).

        Rules:
        - Use only what the context states. Leave a field null rather than guess.
        - List every distinct written source the film is credited as based on (an article and
          a book are two sources; a multi-part series is one).
        - Skip non-written sources (other films, plays, songs, true events with no text named).
        - If the film title is missing and the context doesn't name it, you may name the film
          this exact source is well known to have been adapted into, but only if you are
          certain; otherwise leave it null.

        Known fields:
        ${known}

        Context:
        ---
        ${context}
        ---

        ${ctx.output_format}
    `
}

test "parse a hand-written gap fill" {
    let f = FillGaps$parse(
        `
            {"film_title": "Pain & Gain", "film_year": 2013,
                      "sources": [{"title": "Pain & Gain", "author": "Pete Collins", "publication": "Miami New Times", "date": "1999"}]}
        `,
    );
    assert.equal(f.sources.length(), 1);
    assert.equal(f.sources.at(0)?.publication, "Miami New Times")
}

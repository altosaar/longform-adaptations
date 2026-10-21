// Trace a credited book (or podcast) back to the periodical piece it grew out of, if any.
// Searches the web; Python writes origin_* only when a specific piece is named.

class PrintOrigin {
    from_periodical: bool @description(
        "true only if this work explicitly grew out of a specific article or newspaper series",
    ),
    origin_type: SourceType? @description("that piece's type, when from_periodical"),
    title: string?,
    author: string?,
    publication: string?,
    date: string? @description("ISO date if found, else the year"),
    evidence: string @description("one sentence: what you found"),
}

function TraceOrigin(
    work: string,
    author: string?,
    kind: string,
    adaptation: string,
    year: int?,
) -> PrintOrigin {
    client: HaikuSearch
    prompt: `
        The ${kind} "${work}"${if (author != null) { " by " + (author ?? "") } else { "" }} was adapted
        into "${adaptation}" (${year_text(year)}). Did this ${kind} grow out of a specific
        published article or newspaper series (often the author's own magazine piece)?
        Search the web, then answer only from what you found. "Based on extensive reporting"
        doesn't count: name the piece, its outlet and date, or say no.

        After searching, reply with only the JSON object below: no prose, no citations.
        ${ctx.output_format}
    `
}

testset "origins" {
    test "the orchid thief grew out of orchid fever" {
        let o = TraceOrigin("The Orchid Thief", "Susan Orlean", "book", "Adaptation", 2002);
        assert.is_true(o.from_periodical);
        assert.contains(o.title ?? "", "Orchid Fever")
    }

    test "into the wild grew out of death of an innocent" {
        let o = TraceOrigin("Into the Wild", "Jon Krakauer", "book", "Into the Wild", 2007);
        assert.is_true(o.from_periodical);
        assert.contains(o.publication ?? "", "Outside")
    }

    test "a novel has no periodical origin" {
        let o = TraceOrigin("Jurassic Park", "Michael Crichton", "book", "Jurassic Park", 1993);
        assert.equal(o.from_periodical, false)
    }
}

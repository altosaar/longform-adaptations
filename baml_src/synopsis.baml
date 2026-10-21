// One-sentence synopsis for a title that has no IMDb or Rotten Tomatoes synopsis, written
// from the text we do have: the Wikipedia lead, or else the source article's excerpt.

enum SynopsisBasis {
    Wikipedia,
    Article,
}

function WriteSynopsis(
    title: string,
    year: int?,
    medium: string?,
    basis: SynopsisBasis,
    text: string,
) -> string? {
    client: Haiku
    prompt: `
        Write a one-sentence synopsis (at most 35 words) of the ${medium ?? "film"} "${title}"
        (${year_text(year)}): who it follows and what happens, in the present tense, like an
        IMDb plot line. Don't name the cast, the director, awards or reception.

        Use only the text below. ${if (basis == SynopsisBasis.Article) { "It is the published article the work was adapted from, so describe the true story it tells." } else { "It is the work's Wikipedia lead." }}

        ---
        ${text}
        ---

        Reply with the sentence only. If the text doesn't say what the work is about (a
        disambiguation page, a stub, an announcement without a plot), reply null instead.
    `
}

test "parse a synopsis" {
    assert.equal(
        WriteSynopsis$parse(`A con man poses as a film producer to rescue six Americans from Tehran.`),
        "A con man poses as a film producer to rescue six Americans from Tehran.",
    )
}

// TV seeds: did a podcast that became a series grow out of a published article?
// Filled into the row as its print origin (source_title/author/publication/date) when found.

class PodcastOrigin {
    from_article: bool @description("true only if the podcast is based on a specific published article or series"),
    title: string?,
    author: string?,
    publication: string?,
    date: string? @description("ISO date if found, else the year"),
    evidence: string @description("one sentence: what you found"),
}

function FindPodcastOrigin(podcast: string, adaptation: string, year: int?) -> PodcastOrigin {
    client: HaikuSearch
    prompt: `
        The podcast "${podcast}" was adapted into "${adaptation}" (${year_text(year)}).
        Did this podcast grow out of a published article (newspaper, magazine, or online
        outlet)? Search the web, then answer only from what you found. A podcast by a
        journalist on a topic they also wrote about counts only if the podcast is explicitly
        based on or expanded from that article. If so: its title, author, publication, date.

        After searching, reply with only the JSON object below: no prose, no citations.
        ${ctx.output_format}
    `
}

// Addendum 2 #7: only nonfiction podcasts belong in the TV seed; fiction ones are noise.
// Asked only when the podcast's Wikidata genre (P136) doesn't settle it.
function IsNonfictionPodcast(podcast: string, adaptation: string) -> bool {
    client: Haiku
    prompt: `
        Is the podcast "${podcast}" (adapted as "${adaptation}") nonfiction: reporting,
        documentary, true crime, history, or interviews about real people and events?
        Scripted fiction, audio drama, comedy and improv shows are not.

        ${ctx.output_format}
    `
}

test "serial is nonfiction, limetown is not" {
    assert.equal(IsNonfictionPodcast$parse(`true`), true)
}

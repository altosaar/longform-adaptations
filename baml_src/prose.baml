// Read adaptations out of an editorial page written as prose (not a list): e.g. a magazine's
// own account of its Hollywood deals. Only what the page states; links copied from the page.

class ProseAdaptation {
    screen_title: string @description("the film or series title, as the page gives it"),
    medium: string? @description("film, series, docuseries, or tv movie, if the page says"),
    status: string @description("produced (released or filming), or optioned / in development"),
    source_title: string? @description("the article's headline, if the page names it"),
    source_author: string?,
    publication: string?,
    source_year: int?,
    source_url: string? @description("the link the page gives for the source article, copied exactly"),
    relation: string @description("the page's own wording: 'based on', 'inspired by', …"),
}

function ExtractProseAdaptations(page_url: string, page: string) -> ProseAdaptation[] {
    client: Haiku
    prompt: `
        List every film or TV adaptation this page says was made from a published article
        or newspaper/magazine story (including ones also credited to a podcast or a book).
        Skip titles mentioned for other reasons (a cast member's earlier show, a franchise)
        and unnamed projects ("some fifty stories in development").

        Page (${page_url}), as text with links as [text](url):
        ---
        ${page}
        ---

        ${ctx.output_format}
    `
}

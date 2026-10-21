// Classify the written source a film was adapted from. One call per row; the typed
// result is written straight back to the adaptations table by steps/classify.py.

enum SourceType {
    News_Article @description("reported newspaper story or series"),
    Magazine_Feature @description("long-form magazine or online-magazine feature"),
    Column_Or_Essay @description("first-person essay, column, op-ed"),
    Wire_Or_Brief @description("short news item, wire copy"),
    Short_Fiction @description("fiction published in a periodical; NEGATIVE class"),
    Book @description("book-length work, even if excerpted; NEGATIVE class"),
    Podcast_Or_Other,
    Unknown,
}

enum PublicationScope {
    National,
    Regional @description("state or multi-city, e.g. Texas Monthly"),
    Local @description("single city or town daily/weekly"),
    Alt_Weekly @description("e.g. Miami New Times, LA Weekly"),
    Trade,
    Online_Only,
    Wire,
    Unknown,
}

enum Fidelity {
    Direct @description("credited 'based on the article'"),
    Loose @description("credited but substantially fictionalized"),
    Inspired_By @description("uncredited or 'inspired by' only"),
}

class Classification {
    source_type: SourceType @description("the work this row names (source_title)"),
    origin_type: SourceType @description(
        "the FIRST published work in the chain: the article a book grew out of, etc.",
    ),
    origin_title: string? @description(
        "headline/title of that first work when it differs from source_title; else null",
    ),
    credited_source_type: SourceType @description("what the screen credits name: a book, an article, a podcast…"),
    publication_scope: PublicationScope,
    publication_normalized: string @description("canonical outlet name, e.g. 'The New York Times Magazine'"),
    fidelity: Fidelity,
    source_chain: string @description(
        "how the work reached the screen, first published work first: 'article → film', 'article → book → film', 'article → podcast → series', 'book → film'",
    ),
    confidence: float @description("0 to 1"),
    notes: string @description("one sentence; name any doubt"),
}

function ClassifyAdaptation(
    film_title: string,
    film_year: int?,
    source_title: string,
    source_author: string?,
    publication: string?,
    source_date: string?,
    excerpt: string?,
    context: string?,
) -> Classification {
    client: Haiku
    prompt: `
        You classify the written source a film or series was adapted from, and trace it
        back to its first publication.

        - source_type: the work this row names. A book is Book even if it began as an
          article; fiction in a periodical is Short_Fiction; a podcast is Podcast_Or_Other,
          even when a magazine or newspaper produced it.
        - origin_type / origin_title: the first published work in the chain. A book that
          grew out of the author's own magazine piece has a Magazine_Feature origin (give
          that piece's headline); a podcast that grew out of a newspaper series has a
          News_Article origin. If the named work is itself the first, repeat its type and
          leave origin_title null.
        - credited_source_type: what the screen credits name ("based on the book by…").
        - source_chain: the path, first publication first, e.g. "article → book → film".
        - publication_scope: national outlets (The New Yorker, Wired, Rolling Stone, The New
          York Times Magazine) are National; city magazines with national circulation (New
          York, Los Angeles, Washingtonian) and state magazines (Texas Monthly) are Regional;
          a big-city metro daily (Los Angeles Times, Chicago Tribune, The Boston Globe) or a
          town paper is Local; alt-weeklies are Alt_Weekly.
        - fidelity: Direct if credited as based on the work, Loose if credited but heavily
          fictionalized, Inspired_By if uncredited or "inspired by". A docuseries that doesn't
          credit the article is Inspired_By.

        Film: ${film_title} (${year_text(film_year)})
        Source: "${source_title}" by ${source_author ?? "unknown author"}
        Publication: ${publication ?? "unknown"}, ${source_date ?? "date unknown"}
        Excerpt: ${excerpt ?? "(none)"}
        Seed notes: ${context ?? "(none)"}

        ${ctx.output_format}
    `
}

// The journalism positives (Addendum 2 #1): a row counts iff its origin is one of these.
function is_journalism(t: SourceType) -> bool {
    t == SourceType.News_Article
        || t == SourceType.Magazine_Feature
        || t == SourceType.Column_Or_Essay
        || t == SourceType.Wire_Or_Brief
}

function year_text(year: int?) -> string {
    if (year != null) {
        year.to_string()
    } else {
        "year unknown"
    }
}

// --- hand-labeled rows: real model calls, run with `just test` before a full classify pass ---

testset "labeled" {
    test "pain and gain is alt weekly" {
        let c = ClassifyAdaptation("Pain & Gain", 2013, "Pain & Gain", "Pete Collins", "Miami New Times", "1999", null, null);
        assert.equal(c.publication_scope, PublicationScope.Alt_Weekly);
        assert.is_true(is_journalism(c.origin_type))
    }

    test "spotlight is local news" {
        let c = ClassifyAdaptation(
            "Spotlight",
            2015,
            "Church Allowed Abuse by Priest for Years",
            "Boston Globe Spotlight Team",
            "The Boston Globe",
            "2002-01-06",
            null,
            null,
        );
        assert.equal(c.publication_scope, PublicationScope.Local);
        assert.equal(c.source_type, SourceType.News_Article)
    }

    test "into the wild credits the book" {
        let c = ClassifyAdaptation("Into the Wild", 2007, "Into the Wild", "Jon Krakauer", null, "1996", null, null);
        assert.equal(c.source_type, SourceType.Book);
        assert.equal(c.credited_source_type, SourceType.Book)
    }

    test "saturday night fever is a magazine origin" {
        let c = ClassifyAdaptation(
            "Saturday Night Fever",
            1977,
            "Tribal Rites of the New Saturday Night",
            "Nik Cohn",
            "New York",
            "1976-06-07",
            null,
            null,
        );
        assert.equal(c.origin_type, SourceType.Magazine_Feature)
    }

    test "texas monthly is regional" {
        let c = ClassifyAdaptation(
            "Bernie",
            2011,
            "Midnight in the Garden of East Texas",
            "Skip Hollandsworth",
            "Texas Monthly",
            "1998-01",
            null,
            null,
        );
        assert.equal(c.publication_scope, PublicationScope.Regional);
        assert.equal(c.source_type, SourceType.Magazine_Feature)
    }

    test "argo is a national magazine feature" {
        let c = ClassifyAdaptation(
            "Argo",
            2012,
            "How the CIA Used a Fake Sci-Fi Flick to Rescue Americans From Tehran",
            "Joshuah Bearman",
            "Wired",
            "2007-04",
            null,
            null,
        );
        assert.equal(c.publication_scope, PublicationScope.National);
        assert.equal(c.source_type, SourceType.Magazine_Feature)
    }

    test "dallas buyers club is a regional daily" {
        let c = ClassifyAdaptation(
            "Dallas Buyers Club",
            2013,
            "Buying time",
            "Bill Minutaglio",
            "The Dallas Morning News",
            "1992-08-09",
            null,
            null,
        );
        assert.equal(c.source_type, SourceType.News_Article);
        assert.is_true(
            c.publication_scope == PublicationScope.Local
                || c.publication_scope == PublicationScope.Regional,
        )
    }

    test "kill the messenger is a newspaper series" {
        let c = ClassifyAdaptation(
            "Kill the Messenger",
            2014,
            "Dark Alliance",
            "Gary Webb",
            "San Jose Mercury News",
            "1996",
            null,
            null,
        );
        assert.equal(c.origin_type, SourceType.News_Article);
        assert.is_true(
            c.publication_scope == PublicationScope.Local
                || c.publication_scope == PublicationScope.Regional,
        )
    }

    test "the sessions is a first person essay" {
        let c = ClassifyAdaptation(
            "The Sessions",
            2012,
            "On Seeing a Sex Surrogate",
            "Mark O'Brien",
            "The Sun",
            "1990-05",
            null,
            null,
        );
        assert.equal(c.source_type, SourceType.Column_Or_Essay)
    }

    test "brokeback mountain is short fiction" {
        let c = ClassifyAdaptation(
            "Brokeback Mountain",
            2005,
            "Brokeback Mountain",
            "Annie Proulx",
            "The New Yorker",
            "1997-10-13",
            null,
            null,
        );
        assert.equal(c.source_type, SourceType.Short_Fiction)
    }

    test "biker boyz is alt weekly" {
        let c = ClassifyAdaptation(
            "Biker Boyz",
            2003,
            "Biker Boyz",
            "Michael Gougis",
            "New Times Los Angeles",
            "2000-04",
            null,
            null,
        );
        assert.equal(c.publication_scope, PublicationScope.Alt_Weekly)
    }

    test "new york magazine is regional" {
        let c = ClassifyAdaptation(
            "Hustlers",
            2019,
            "The Hustlers at Scores",
            "Jessica Pressler",
            "New York Magazine",
            "2015-12-28",
            null,
            null,
        );
        assert.equal(c.source_type, SourceType.Magazine_Feature);
        assert.equal(c.publication_scope, PublicationScope.Regional)
    }

    test "landman credits only a podcast" {
        let c = ClassifyAdaptation(
            "Landman",
            2024,
            "Boomtown",
            "Christian Wallace",
            "Texas Monthly",
            "2019",
            null,
            "Source is a podcast (Boomtown, Texas Monthly) adapted into a TV series; no print article found.",
        );
        assert.equal(c.source_type, SourceType.Podcast_Or_Other);
        assert.equal(c.origin_type, SourceType.Podcast_Or_Other)
    }

    test "tiger king is a docuseries" {
        let c = ClassifyAdaptation(
            "Tiger King",
            2020,
            "Joe Exotic",
            "Robert Moor",
            "New York",
            "2019-09",
            null,
            "Netflix documentary series.",
        );
        assert.equal(c.fidelity, Fidelity.Inspired_By)
    }

    test "inventing anna is an article to series" {
        let c = ClassifyAdaptation(
            "Inventing Anna",
            2022,
            "Maybe She Had So Much Money She Just Lost Track of It",
            "Jessica Pressler",
            "New York / The Cut",
            "2018-05-28",
            null,
            null,
        );
        assert.equal(c.source_type, SourceType.Magazine_Feature);
        assert.equal(c.origin_type, SourceType.Magazine_Feature)
    }
}

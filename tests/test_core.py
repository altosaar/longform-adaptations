import sqlite3

import db
from seeds import iso_date, parse_citation, split_wayback
from steps.archive import raw_snapshot
from steps.dedupe import same_source


def test_iso_date():
    assert iso_date("January 23, 1995") == "1995-01-23"
    assert iso_date("Apr 2007") == "2007-04"
    assert iso_date("1972") == "1972"
    assert iso_date("") == ""


def test_parse_citation():
    c = parse_citation('"Orchid Fever", The New Yorker, January 23, 1995')
    assert c == {"source_title": "Orchid Fever", "publication": "The New Yorker", "source_date": "1995-01-23"}
    assert parse_citation("Series of articles in Miami New Times") == {"publication": "Miami New Times"}


def test_wayback_links():
    url = "https://web.archive.org/web/20131222051248/http://www.outsideonline.com/x.html"
    assert split_wayback(url) == ("http://www.outsideonline.com/x.html", url)
    assert raw_snapshot(url) == "https://web.archive.org/web/20131222051248id_/http://www.outsideonline.com/x.html"


def test_norm_and_ids():
    assert db.norm("All The President’s Men") == db.norm("All the President's Men (film)")
    assert db.norm("Pain & Gain") == db.norm("Pain and Gain")
    assert db.row_id("Argo (2012 film)", "X") == db.row_id("argo", "x")


def test_upsert_fills_blanks_only(tmp_path):
    con = db.connect(tmp_path / "t.sqlite")
    rid = db.upsert(con, {"film_title": "Argo", "source_title": "X", "seed": "a", "publication": "Wired"})
    db.upsert(con, {"film_title": "Argo", "source_title": "X", "seed": "b", "publication": "WIRED", "film_year": 2012})
    row = con.execute("SELECT * FROM adaptations WHERE id = ?", (rid,)).fetchone()
    assert (row["publication"], row["film_year"], row["seed"]) == ("Wired", 2012, "a,b")
    db.upsert(con, {"id": rid, "publication": "WIRED"}, force=True)
    assert con.execute("SELECT publication FROM adaptations").fetchone()[0] == "WIRED"
    assert isinstance(con, sqlite3.Connection)


def _row(title, author="", origin=None):
    return {"source_title": title, "source_author": author, "origin_type": origin, "origin_title": None}


def test_same_source():
    assert same_source(_row("Tribal Rites of Saturday Night"), _row("Tribal Rites of the New Saturday Night"))
    assert same_source(_row(""), _row("Anything"))
    # same author, same journalism origin: one piece under two names
    assert same_source(_row("The Great Escape", "Joshuah Bearman", "magazine_feature"),
                       _row("How the CIA Used a Fake Sci-Fi Flick", "Joshuah Bearman", "magazine_feature"))
    # same author, article vs memoir with no periodical origin: two rows
    assert not same_source(_row("Orchid Fever", "Susan Orlean", "magazine_feature"),
                           _row("The Orchid Thief", "Susan Orlean", "book"))


def test_rowspans_expand():
    from seeds.wikipedia_podcast_adaptations import parse

    html = """<table class="wikitable">
    <tr><th>Podcast</th><th>Format</th><th>Title of adapted work</th><th>Year of release</th></tr>
    <tr><td>A</td><td rowspan="2">Television</td><td>Show A</td><td>2018</td></tr>
    <tr><td>B</td><td>Show B</td><td>2019</td></tr>
    <tr><td>C</td><td>Book</td><td>Book C</td><td>2020</td></tr>
    <tr><td>D</td><td>Film</td><td>Movie D</td><td>2021</td></tr></table>"""
    rows = parse(html)
    assert [(r["film_title"], r["film_year"], r["medium"]) for r in rows] == [
        ("Show A", 2018, None), ("Show B", 2019, None), ("Movie D", 2021, "film")]


def test_first_sentence():
    from steps.synopsis import first_sentence

    assert first_sentence("On Nov. 4, 1979, militants storm the U.S. embassy. Six escape.") == (
        "On Nov. 4, 1979, militants storm the U.S. embassy.")
    assert first_sentence("A profile of J. R. Moehringer. More.") == "A profile of J. R. Moehringer."
    assert first_sentence("N/A") == ""

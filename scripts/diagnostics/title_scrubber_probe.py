from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.app.services.media_title_parser import parse_media_title  # noqa: E402


CASES: list[tuple[str, str, int | None]] = [
    ("Blade Runner 2049 (2017) AV1-10bit 1080p 7RIP", "Blade Runner 2049", 2017),
    ("Wonder Woman 1984 (2020) 1080p BluRay x264", "Wonder Woman 1984", 2020),
    ("Argentina 1985 (2022) 1080p WEB-DL x264", "Argentina 1985", 2022),
    ("Love Story 2050 (2008) 720p AMZN WEBRip x265", "Love Story 2050", 2008),
    ("Death Race 2000 (1975) 1080p BluRay", "Death Race 2000", 1975),
    ("Equalizer 2000 (1987) 720p Ac3 Spa Sub Ita Eng-MIRCrew", "Equalizer 2000", 1987),
    ("Frankenstein 1970 (1958) DVDRip", "Frankenstein 1970", 1958),
    ("Pastorale 1943 (1978) WEBRip", "Pastorale 1943", 1978),
    ("The Matrix (1999) DVDRip - NonyMovies", "The Matrix", 1999),
    ("Harry Potter 2009 Open Matte 1080p WEBRip x265", "Harry Potter", 2009),
    (
        "The African Queen (1951)-Humphrey Bogart & Katharine Hepburn-1080p-H264-AC 3 (DolbyDigital-5.1) ? nickarad",
        "The African Queen",
        1951,
    ),
    ("Django (1966)-Franco Nero-1080p-H264-AC 3 (DolbyDigital-5.1) ? nickarad", "Django", 1966),
    ("The Pink Panther (1963)-Peter Sellers-1080p-H264-AC 3", "The Pink Panther", 1963),
    ("The Great Escape (1963) 1080p-H264-AAC", "The Great Escape", 1963),
    ("The Guns of Navarone (1961) 1080p-H264-AC 3 (DTS 5.1) & nickarad", "The Guns of Navarone", 1961),
    ("Alice in Wonderland (1951)-Cartoon-1080p-H264-AC 3", "Alice in Wonderland", 1951),
    ("Lady and the Tramp (1955) Cartoon movie-1080p-H264-AC 3", "Lady and the Tramp", 1955),
    ("Blonde Death [1984 - USA] no budget cult classic", "Blonde Death", 1984),
    ("Message from Space [1978 - Japan] (English Version) sci fi", "Message from Space", 1978),
    ("Annie - The Virgin of Saint Tropez [1975 - France] (ENG) erotic drama", "Annie - The Virgin of Saint Tropez", 1975),
    ("Young Butterflies [1975 - Sweden] (English) erotic drama", "Young Butterflies", 1975),
    ("Riot in a Women's Prison [1974 - Italy] (english version) drama", "Riot in a Women's Prison", 1974),
    ("The Steel Helmet [1951 - USA] Samuel Fuller Korean War action", "The Steel Helmet", 1951),
    ("Black Tea [2024 - France + Taiwan] (DUAL Zho Fra) drama", "Black Tea", 2024),
    ("Under the Volcano - Pod wulkanem [2024 - Poland] (Ukrainian) war drama", "Under the Volcano - Pod wulkanem", 2024),
    ("Armadillo *2010* [BDRip.XviD-miguel] [ENG]", "Armadillo", 2010),
    ("Lust Caution *2007* [DVDRip.XviD-miguel] [ENG]", "Lust Caution", 2007),
    ("Alvarez Kelly *1966* [DVDRip XviD AC 3 v62] [Lektor PL]", "Alvarez Kelly", 1966),
    ("Trust.1990.(1001.Movies.You.Must.See).1080p.BRRip.x264-Classics", "Trust", 1990),
    ("Lamerica.1994.(1001 Movies You Must See Before You Die).720p.x264-Classics", "Lamerica", 1994),
    ("Zero.Kelvin.1995.(1001.Movies.You.Must.See).720p.x264-Classics", "Zero Kelvin", 1995),
    ("Mother.and.Son.1997.(1001 Movies).1080p.BRRip.x264-Classics", "Mother and Son", 1997),
    ("The Sound of Music 1965 45th Anniv (1080p Bluray)", "The Sound of Music", 1965),
    ("Bobby (2006) Language:English-Russian, Subs:Spanish-Russian-English", "Bobby", 2006),
    ("La viaccia - Le mauvais chemin (1961) lang: IT+SP with subs: FR+EN", "La viaccia - Le mauvais chemin", 1961),
    ("Diep (2005) Dutch audio-no subs", "Diep", 2005),
    ("Ma Mere (2004) NC-17 Uncut English subs )", "Ma Mere", 2004),
    ("Caught Stealing, 2025, hardcoded nl subs", "Caught Stealing", 2025),
    ("The Hollywood Ten (John Berry, 1950)_Sub.srt.PTBR", "The Hollywood Ten", 1950),
    ("Righteous.Kill[2008]BRrip-aЯRo", "Righteous Kill", 2008),
    ("Reindeer.Games[2000]BRrip-aЯRo", "Reindeer Games", 2000),
    ("Five.Miles.To.Midnight[1962]BRrip-aЯRo", "Five Miles To Midnight", 1962),
    ("Coraline[2009]DvDrip-Latino-JcGoku21", "Coraline", 2009),
    ("RocknRolla[2008]DvDrip-aXXo", "RocknRolla", 2008),
    ("Persepolis (2007) [HDRip-AC3][Spanish]", "Persepolis", 2007),
    ("Kung Fu Sion (2004) [HDRip-AC3][Spanish]", "Kung Fu Sion", 2004),
    ("Help! (1965)Mp-4-Blu-Ray Rip-1080p-AAC-DSD", "Help!", 1965),
    ("Lady and the Tramp (1955)Mp-4-X264-Dvd-Rip-480p-AAC-DSD", "Lady and the Tramp", 1955),
    ("[Blu-ray] Borsalino (1970) [Jacques Deray, Belmondo, Alain Delon]", "Borsalino", 1970),
    ("[DVDRip - low quality] Les Novices (1970) [cast]", "Les Novices", 1970),
    ("[TVRip low quality] Madly / The Love Mates (1970) - Roger Kahane", "Madly / The Love Mates", 1970),
    ("[FOUND] Terrore.Sul.Treno-Terror.On.A.Train.(1953).ITA-ENG", "Terrore Sul Treno - Terror On A Train", 1953),
    ("Humphrey Bogart- The Caine Mutiny (1954) 1080p-H264", "The Caine Mutiny", 1954),
    ("Kirk Douglas - 20000 League Under Sea [1954] 1080p-H264", "20000 League Under Sea", 1954),
    ("Walt Disney - Corn Chips (1951) 1080p-H264", "Corn Chips", 1951),
    ("JAMES BOND-From Russia With Love (1963) 1080p-H264", "From Russia With Love", 1963),
    ("CHARLIE CHAPLIN - A King in New York (1957) 720p-H264", "A King in New York", 1957),
    ("Mr. Ove - En Man Som Heter Ove (2015) 1080p H265", "Mr Ove - En Man Som Heter Ove", 2015),
    ("Dirty Dancing 2 - Havana Nights (2004) WEBDL 1080p", "Dirty Dancing 2 - Havana Nights", 2004),
    ("Saw - L'enigmista (2004) UpScaled 2160p", "Saw - L'enigmista", 2004),
    ("The Odyssey - L'Odissea (1997) ITA ENG Ac3", "The Odyssey - L'Odissea", 1997),
    ("Psycho - Psyco.1960.iTA.ENG", "Psycho - Psyco", 1960),
    ("Indovina chi viene a cena-Guess who.s coming to dinner (1967)", "Indovina chi viene a cena - Guess who's coming to dinner", 1967),
    ("Otoko no monshô - ruten no okite (1965)", "Otoko no monshô - ruten no okite", 1965),
    ("Cenerentola (Cinderella - 1950)[1080p]", "Cenerentola (Cinderella)", 1950),
    ("Safe.-.2012.-.Blu-ray.-.1080p.-.x264", "Safe", 2012),
    ("Marnie.-.1964.-.Blu-ray.-.1080p.-.x264", "Marnie", 1964),
    ("Psycho.-.1960.-.Blu-ray.-.1080p.-.x264", "Psycho", 1960),
    ("Never Ending Story (1984) 1080p BluRay", "Never Ending Story", 1984),
    ("John Wick Chapter 2 (2017) 1080p BluRay", "John Wick Chapter 2", 2017),
    ("Big Hero 6 (2014) 1080p BluRay", "Big Hero 6", 2014),
    ("Inside Out 2 (2024) 1080p WEB-DL", "Inside Out 2", 2024),
    ("The BFG (2016) 1080p BluRay", "The BFG", 2016),
    ("Kingdom of Heaven DC Roadshow Version 2005 2160p UHD", "Kingdom of Heaven", 2005),
    ("Legend 1985 DC 1080p BluRay", "Legend", 1985),
    ("LEGO DC Comics Super Heroes - Justice League - Cosmic Clash (2016).1080p.H265", "LEGO DC Comics Super Heroes - Justice League - Cosmic Clash", 2016),
    ("DC.League.of.Super-Pets.2022.1080p.BluRay.x264", "DC League of Super-Pets", 2022),
    ("The Final Cut (2004) WEBRip 1080p HEVC AAC", "The Final Cut", 2004),
    ("A.Final.Cut.For.Orson.40.Years.in.The.Making.2018.1080p.NF.WEBRip", "A Final Cut For Orson 40 Years in The Making", 2018),
    ("V/H/S (2012) (1080p BluRay x265)", "V/H/S", 2012),
    ("V/H/S: Viral (2014) (1080p BluRay x265)", "V/H/S: Viral", 2014),
    ("[REC] (2007) 1080p BluRay", "[REC]", 2007),
    ("[18+] Diet of Sex 2014 DVDRip", "[18+] Diet of Sex", 2014),
    ("My.Show.S01E01.1080p.WEB-DL.x264-GROUP.mkv", "My Show S01E01", None),
    ("My.Show.S1E1.720p.HDTV.x264-GROUP.mkv", "My Show S1E1", None),
    ("My.Show.1x02.1080p.WEB-DL.x265-GROUP.mkv", "My Show 1x02", None),
    ("Anime.Title.E01.1080p.WEB-DL.AAC2.0.x264-GROUP.mkv", "Anime Title E01", None),
    ("Anime.Title.EP01.1080p.WEB-DL.AAC2.0.x264-GROUP.mkv", "Anime Title EP01", None),
    ("Anime.Title.OVA.01.1080p.BluRay.x265-GROUP.mkv", "Anime Title OVA 01", None),
    ("The Italian Job 1969 1080p BluRay", "The Italian Job", 1969),
    ("The French Connection 1971 1080p BluRay", "The French Connection", 1971),
]


def main() -> int:
    passed = 0
    lines = []
    for index, (raw, expected_title, expected_year) in enumerate(CASES, 1):
        parsed = parse_media_title(title=None, original_filename=raw, year=None)
        display = parsed["display_title"]
        year = parsed["parsed_year"]
        ok = display == expected_title and year == expected_year
        passed += int(ok)
        status = "PASS" if ok else "FAIL"
        lines.append(f"{status} #{index}: {raw}")
        lines.append(f"  expected: {expected_title!r} / {expected_year!r}")
        lines.append(f"  actual:   {display!r} / {year!r}")
        lines.append(f"  warnings: {', '.join(parsed.get('warnings') or [])}")
    lines.insert(0, f"Title Scrubber v1.0.0 parser probe: {passed}/{len(CASES)} passed")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

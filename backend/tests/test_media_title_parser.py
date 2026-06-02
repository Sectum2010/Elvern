from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from backend.app.db import get_connection, utcnow_iso
from backend.app.services.library_movie_identity_service import _dedupe_group_key
from backend.app.services.media_title_parser import TITLE_PARSER_VERSION, parse_media_title
from backend.app.services.title_normalization import (
    build_poster_candidate_family,
    clean_title_for_matching,
    resolve_poster_match_identity,
    resolve_title_metadata,
)


FIXTURE_PATH = Path(__file__).with_name("fixtures") / "media_title_parser_cases.json"
FIXTURE_CASES = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
CASES_BY_NAME = {case["name"]: case for case in FIXTURE_CASES}
REPO_ROOT = Path(__file__).resolve().parents[2]
EDITION_PHRASE_MAP = {
    "director's cut": ["director", "cut"],
    "final cut": ["final", "cut"],
    "extended": ["extended"],
    "unrated": ["unrated"],
}
PHASE15_DETERMINISTIC_CASES = [
    (
        "Avatar - The Way of Water (2022) IMAX 1080p 10bit Bluray x265 HEVC [Org DDP 5.1 Hindi + DDP 7.1 Atmos English] MSubs ~ TombDoc",
        "Avatar - The Way of Water",
        2022,
    ),
    ("Dune - Part Two (2024) AV1 1080p 7RIP", "Dune - Part Two", 2024),
    ("Dune - Part One (2021) AV1 1080p 7RIP", "Dune - Part One", 2021),
    (
        "Venom - The Last Dance (2024) 1080p 10bit Bluray x265 HEVC [Org DD 5.1 Hindi + DD 5.1 English] ESubs ~ TombDoc",
        "Venom - The Last Dance",
        2024,
    ),
    (
        "F1 - The Movie (2025) EUR 1080p 10bit Bluray x265 HEVC [Org DDP 5.1 Atmos Hindi + DDP 7.1 Atmos English] MSubs ~ TombDoc",
        "F1 - The Movie",
        2025,
    ),
    ("The Italian Job 2003 2160p Bluray x265 DDP+DTS-KiNGDOM", "The Italian Job", 2003),
    ("The Final Cut (2004) WEBRip 1080p HEVC AAC ITA ENG - Lullozzo", "The Final Cut", 2004),
    ("LEGO DC - Shazam! Magic and Monsters (2020).1080p.H265.EAC3.6CH-MNKYDDL", "LEGO DC - Shazam! Magic and Monsters", 2020),
    ("LEGO DC Batman - Family Matters (2019).1080p.H265.EAC3.6CH-MNKYDDL", "LEGO DC Batman - Family Matters", 2019),
    (
        "LEGO DC Comics Super Heroes - Justice League - Cosmic Clash (2016).1080p.H265.EAC3.6CH-MNKYDDL",
        "LEGO DC Comics Super Heroes - Justice League - Cosmic Clash",
        2016,
    ),
    ("A Beautiful Mind (2001) (2160p x265 10bit HDR UHD BD Atmos) [Prof]", "A Beautiful Mind", 2001),
    ("The Nice Guys (2016) (2160p x265 10bit HDR UHD BD Atmos) [Prof]", "The Nice Guys", 2016),
    ("The Red Turtle (2016) (1080p BluRay x265 10-bit Fre 5.1 AAC) [WeSLeY]", "The Red Turtle", 2016),
    ("Spirited Away (2001) (1080p BluRay x265 10-bit Eng 5.1 + Jap 5.1 AAC) [WeSLeY]", "Spirited Away", 2001),
    ("Kill Bill Vol. 2 (2004) (2160p x265 10bit HDR UHD BD DTS-HD MA 5.1) [Prof]", "Kill Bill Vol 2", 2004),
    ("Death By Hanging 1968 JPN SUB ENG, ITA 1080p BluRay x264", "Death By Hanging", 1968),
    ("A Love Story 1942 ITA SUB ENG, ITA DVDRip x264", "A Love Story", 1942),
    ("The Man With The Suitcase 1984 FRE SUB ENG, ITA 1080p BluRay x264", "The Man With The Suitcase", 1984),
    ("Apocalypse In The Tropics 2024 PT-BR MULTISUB 1080p WEB-DL x264", "Apocalypse In The Tropics", 2024),
    ("The.Roundup.2022.iTA-KOR.Bluray.1080p.x264-CYBER.mkv", "The Roundup", 2022),
    ("The Matrix (1999) DVDRip - NonyMovies", "The Matrix", 1999),
    ("Annie (1999) DVDRIP", "Annie", 1999),
    ("Il testimone (2001) DVDRip SD x264 AAC ITA - Bifra", "Il testimone", 2001),
    ("Chiedimi quello che vuoi (2024) DVDRip Mkv H264 AC3 iTA 5.1 No Sub - CoSmo Crew", "Chiedimi quello che vuoi", 2024),
    ("The Animal (2001) DVDRip SD H264 ITA ENG SPA Ac3 5.1 sub Ita Eng Spa [ArMor] iDN_CreW", "The Animal", 2001),
    ("V/H/S (2012) (1080p BluRay x265 10bit EAC3 5.1 Ghost) [QxR]", "V/H/S", 2012),
    ("V/H/S: Viral (2014) (1080p BluRay x265 10bit EAC3 5.1 Ghost) [QxR]", "V/H/S: Viral", 2014),
    ("Pirates Of The Caribbean 3 At World's End 2007 [EN/FR/ES] Bluray 1080p AV1 OPUS 5.1-UH", "Pirates Of The Caribbean 3 At World's End", 2007),
    ("Batman Begins 2005 Bluray IMAX 2160p AV1 HDR10 EN/FR/ES/DE OPUS 5.1-UH", "Batman Begins", 2005),
    ("Lethal Weapon 4 1998 Bluray 1080p AV1 EN/FR/DE/ITA/ES OPUS 5.1-UH", "Lethal Weapon 4", 1998),
    ("My.Show.S01E01.1080p.WEB-DL.x264-GROUP.mkv", "My Show S01E01", None),
    ("My.Show.S1E1.720p.HDTV.x264-GROUP.mkv", "My Show S1E1", None),
    ("My.Show.1x02.1080p.WEB-DL.x265-GROUP.mkv", "My Show 1x02", None),
    ("Anime.Title.EP01.1080p.WEB-DL.AAC2.0.x264-GROUP.mkv", "Anime Title EP01", None),
    ("Anime.Title.OVA.01.1080p.BluRay.x265-GROUP.mkv", "Anime Title OVA 01", None),
]
PHASE17_DETERMINISTIC_CASES = [
    (
        "Transporter 2 (2005) (WEBDL-1080p x265 AC3 5.1 [EN] [EN+SV]) MrPanda",
        "Transporter 2",
        2005,
    ),
    ("Beast (Bestia) 2021 No Language 1080p WEB-DL x264", "Beast (Bestia)", 2021),
    ("The French Italian 2025 1080p AMZN WEBRip DDP2.0 H265", "The French Italian", 2025),
    (
        "Dont Look Up - Sci-Fi Comedy 2021 Eng Fra Ita Rus Ukr Multi Subs 2160p [HEVC-mp4]",
        "Dont Look Up",
        2021,
    ),
    (
        "Solaris - Sci-Fi 1972 Eng Rus Comm Multi Subs 1080p [HEVC-mp4]",
        "Solaris",
        1972,
    ),
    ("Stigmata - Horror 1999 Eng Rus 1080p BluRay x264.mkv", "Stigmata", 1999),
    ("Mission: Impossible - Ghost Protocol (2011) 1080p BluRay x264.mkv", "Mission: Impossible - Ghost Protocol", 2011),
    (
        "The Hunger Games: Mockingjay - Part 2 (2015) 1080p BluRay x264.mkv",
        "The Hunger Games: Mockingjay - Part 2",
        2015,
    ),
    ("Epoch / Epoch: Evolution (2001/2003) SD", "Epoch / Epoch: Evolution", 2001),
    (
        "Help! I'm a Fish／Hjælp! Jeg er en fisk／A Fish Tale (2000) DVDRip.mkv",
        "Help! I'm a Fish/Hjælp! Jeg er en fisk/A Fish Tale",
        2000,
    ),
    ("Fantastic Mr Fox 2009 1080p BluRay x264.mkv", "Fantastic Mr Fox", 2009),
    (
        "Avatar.The.Way.Of.The.Water.2022.48fps.2160p.UHD.BluRay.x265.mkv",
        "Avatar The Way Of The Water",
        2022,
    ),
    ("[moon] Interstellar 2014 WEBRip x264 AAC.mkv", "Interstellar", 2014),
    ("[18+] Diet of Sex 2014 DVDRip.mkv", "[18+] Diet of Sex", 2014),
]
PHASE18_DETERMINISTIC_CASES = [
    (
        "Hybrid (2007) 720p WEB-DL x264 Eng Subs [Dual Audio] [Hindi DDP 2.0 - English DDP 5.1] Exclusive By -=!Dr.STAR!=-",
        "Hybrid",
        2007,
    ),
    (
        "Aurore (2005) DVDRip x264 [French-AC3-5.1/Stereo] [English/French Subs] [Frankvjecy]",
        "Aurore",
        2005,
    ),
    (
        "The.Brothers.Karamazov.1958.(Yul Brynner-Maria Schell).720p.x264-Classics",
        "The Brothers Karamazov",
        1958,
    ),
    ("Il Padrone Sono Me 1955 ITA TVRip XviD", "Il Padrone Sono Me", 1955),
    ("I 600 Giorni Di Salò 1991 ITA SUB ITA DVD9", "I 600 Giorni Di Salò", 1991),
    ("Luciferina (2018) [1080p] [BluRay] [5.1] [YTS.MX]", "Luciferina", 2018),
    (
        "Dr. Dolittle 3 2006-ENG-SD-WEBRip-334MiB-AAC-x264 [PortalGoods]",
        "Dr Dolittle 3",
        2006,
    ),
    ("Catch.Me.If.You.Can[2002]1080p.BRrip-aЯRo", "Catch Me If You Can", 2002),
    (
        "Chinese Zodiac 2012 Upscaled BluRay 2160p HDR10 HEVC DTS-HD MA 5.1 x265-E",
        "Chinese Zodiac",
        2012,
    ),
    (
        "No.Country.for.Old.Men.2007.Criterion.Collection.1080p.Bluray.DDP5.1.HEVC.x265-BluBirD.mkv",
        "No Country for Old Men",
        2007,
    ),
    ("Moana 2 (2024) [1080p] [WEBRip] [5.1]", "Moana 2", 2024),
]
PHASE19_DETERMINISTIC_CASES = [
    ("Project Hail Mary (2026) (1080p DS4K Web-DL x265 10bit HDR E-AC-3 5.1) [Kris]", "Project Hail Mary", 2026),
    ("Crime 101 (2026) (1080p DS4K Web-DL x265 10bit HDR E-AC-3 5.1) [Kris]", "Crime 101", 2026),
    ("The Notebook (2004) (1080p BluRay x265 8bit AAC 5.1) [Kris]", "The Notebook", 2004),
    ("The Grand Budapest Hotel (2014) (2160p x265 10bit HDR UHD BD DTS-HD MA 5.1) [Prof]", "The Grand Budapest Hotel", 2014),
    ("No Country for Old Men (2007) (2160p x265 10bit HDR UHD BD DTS-HD MA 5.1) [Prof]", "No Country for Old Men", 2007),
    ("Ocean's Thirteen (2007) (2160p BluRay x265 10bit HDR DTS-HD MA 5.1 English + Czech + French + German + Italian + Japanese + Spanish r00t) [QxR]", "Ocean's Thirteen", 2007),
    ("Luciferina (2018) [1080p] [BluRay] [5.1] [YTS.MX]", "Luciferina", 2018),
    ("Our Friend, Martin (1999) [DVDRip 480p] [10 bit x265 HEVC] [AC-3] [SBinK]", "Our Friend, Martin", 1999),
    ("Sleepy Hollow (1999) [BluRayRip 2160p] [10 bit x265 HEVC HDR10] [DTS-HD 5.1] [AC-3] [SBinK]", "Sleepy Hollow", 1999),
    ("Bleach the Movie Hell Verse (2010) [tmdbid-73245] - [Remux-1080p][DTS-HD MA 5.1][EN+JA][h264]-psychic", "Bleach the Movie Hell Verse", 2010),
    ("Death By Hanging 1968 JPN SUB ENG, ITA 1080p BluRay x264", "Death By Hanging", 1968),
    ("The Man With The Suitcase 1984 FRE SUB ENG, ITA 1080p BluRay x264", "The Man With The Suitcase", 1984),
    ("Apocalypse In The Tropics 2024 PT-BR MULTISUB 1080p WEB-DL x264", "Apocalypse In The Tropics", 2024),
    ("The.Roundup.2022.iTA-KOR.Bluray.1080p.x264-CYBER.mkv", "The Roundup", 2022),
    ("Sex.2024.iTA-NOR.Bluray.1080p.x264-CYBER.mkv", "Sex", 2024),
    ("Love.2024.iTA-NOR.Bluray.1080p.x264-CYBER.mkv", "Love", 2024),
    ("Othello 67 1967 No Language 1080p WEB-DL x264", "Othello 67", 1967),
    ("Dyketactics 1974 No Language 1080p WEB-DL x264", "Dyketactics", 1974),
    ("Annie (1999) DVDRIP", "Annie", 1999),
    ("The World Is Not Enough (1999) DVDRip - NonyMovies", "The World Is Not Enough", 1999),
    ("Mission: Impossible II (2000) DVDRip - NonyMovies", "Mission: Impossible II", 2000),
    ("American Psycho (2000) DVDRip - NonyMovies", "American Psycho", 2000),
    ("Il testimone (2001) DVDRip SD x264 AAC ITA - Bifra", "Il testimone", 2001),
    ("Chiedimi quello che vuoi (2024) DVDRip Mkv H264 AC3 iTA 5.1 No Sub - CoSmo Crew", "Chiedimi quello che vuoi", 2024),
    ("The Animal (2001) DVDRip SD H264 ITA ENG SPA Ac3 5.1 sub Ita Eng Spa [ArMor] iDN_CreW", "The Animal", 2001),
    ("Harry.Potter.and.the.Half-Blood.Prince.2009.Open.Matte.1080p.WEBRip.x265-KONTRAST", "Harry Potter and the Half-Blood Prince", 2009),
    ("No.Country.for.Old.Men.2007.Criterion.Collection.1080p.Bluray.DDP5.1.HEVC.x265-BluBirD.mkv", "No Country for Old Men", 2007),
    ("The.Fall.2006.Restored.UHD.BluRay.1080p.DDP.5.1.DoVi.HDR10.x265-SM737", "The Fall", 2006),
    ("Casino Royale 2006 Uncut UHD BluRay 2160p DTS-HD MA 5 1 DV HEVC REMUX-FraMeSToR", "Casino Royale", 2006),
    ("Dune - Part Two (2024) 1080p 10bit Bluray x265 HEVC [Org DD 5.1 Hindi + DD 5.1 English] MSubs ~ TombDoc", "Dune - Part Two", 2024),
    ("Dune - Part One (2021) 1080p 10bit Bluray x265 HEVC [Org DD 5.1 Hindi + DD 5.1 English] MSubs ~ TombDoc", "Dune - Part One", 2021),
    ("The Texas Chainsaw Massacre - The Beginning 2006 1080p Blu-Ray HEVC x265 10Bit DDP5.1 Subs KINGDOM", "The Texas Chainsaw Massacre - The Beginning", 2006),
    ("Aliens - The Big Think (2016) 720p x265", "Aliens - The Big Think", 2016),
    ("F9 - The Fast Saga (2021) Director's Cut 1080p 10bit Bluray x265 HEVC [Org DDP 5.1 Hindi + DDP 5.1 Atmos English] ESubs ~ TombDoc", "F9 - The Fast Saga", 2021),
    ("F1 - The Movie (2025) EUR 1080p 10bit Bluray x265 HEVC [Org DDP 5.1 Atmos Hindi + DDP 7.1 Atmos English] MSubs ~ TombDoc", "F1 - The Movie", 2025),
    ("Avatar - The Way of Water (2022) IMAX 1080p 10bit Bluray x265 HEVC [Org DDP 5.1 Hindi + DDP 7.1 Atmos English] MSubs ~ TombDoc", "Avatar - The Way of Water", 2022),
    ("Space Oddity - Sci-Fi Rom-Com 2022 Eng Rus Multi Subs 720p [HEVC-mp4]", "Space Oddity", 2022),
    ("Dead Heat Remastered - Horror 1988 Eng Rus Comm Multi Subs 720p [HEVC-mp4]", "Dead Heat", 1988),
    ("The.Beast.of.the.City.1932.(Walter Huston - Film Noir).1080p.BRRip.x264-Classics", "The Beast of the City", 1932),
    ("El.Condor.1970.(Lee Van Cleef - Jim Brown - Western).720p.x264-Classics", "El Condor", 1970),
    ("Sunshine.1973.(Joseph Sargent - Drama).720p.x264-Classics", "Sunshine", 1973),
    ("The.Undying.Monster.1942.(Horror - Mystery).720p.BRRip.x264-Classics", "The Undying Monster", 1942),
    ("Dogma - Fantasy 1999 Eng Rus Multi Subs 720p [H264-mp4]", "Dogma", 1999),
    ("Kull The Conqueror - Fantasy 1997 Eng Rus Multi Subs 720p [H264-mp4]", "Kull The Conqueror", 1997),
    ("The Final Cut (2004) WEBRip 1080p HEVC AAC ITA ENG - Lullozzo", "The Final Cut", 2004),
    ("A.Final.Cut.For.Orson.40.Years.in.The.Making.2018.1080p.NF.WEBRip.DD5.1.x264-NTG", "A Final Cut For Orson 40 Years in The Making", 2018),
    ("LEGO DC Comics Super Heroes - Justice League - Cosmic Clash (2016).1080p.H265.EAC3.6CH-MNKYDDL", "LEGO DC Comics Super Heroes - Justice League - Cosmic Clash", 2016),
    ("DC.League.of.Super-Pets.2022.1080p.BluRay.x264-iFT_EniaHD", "DC League of Super-Pets", 2022),
    ("V/H/S (2012) (1080p BluRay x265 10bit EAC3 5.1 Ghost) [QxR]", "V/H/S", 2012),
    ("V/H/S: Viral (2014) (1080p BluRay x265 10bit EAC3 5.1 Ghost) [QxR]", "V/H/S: Viral", 2014),
    ("Pirates Of The Caribbean 3 At World's End 2007 [EN/FR/ES] Bluray 1080p AV1 OPUS 5.1-UH", "Pirates Of The Caribbean 3 At World's End", 2007),
    ("Batman Begins 2005 Bluray IMAX 2160p AV1 HDR10 EN/FR/ES/DE OPUS 5.1-UH", "Batman Begins", 2005),
    ("Lethal Weapon 4 1998 Bluray 1080p AV1 EN/FR/DE/ITA/ES OPUS 5.1-UH", "Lethal Weapon 4", 1998),
    ("My.Show.S01E01.1080p.WEB-DL.x264-GROUP.mkv", "My Show S01E01", None),
    ("Anime.Title.EP01.1080p.WEB-DL.AAC2.0.x264-GROUP.mkv", "Anime Title EP01", None),
    ("Anime.Title.OVA.01.1080p.BluRay.x265-GROUP.mkv", "Anime Title OVA 01", None),
]
PHASE20_DETERMINISTIC_CASES = [
    ("Minority Report (Spielberg, 2002).mkv", "Minority Report", 2002),
    ("American Psycho (Harron, 2000).mkv", "American Psycho", 2000),
    ("Danny the Dog (Leterrier, 2005)", "Danny the Dog", 2005),
    ("Herbie il Super Maggiolino (2005, Robinson) [BDMux1080p Ita-Eng]", "Herbie il Super Maggiolino", 2005),
    (
        "The Big Lebowski (1998) + EXTRAS (1080p BluRay x265 10bit HDR ITA ENG MULTISUB) - [GEGE] [6.65GB]",
        "The Big Lebowski",
        1998,
    ),
    (
        "Inside Man (2006) + Extras (1080p BluRay x265 10bit ITA ENG SUB ITA ENG) - GEGE [7.7gb]",
        "Inside Man",
        2006,
    ),
    ("Ocean's Thirteen-2007-BdRip-(1080p)-Italian AC3-English AAC-x264", "Ocean's Thirteen", 2007),
    ("Harold-And-Kumar-Go-To-White-Castle-2004-1080p-Blu-Ray-HEVC-x265-10-Bit-DDP5-1-Subs-KINGDOM", "Harold-And-Kumar-Go-To-White-Castle", 2004),
]
PHASE21_DETERMINISTIC_CASES = [
    ("Blade Runner 2049 (2017) AV1-10bit 1080p 7RIP", "Blade Runner 2049", 2017),
    ("Wonder Woman 1984 (2020) 1080p BluRay x264", "Wonder Woman 1984", 2020),
    ("Argentina 1985 (2022) 1080p WEB-DL x264", "Argentina 1985", 2022),
    ("Death Race 2000 (1975) 1080p BluRay", "Death Race 2000", 1975),
    (
        "The African Queen (1951)-Humphrey Bogart & Katharine Hepburn-1080p-H264-AC 3 (DolbyDigital-5.1) ? nickarad",
        "The African Queen",
        1951,
    ),
    ("The Great Escape (1963) 1080p-H264-AAC", "The Great Escape", 1963),
    ("Lady and the Tramp (1955) Cartoon movie-1080p-H264-AC 3", "Lady and the Tramp", 1955),
    ("Blonde Death [1984 - USA] no budget cult classic", "Blonde Death", 1984),
    ("Message from Space [1978 - Japan] (English Version) sci fi", "Message from Space", 1978),
    ("Annie - The Virgin of Saint Tropez [1975 - France] (ENG) erotic drama", "Annie - The Virgin of Saint Tropez", 1975),
    ("Black Tea [2024 - France + Taiwan] (DUAL Zho Fra) drama", "Black Tea", 2024),
    ("Armadillo *2010* [BDRip.XviD-miguel] [ENG]", "Armadillo", 2010),
    ("Trust.1990.(1001.Movies.You.Must.See).1080p.BRRip.x264-Classics", "Trust", 1990),
    ("The Sound of Music 1965 45th Anniv (1080p Bluray)", "The Sound of Music", 1965),
    ("Bobby (2006) Language:English-Russian, Subs:Spanish-Russian-English", "Bobby", 2006),
    ("La viaccia - Le mauvais chemin (1961) lang: IT+SP with subs: FR+EN", "La viaccia - Le mauvais chemin", 1961),
    ("The Hollywood Ten (John Berry, 1950)_Sub.srt.PTBR", "The Hollywood Ten", 1950),
    ("Righteous.Kill[2008]BRrip-aЯRo", "Righteous Kill", 2008),
    ("Persepolis (2007) [HDRip-AC3][Spanish]", "Persepolis", 2007),
    ("Help! (1965)Mp-4-Blu-Ray Rip-1080p-AAC-DSD", "Help!", 1965),
    ("[Blu-ray] Borsalino (1970) [Jacques Deray, Belmondo, Alain Delon]", "Borsalino", 1970),
    ("[TVRip low quality] Madly / The Love Mates (1970) - Roger Kahane", "Madly / The Love Mates", 1970),
    ("[FOUND] Terrore.Sul.Treno-Terror.On.A.Train.(1953).ITA-ENG", "Terrore Sul Treno - Terror On A Train", 1953),
    ("Humphrey Bogart- The Caine Mutiny (1954) 1080p-H264", "The Caine Mutiny", 1954),
    ("Kirk Douglas - 20000 League Under Sea [1954] 1080p-H264", "20000 League Under Sea", 1954),
    ("Walt Disney - Corn Chips (1951) 1080p-H264", "Corn Chips", 1951),
    ("JAMES BOND-From Russia With Love (1963) 1080p-H264", "From Russia With Love", 1963),
    ("Mr. Ove - En Man Som Heter Ove (2015) 1080p H265", "Mr Ove - En Man Som Heter Ove", 2015),
    ("Dirty Dancing 2 - Havana Nights (2004) WEBDL 1080p", "Dirty Dancing 2 - Havana Nights", 2004),
    ("Psycho - Psyco.1960.iTA.ENG", "Psycho - Psyco", 1960),
    ("Indovina chi viene a cena-Guess who.s coming to dinner (1967)", "Indovina chi viene a cena - Guess who's coming to dinner", 1967),
    ("Cenerentola (Cinderella - 1950)[1080p]", "Cenerentola (Cinderella)", 1950),
    ("Safe.-.2012.-.Blu-ray.-.1080p.-.x264", "Safe", 2012),
    ("The Matrix (1999) DVDRip - NonyMovies", "The Matrix", 1999),
    ("Harry Potter 2009 Open Matte 1080p WEBRip x265", "Harry Potter", 2009),
    ("[REC].2007.1080p.BluRay.x264.mkv", "[REC]", 2007),
    ("[18+] Diet of Sex 2014 DVDRip", "[18+] Diet of Sex", 2014),
]


@pytest.mark.parametrize("case", FIXTURE_CASES, ids=[case["name"] for case in FIXTURE_CASES])
def test_parse_media_title_regressions(case) -> None:
    parsed = parse_media_title(
        title=case["title"],
        original_filename=case["original_filename"],
        year=case["year"],
    )

    assert parsed["display_title"] == case["expected_display_title"]
    assert parsed["base_title"] == case.get(
        "expected_base_title",
        case.get("expected_poster_match_title", case["expected_display_title"]),
    )
    assert parsed["edition_identity"] == case["expected_edition_identity"]
    assert parsed["parsed_year"] == case["expected_parsed_year"]
    assert parsed["poster_match_title"] == case.get("expected_poster_match_title", case["expected_display_title"])
    assert parsed["poster_match_year"] == case.get("expected_poster_match_year", case["expected_parsed_year"])
    assert parsed["poster_match_identity"]["title"] == case.get("expected_poster_match_title", case["expected_display_title"])
    assert parsed["poster_match_identity"]["year"] == case.get("expected_poster_match_year", case["expected_parsed_year"])
    assert parsed["poster_match_source"] in {"title", "original_filename", "stored_title", "fallback", None}
    assert parsed["poster_match_identity"]["source"] in {"title", "original_filename", "stored_title", "fallback", None}
    assert parsed["title_source"] in {"title", "original_filename", "stored_title", "fallback"}
    assert parsed["parse_confidence"] in {"high", "medium", "low"}
    assert isinstance(parsed["warnings"], list)
    assert parsed["parser_version"] == TITLE_PARSER_VERSION
    assert parsed["suspicious_output"] is False
    for marker in case.get("expected_warning_markers", []):
        assert marker in parsed["warnings"]
    if case["expected_parsed_year"] is not None:
        assert str(case["expected_parsed_year"]) not in parsed["display_title"]
    for phrase in EDITION_PHRASE_MAP.get(case["expected_edition_identity"], []):
        assert phrase not in parsed["display_title"].lower()
    assert "1080p" not in parsed["display_title"].lower()
    assert "bluray" not in parsed["display_title"].lower()
    assert "tmdbid" not in parsed["display_title"].lower()
    assert "imdb" not in parsed["display_title"].lower()


def test_dirty_stored_title_does_not_beat_filename_source() -> None:
    parsed = parse_media_title(
        title="One Piece Film Strong World 1080p BluRay DDP 5 1 10bit H 265-iVy",
        original_filename="One Piece Film Strong World 1080p BluRay DDP 5 1 10bit H 265-iVy.mkv",
        year=None,
    )

    assert parsed["display_title"] == "One Piece Film Strong World"
    assert parsed["title_source"] == "original_filename"
    assert parsed["suspicious_output"] is False


@pytest.mark.parametrize(
    ("original_filename", "expected_title", "expected_year"),
    PHASE15_DETERMINISTIC_CASES,
)
def test_phase15_deterministic_scrubbing_examples(
    original_filename: str,
    expected_title: str,
    expected_year: int | None,
) -> None:
    parsed = parse_media_title(title=None, original_filename=original_filename, year=None)

    assert parsed["display_title"] == expected_title
    assert parsed["base_title"] == expected_title
    assert parsed["poster_match_title"] == expected_title
    assert parsed["parsed_year"] == expected_year
    assert parsed["poster_match_year"] == expected_year
    assert parsed["suspicious_output"] is False


@pytest.mark.parametrize(
    ("original_filename", "expected_title", "expected_year"),
    PHASE17_DETERMINISTIC_CASES,
)
def test_phase17_true_failure_classifier_examples(
    original_filename: str,
    expected_title: str,
    expected_year: int | None,
) -> None:
    parsed = parse_media_title(title=None, original_filename=original_filename, year=None)

    assert parsed["display_title"] == expected_title
    assert parsed["base_title"] == expected_title
    assert parsed["poster_match_title"] == expected_title
    assert parsed["parsed_year"] == expected_year
    assert parsed["poster_match_year"] == expected_year
    assert parsed["suspicious_output"] is False


@pytest.mark.parametrize(
    ("original_filename", "expected_title", "expected_year"),
    PHASE18_DETERMINISTIC_CASES,
)
def test_phase18_bracket_spans_and_release_year_grammar(
    original_filename: str,
    expected_title: str,
    expected_year: int | None,
) -> None:
    parsed = parse_media_title(title=None, original_filename=original_filename, year=None)

    assert parsed["display_title"] == expected_title
    assert parsed["base_title"] == expected_title
    assert parsed["poster_match_title"] == expected_title
    assert parsed["parsed_year"] == expected_year
    assert parsed["poster_match_year"] == expected_year
    assert parsed["suspicious_output"] is False


@pytest.mark.parametrize(
    ("original_filename", "expected_title", "expected_year"),
    PHASE19_DETERMINISTIC_CASES,
)
def test_phase19_remaining_true_failure_examples(
    original_filename: str,
    expected_title: str,
    expected_year: int | None,
) -> None:
    parsed = parse_media_title(title=None, original_filename=original_filename, year=None)

    assert parsed["display_title"] == expected_title
    assert parsed["base_title"] == expected_title
    assert parsed["poster_match_title"] == expected_title
    assert parsed["parsed_year"] == expected_year
    assert parsed["poster_match_year"] == expected_year
    assert parsed["suspicious_output"] is False


@pytest.mark.parametrize(
    ("original_filename", "expected_title", "expected_year"),
    PHASE20_DETERMINISTIC_CASES,
)
def test_phase20_safe_true_failure_examples(
    original_filename: str,
    expected_title: str,
    expected_year: int | None,
) -> None:
    parsed = parse_media_title(title=None, original_filename=original_filename, year=None)

    assert parsed["display_title"] == expected_title
    assert parsed["base_title"] == expected_title
    assert parsed["poster_match_title"] == expected_title
    assert parsed["parsed_year"] == expected_year
    assert parsed["poster_match_year"] == expected_year
    assert parsed["suspicious_output"] is False


@pytest.mark.parametrize(
    ("original_filename", "expected_title", "expected_year"),
    PHASE21_DETERMINISTIC_CASES,
)
def test_phase21_title_number_and_post_year_suffix_grammar(
    original_filename: str,
    expected_title: str,
    expected_year: int | None,
) -> None:
    parsed = parse_media_title(title=None, original_filename=original_filename, year=None)

    assert parsed["display_title"] == expected_title
    assert parsed["base_title"] == expected_title
    assert parsed["poster_match_title"] == expected_title
    assert parsed["parsed_year"] == expected_year
    assert parsed["poster_match_year"] == expected_year
    assert parsed["suspicious_output"] is False


def test_title_scrubber_v1_protected_regression_set() -> None:
    cases = [
        ("Never Ending Story (1984) 1080p BluRay", "Never Ending Story", 1984),
        ("John Wick Chapter 2 (2017) 1080p BluRay", "John Wick Chapter 2", 2017),
        ("Big Hero 6 (2014) 1080p BluRay", "Big Hero 6", 2014),
        ("Inside Out 2 (2024) 1080p WEB-DL", "Inside Out 2", 2024),
        ("The BFG (2016) 1080p BluRay", "The BFG", 2016),
        ("Kingdom of Heaven DC Roadshow Version 2005 2160p UHD", "Kingdom of Heaven", 2005),
        ("Legend 1985 DC 1080p BluRay", "Legend", 1985),
        (
            "LEGO DC Comics Super Heroes - Justice League - Cosmic Clash (2016).1080p.H265",
            "LEGO DC Comics Super Heroes - Justice League - Cosmic Clash",
            2016,
        ),
        ("DC.League.of.Super-Pets.2022.1080p.BluRay.x264", "DC League of Super-Pets", 2022),
        ("The Final Cut (2004) WEBRip 1080p HEVC AAC", "The Final Cut", 2004),
        (
            "A.Final.Cut.For.Orson.40.Years.in.The.Making.2018.1080p.NF.WEBRip",
            "A Final Cut For Orson 40 Years in The Making",
            2018,
        ),
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
        ("Blade Runner 2049 (2017) AV1-10bit 1080p 7RIP", "Blade Runner 2049", 2017),
        ("Wonder Woman 1984 (2020) 1080p BluRay x264", "Wonder Woman 1984", 2020),
        ("Argentina 1985 (2022) 1080p WEB-DL x264", "Argentina 1985", 2022),
        ("Death Race 2000 (1975) 1080p BluRay", "Death Race 2000", 1975),
        ("The Italian Job 1969 1080p BluRay", "The Italian Job", 1969),
        ("The French Connection 1971 1080p BluRay", "The French Connection", 1971),
        (
            "The African Queen (1951)-Humphrey Bogart & Katharine Hepburn-1080p-H264-AC 3 (DolbyDigital-5.1) ? nickarad",
            "The African Queen",
            1951,
        ),
        ("Blonde Death [1984 - USA] no budget cult classic", "Blonde Death", 1984),
        ("Armadillo *2010* [BDRip.XviD-miguel] [ENG]", "Armadillo", 2010),
        ("Trust.1990.(1001.Movies.You.Must.See).1080p.BRRip.x264-Classics", "Trust", 1990),
        ("Righteous.Kill[2008]BRrip-aЯRo", "Righteous Kill", 2008),
        ("Bobby (2006) Language:English-Russian, Subs:Spanish-Russian-English", "Bobby", 2006),
    ]

    for original_filename, expected_title, expected_year in cases:
        parsed = parse_media_title(title=None, original_filename=original_filename, year=None)
        assert parsed["display_title"] == expected_title
        assert parsed["parsed_year"] == expected_year
        assert parsed["parser_version"] == "title-scrubber-v1.0.0"
        assert parsed["suspicious_output"] is False


@pytest.mark.parametrize(
    ("original_filename", "expected_title", "expected_year"),
    [
        ("[REC].2007.1080p.BluRay.x264.mkv", "[REC]", 2007),
        ("Show.Name.S01E01.1080p.WEB-DL.x264-GROUP.mkv", "Show Name S01E01", None),
        ("Show.Name.S1E1.720p.HDTV.x264-GROUP.mkv", "Show Name S1E1", None),
        ("Show.Name.1x02.1080p.WEB-DL.x265-GROUP.mkv", "Show Name 1x02", None),
        ("Anime.Name.E01.1080p.WEB-DL.x264-GROUP.mkv", "Anime Name E01", None),
        ("Anime.Name.EP01.1080p.WEB-DL.x264-GROUP.mkv", "Anime Name EP01", None),
        ("Anime.Name.OVA.01.1080p.BluRay.x265-GROUP.mkv", "Anime Name OVA 01", None),
        ("His and Hers 2026 S01E03 XviD-AFG", "His and Hers 2026 S01E03", None),
        ("Road Wars 2022 S02E08 XviD-AFG", "Road Wars 2022 S02E08", None),
    ],
)
def test_phase15_negative_guards_preserve_titles_and_episode_identity(
    original_filename: str,
    expected_title: str,
    expected_year: int | None,
) -> None:
    parsed = parse_media_title(title=None, original_filename=original_filename, year=None)

    assert parsed["display_title"] == expected_title
    assert parsed["parsed_year"] == expected_year
    assert parsed["suspicious_output"] is False


def test_phase20_hyphenated_date_range_is_not_treated_as_release_year() -> None:
    parsed = parse_media_title(
        title=None,
        original_filename="Russia.1985-1999.TraumaZone.S01E07.WEBRip.x264-XEN0N",
        year=None,
    )

    assert parsed["display_title"] == "Russia 1985-1999 TraumaZone S01E07"
    assert parsed["parsed_year"] is None


@pytest.mark.parametrize(
    ("original_filename", "expected_title", "expected_year", "expected_edition_markers"),
    [
        (
            "Kingdom.of.Heaven.DC.Roadshow.Version.2005.2160p.UHD.Blu-ray.Remux.DV.HDR.HEVC.TrueHD.Atmos.7.1-CiNEPHiLES.mkv",
            "Kingdom of Heaven",
            2005,
            {"director's cut", "roadshow"},
        ),
        ("Legend 1985 DC.mkv", "Legend", 1985, {"director's cut"}),
        ("Troy DC 2004 1080p BluRay x264.mkv", "Troy", 2004, {"director's cut"}),
        ("Movie.Name.2000.DC.1080p.BluRay.x264-GROUP.mkv", "Movie Name", 2000, {"director's cut"}),
        (
            "Monster\\'s Ball [Unrated DC].2001.BRRip.XviD.AC3[5.1]-VLiS",
            "Monster's Ball",
            2001,
            {"unrated", "director's cut"},
        ),
        ("Spawn (1997) (DC 1080p BluRay x265).mkv", "Spawn", 1997, {"director's cut"}),
    ],
)
def test_dc_abbreviation_stripped_only_in_directors_cut_context(
    original_filename: str,
    expected_title: str,
    expected_year: int,
    expected_edition_markers: set[str],
) -> None:
    parsed = parse_media_title(title=None, original_filename=original_filename, year=None)

    assert parsed["display_title"] == expected_title
    assert parsed["base_title"] == expected_title
    assert parsed["parsed_year"] == expected_year
    assert expected_edition_markers.issubset(set(str(parsed["edition_identity"]).split("|")))
    assert "DC" not in parsed["display_title"]
    assert parsed["suspicious_output"] is False


@pytest.mark.parametrize(
    ("original_filename", "expected_title", "expected_year"),
    [
        ("LEGO DC - Shazam! Magic and Monsters (2020).1080p.H265.EAC3.6CH-MNKYDDL", "LEGO DC - Shazam! Magic and Monsters", 2020),
        ("LEGO DC Batman - Family Matters (2019).1080p.H265.EAC3.6CH-MNKYDDL", "LEGO DC Batman - Family Matters", 2019),
        (
            "LEGO DC Comics Super Heroes - Justice League - Cosmic Clash (2016).1080p.H265.EAC3.6CH-MNKYDDL",
            "LEGO DC Comics Super Heroes - Justice League - Cosmic Clash",
            2016,
        ),
        ("DC.League.of.Super-Pets.2022.1080p.BluRay.x264-iFT_EniaHD", "DC League of Super-Pets", 2022),
        ("DC.Showcase.Catwoman.2011.1080p.BluRay.x264.mkv", "DC Showcase Catwoman", 2011),
    ],
)
def test_dc_franchise_titles_preserved(
    original_filename: str,
    expected_title: str,
    expected_year: int,
) -> None:
    parsed = parse_media_title(title=None, original_filename=original_filename, year=None)

    assert parsed["display_title"] == expected_title
    assert parsed["parsed_year"] == expected_year
    assert parsed["edition_identity"] == "standard"
    assert parsed["suspicious_output"] is False


@pytest.mark.parametrize(
    ("original_filename", "expected_title", "expected_year"),
    [
        ("V/H/S (2012) (1080p BluRay x265 10bit EAC3 5.1 Ghost) [QxR]", "V/H/S", 2012),
        ("V/H/S: Viral (2014) (1080p BluRay x265 10bit EAC3 5.1 Ghost) [QxR]", "V/H/S: Viral", 2014),
        ("The Hunt/Jagten (2012) (1080p BluRay x265 8-bit AC-3 5.1) [WeSLeY]", "The Hunt/Jagten", 2012),
        ("/mnt/media/Movie.2020.1080p.mkv", "Movie", 2020),
        (r"C:\Media\Movie.2020.1080p.mkv", "Movie", 2020),
    ],
)
def test_slash_titles_not_treated_as_paths(
    original_filename: str,
    expected_title: str,
    expected_year: int,
) -> None:
    parsed = parse_media_title(title=None, original_filename=original_filename, year=None)

    assert parsed["display_title"] == expected_title
    assert parsed["parsed_year"] == expected_year
    assert parsed["suspicious_output"] is False


@pytest.mark.parametrize(
    ("original_filename", "expected_title", "expected_year"),
    [
        (
            "Avatar - The Way of Water (2022) IMAX 1080p 10bit Bluray x265 HEVC [Org DDP 5.1 Hindi + DDP 7.1 Atmos English] MSubs ~ TombDoc",
            "Avatar - The Way of Water",
            2022,
        ),
        ("Dune - Part Two (2024) AV1 1080p 7RIP", "Dune - Part Two", 2024),
        (
            "Venom - The Last Dance (2024) 1080p 10bit Bluray x265 HEVC [Org DD 5.1 Hindi + DD 5.1 English] ESubs ~ TombDoc",
            "Venom - The Last Dance",
            2024,
        ),
        (
            "F1 - The Movie (2025) EUR 1080p 10bit Bluray x265 HEVC [Org DDP 5.1 Atmos Hindi + DDP 7.1 Atmos English] MSubs ~ TombDoc",
            "F1 - The Movie",
            2025,
        ),
        (
            "LEGO DC Comics Super Heroes - Justice League - Cosmic Clash (2016).1080p.H265.EAC3.6CH-MNKYDDL",
            "LEGO DC Comics Super Heroes - Justice League - Cosmic Clash",
            2016,
        ),
        ("The Texas Chainsaw Massacre - The Beginning (2006).1080p.BluRay.x264.mkv", "The Texas Chainsaw Massacre - The Beginning", 2006),
        ("Aliens - The Big Think (2016).1080p.WEB-DL.x264.mkv", "Aliens - The Big Think", 2016),
    ],
)
def test_dash_subtitle_preserved_before_metadata(
    original_filename: str,
    expected_title: str,
    expected_year: int,
) -> None:
    parsed = parse_media_title(title=None, original_filename=original_filename, year=None)

    assert parsed["display_title"] == expected_title
    assert parsed["parsed_year"] == expected_year
    assert parsed["suspicious_output"] is False


@pytest.mark.parametrize(
    ("original_filename", "expected_title"),
    [
        ("My.Show.S01E01.1080p.WEB-DL.x264-GROUP.mkv", "My Show S01E01"),
        ("My.Show.S1E1.720p.HDTV.x264-GROUP.mkv", "My Show S1E1"),
        ("My.Show.1x02.1080p.WEB-DL.x265-GROUP.mkv", "My Show 1x02"),
        ("Anime.Title.E01.1080p.WEB-DL.AAC2.0.x264-GROUP.mkv", "Anime Title E01"),
        ("Anime.Title.EP01.1080p.WEB-DL.AAC2.0.x264-GROUP.mkv", "Anime Title EP01"),
        ("Anime.Title.Ep.01.1080p.BluRay.x265-GROUP.mkv", "Anime Title Ep 01"),
        ("Anime.Title.Episode.01.1080p.BluRay.x265-GROUP.mkv", "Anime Title Episode 01"),
        ("Anime.Title.OVA.01.1080p.BluRay.x265-GROUP.mkv", "Anime Title OVA 01"),
        ("Anime.Title.OAD.01.1080p.BluRay.x265-GROUP.mkv", "Anime Title OAD 01"),
        ("Anime.Title.Special.01.1080p.BluRay.x265-GROUP.mkv", "Anime Title Special 01"),
    ],
)
def test_tv_anime_episode_tokens_preserved(original_filename: str, expected_title: str) -> None:
    parsed = parse_media_title(title=None, original_filename=original_filename, year=None)

    assert parsed["display_title"] == expected_title
    assert parsed["parsed_year"] is None
    assert parsed["suspicious_output"] is False


@pytest.mark.parametrize(
    ("original_filename", "expected_title", "expected_year", "expected_edition"),
    [
        ("The Final Cut (2004) WEBRip 1080p HEVC AAC ITA ENG - Lullozzo", "The Final Cut", 2004, "standard"),
        ("Final Cut (1998).1080p.WEB-DL.x264.mkv", "Final Cut", 1998, "standard"),
        ("Director's Cut (2000).1080p.WEB-DL.x264.mkv", "Director's Cut", 2000, "standard"),
        ("Troy Director's Cut (2004).1080p.BluRay.x264.mkv", "Troy", 2004, "director's cut"),
        ("Saw Director's Cut 2004 1080p BluRay x264.mkv", "Saw", 2004, "director's cut"),
        (
            "Kingdom.of.Heaven.DC.Roadshow.Version.2005.2160p.UHD.Blu-ray.Remux.DV.HDR.HEVC.TrueHD.Atmos.7.1-CiNEPHiLES.mkv",
            "Kingdom of Heaven",
            2005,
            "roadshow|director's cut",
        ),
    ],
)
def test_the_final_cut_not_collapsed_to_article(
    original_filename: str,
    expected_title: str,
    expected_year: int,
    expected_edition: str,
) -> None:
    parsed = parse_media_title(title=None, original_filename=original_filename, year=None)

    assert parsed["display_title"] == expected_title
    assert parsed["parsed_year"] == expected_year
    assert parsed["edition_identity"] == expected_edition
    assert parsed["display_title"] not in {"The", "A", "An"}
    assert parsed["suspicious_output"] is False


@pytest.mark.parametrize(
    ("original_filename", "expected_title", "expected_year"),
    [
        ("A Beautiful Mind (2001) (2160p x265 10bit HDR UHD BD Atmos) [Prof]", "A Beautiful Mind", 2001),
        ("The Nice Guys (2016) (2160p x265 10bit HDR UHD BD Atmos) [Prof]", "The Nice Guys", 2016),
        ("Spirited Away (2001) (1080p BluRay x265 10-bit Eng 5.1 + Jap 5.1 AAC) [WeSLeY]", "Spirited Away", 2001),
        ("Ponyo (2008) (1080p BluRay x265 10-bit Eng 5.1 + Jap 5.1 AAC) [WeSLeY]", "Ponyo", 2008),
        ("Kill Bill Vol. 2 (2004) (2160p x265 10bit HDR UHD BD DTS-HD MA 5.1) [Prof]", "Kill Bill Vol 2", 2004),
        ("[moon] Ted 2 2015 WEBRip x264 AAC.mkv", "Ted 2", 2015),
        ("[moon] Project X 2012 WEBRip x264 AAC.mkv", "Project X", 2012),
        ("[REC].2007.1080p.BluRay.x264.mkv", "[REC]", 2007),
        ("[18+] Monamour 2006 DVDRip.mkv", "[18+] Monamour", 2006),
    ],
)
def test_bracket_release_groups_removed_only_in_suffix_context(
    original_filename: str,
    expected_title: str,
    expected_year: int,
) -> None:
    parsed = parse_media_title(title=None, original_filename=original_filename, year=None)

    assert parsed["display_title"] == expected_title
    assert parsed["parsed_year"] == expected_year
    assert parsed["suspicious_output"] is False


def test_trusted_clean_title_beats_dirty_filename_when_available() -> None:
    parsed = parse_media_title(
        title="Ocean's Eleven",
        original_filename="Oceans.Eleven.2001.1080p.BluRay.Remux.mkv",
        year=2001,
    )

    assert parsed["display_title"] == "Ocean's Eleven"
    assert parsed["title_source"] == "title"
    assert parsed["poster_match_title"] == "Ocean's Eleven"
    assert parsed["poster_match_year"] == 2001
    assert parsed["poster_match_source"] == "title"
    assert parsed["poster_match_identity"] == {
        "title": "Ocean's Eleven",
        "year": 2001,
        "source": "title",
    }


@pytest.mark.parametrize(
    ("stored_title", "original_filename", "year", "expected_title", "expected_year"),
    [
        (
            "Harry Potter and the Deathly Hallows Part",
            "Harry.Potter.and.the.Deathly.Hallows.Part.1.2010.4K.UHD.2160p.REMUX.DV.DTS-HD.MA.7.1.Dual.PTBR-BrRemux.mkv",
            2010,
            "Harry Potter and the Deathly Hallows Part 1",
            2010,
        ),
        (
            "Harry Potter and the Deathly Hallows Part",
            "Harry.Potter.and.the.Deathly.Hallows.Part.2.2011.4K.UHD.2160p.REMUX.DV.DTS-HD.MA.7.1.Dual.PTBR-BrRemux.mkv",
            2011,
            "Harry Potter and the Deathly Hallows Part 2",
            2011,
        ),
        (
            "The Menu  iTA-ENG WEBDL 2160p HEVC HDR x265-CYBER",
            "The.Menu.2022.iTA-ENG.WEBDL.2160p.HEVC.HDR.x265-CYBER.mkv",
            2022,
            "The Menu",
            2022,
        ),
        (
            "The Never Ending Story ITA-ENG",
            "The Never Ending Story (1984) ITA-ENG Ac3 5.1 BDRip 1080p H264 sub ita eng [ArMor].mkv",
            1984,
            "The Never Ending Story",
            1984,
        ),
    ],
)
def test_live_row_like_dirty_titles_do_not_beat_cleaner_raw_sources(
    stored_title: str,
    original_filename: str,
    year: int,
    expected_title: str,
    expected_year: int,
) -> None:
    parsed = parse_media_title(
        title=stored_title,
        original_filename=original_filename,
        year=year,
    )
    poster_identity = resolve_poster_match_identity(
        title=stored_title,
        original_filename=original_filename,
        year=year,
    )

    assert parsed["display_title"] == expected_title
    assert parsed["title_source"] == "original_filename"
    assert parsed["poster_match_title"] == expected_title
    assert parsed["poster_match_year"] == expected_year
    assert parsed["poster_match_source"] == "original_filename"
    assert parsed["poster_match_identity"] == {
        "title": expected_title,
        "year": expected_year,
        "source": "original_filename",
    }
    assert poster_identity["title"] == expected_title
    assert poster_identity["year"] == expected_year
    assert poster_identity["source"] == "original_filename"


@pytest.mark.parametrize(
    ("original_filename", "expected_title", "expected_year"),
    [
        (
            "The Never Ending Story (1984) ITA-ENG Ac3 5.1 BDRip 1080p H264 sub ita eng [ArMor].mkv",
            "The Never Ending Story",
            1984,
        ),
        (
            "Nightbitch (2024) [1080p Ita Eng Spa 5.1 HEVC10 SubS] byMe7alh [MIRCrew]",
            "Nightbitch",
            2024,
        ),
        (
            "La.Sposa!2026.iTA-ENG.Bluray.1080p.x264-CYBER.mkv",
            "La Sposa",
            2026,
        ),
        (
            "Le Cose Non Dette (2026) iTA-Bluray.1080p.x264-Dr4gon.mkv",
            "Le Cose Non Dette",
            2026,
        ),
        (
            "L'Amore E Altre Seghe Mentali (2024) iTA-BluRay.1080p.x264-Dr4gon.mkv",
            "L'Amore E Altre Seghe Mentali",
            2024,
        ),
        (
            "Safe.-.2012.-.Blu-ray.-.1080p.-.x264.-.DTS.ITA.AC3.ENG.-.Sub.ITA.-LV89",
            "Safe",
            2012,
        ),
        (
            "Hot Tub Time Machine 2 (2015 ITA/ENG) [1080p x265] [Paso77]",
            "Hot Tub Time Machine 2",
            2015,
        ),
        (
            "Before Sunset (2004 ITA/ENG) [1080p x265] [Paso77]",
            "Before Sunset",
            2004,
        ),
        (
            "Titanic (1997 ITA/ENG) [1080p x265] [Paso77]",
            "Titanic",
            1997,
        ),
        (
            "The Green Mile (1999 ITA/ENG) [1080p x265] [Paso77]",
            "The Green Mile",
            1999,
        ),
    ],
)
def test_diagnostic_metadata_suffix_leaks_are_scrubbed(
    original_filename: str,
    expected_title: str,
    expected_year: int,
) -> None:
    parsed = parse_media_title(title=None, original_filename=original_filename, year=None)

    assert parsed["display_title"] == expected_title
    assert parsed["base_title"] == expected_title
    assert parsed["poster_match_title"] == expected_title
    assert parsed["parsed_year"] == expected_year
    assert parsed["poster_match_year"] == expected_year
    assert parsed["suspicious_output"] is False


@pytest.mark.parametrize(
    ("original_filename", "expected_title", "expected_year"),
    [
        ("Spider-Man.2002.1080p.BluRay.Remux.TrueHD.mkv", "Spider-Man", 2002),
        ("Se7en.1995.1080p.BluRay.mkv", "Se7en", 1995),
        ("3 Idiots 2009 1080p BluRay.mkv", "3 Idiots", 2009),
        ("Project X 2012 1080p WEB-DL.mkv", "Project X", 2012),
        ("Malcolm X 1992 1080p BluRay.mkv", "Malcolm X", 1992),
        ("Movie Name [A True Story] 2020 1080p WEB-DL.mkv", "Movie Name [A True Story]", 2020),
    ],
)
def test_suffix_scrubbing_preserves_meaningful_title_tokens(
    original_filename: str,
    expected_title: str,
    expected_year: int,
) -> None:
    parsed = parse_media_title(title=None, original_filename=original_filename, year=None)

    assert parsed["display_title"] == expected_title
    assert parsed["parsed_year"] == expected_year
    assert parsed["suspicious_output"] is False


@pytest.mark.parametrize(
    ("original_filename", "expected_title", "expected_year"),
    [
        ("John.Wick.Chapter.2.2017.1080p.BluRay.x264.mkv", "John Wick Chapter 2", 2017),
        ("Big.Hero.6.2014.1080p.BluRay.x264.mkv", "Big Hero 6", 2014),
        ("Blade II (2002) (1080p BluRay x265 10bit EAC3 7.1 Celdra) [QxR]", "Blade II", 2002),
        ("Inside.Out.2.2024.1080p.WEB-DL.x264.mkv", "Inside Out 2", 2024),
        ("The.BFG.2016.1080p.BluRay.x264.mkv", "The BFG", 2016),
        (
            "The Never Ending Story (1984) ITA-ENG Ac3 5.1 BDRip 1080p H264 sub ita eng [ArMor].mkv",
            "The Never Ending Story",
            1984,
        ),
        ("Se7en.1995.1080p.BluRay.x264.mkv", "Se7en", 1995),
        ("3.Idiots.2009.1080p.BluRay.x264.mkv", "3 Idiots", 2009),
        ("Malcolm.X.1992.1080p.BluRay.x264.mkv", "Malcolm X", 1992),
        ("Project.X.2012.1080p.BluRay.x264.mkv", "Project X", 2012),
        (
            "Nightbitch (2024) [1080p Ita Eng Spa 5.1 HEVC10 SubS] byMe7alh [MIRCrew]",
            "Nightbitch",
            2024,
        ),
        ("Before Sunset (2004 ITA/ENG) [1080p x265] [Paso77]", "Before Sunset", 2004),
    ],
)
def test_phase_one_overtrim_regressions_preserve_title_tokens(
    original_filename: str,
    expected_title: str,
    expected_year: int,
) -> None:
    parsed = parse_media_title(title=None, original_filename=original_filename, year=None)

    assert parsed["display_title"] == expected_title
    assert parsed["base_title"] == expected_title
    assert parsed["parsed_year"] == expected_year
    assert parsed["poster_match_title"] == expected_title
    assert parsed["poster_match_year"] == expected_year
    assert parsed["suspicious_output"] is False


def test_dirty_never_ending_story_stored_title_uses_safe_filename_parse() -> None:
    parsed = parse_media_title(
        title="The Never Ending Story ITA-ENG",
        original_filename="The Never Ending Story (1984) ITA-ENG Ac3 5.1 BDRip 1080p H264 sub ita eng [ArMor].mkv",
        year=1984,
    )

    assert parsed["display_title"] == "The Never Ending Story"
    assert parsed["title_source"] == "original_filename"
    assert parsed["parsed_year"] == 1984
    assert parsed["suspicious_output"] is False


def test_the_bfg_must_never_collapse_to_article_only() -> None:
    parsed = parse_media_title(
        title=None,
        original_filename="The.BFG.2016.1080p.BluRay.x264.mkv",
        year=None,
    )

    assert parsed["display_title"] == "The BFG"
    assert parsed["display_title"] != "The"
    assert "display_title_implausibly_short" not in parsed["warnings"]


def test_never_ending_story_poster_candidates_include_safe_spacing_variants() -> None:
    parsed = parse_media_title(
        title=None,
        original_filename="The Never Ending Story (1984) ITA-ENG Ac3 5.1 BDRip 1080p H264 sub ita eng [ArMor].mkv",
        year=None,
    )
    family = build_poster_candidate_family(
        title=None,
        original_filename="The Never Ending Story (1984) ITA-ENG Ac3 5.1 BDRip 1080p H264 sub ita eng [ArMor].mkv",
        year=None,
    )

    assert parsed["display_title"] == "The Never Ending Story"
    assert {"The Never Ending Story", "The NeverEnding Story", "Never Ending Story", "NeverEnding Story"}.issubset(
        set(family["titles"])
    )


def test_title_normalization_wrappers_use_backend_parser() -> None:
    raw_value = "One Piece Stampede () [tmdbid-568012] - [Remux-1080p][TrueHD].mkv"

    cleaned_title = clean_title_for_matching(raw_value, None)
    metadata = resolve_title_metadata(
        title=None,
        year=None,
        original_filename=raw_value,
    )
    poster_identity = resolve_poster_match_identity(
        title=None,
        year=None,
        original_filename=raw_value,
    )

    assert cleaned_title == "One Piece Stampede"
    assert metadata["display_title"] == "One Piece Stampede"
    assert metadata["base_title"] == "One Piece Stampede"
    assert metadata["poster_match_title"] == "One Piece Stampede"
    assert metadata["poster_match_year"] is None
    assert metadata["edition_identity"] == "standard"
    assert metadata["title_source"] == "original_filename"
    assert metadata["parsed_year"] is None
    assert poster_identity["title"] == "One Piece Stampede"
    assert poster_identity["year"] is None
    assert poster_identity["source"] == "original_filename"


def test_colon_subtitle_identity_is_preserved_for_display_and_poster_matching() -> None:
    filename = "Pirates of the Caribbean: The Curse of the Black Pearl.2003.1080p.BluRay.x264.mkv"

    parsed = parse_media_title(
        title=None,
        original_filename=filename,
        year=None,
    )
    poster_identity = resolve_poster_match_identity(
        title=None,
        original_filename=filename,
        year=None,
    )

    assert parsed["display_title"] == "Pirates of the Caribbean: The Curse of the Black Pearl"
    assert parsed["poster_match_identity"] == {
        "title": "Pirates of the Caribbean: The Curse of the Black Pearl",
        "year": 2003,
        "source": "original_filename",
    }
    assert poster_identity == {
        "title": "Pirates of the Caribbean: The Curse of the Black Pearl",
        "year": 2003,
        "source": "original_filename",
        "parse_confidence": "high",
        "warnings": [
            "standalone_release_year_cut",
            "technical_suffix_density_cut",
            "metadata_suffix_removed",
        ],
        "parser_version": TITLE_PARSER_VERSION,
        "suspicious_output": False,
    }


def test_cloud_dedupe_identity_keeps_distinct_colon_subtitles_separate() -> None:
    alpha_row = {
        "id": 1,
        "title": "Movie Franchise: Alpha",
        "original_filename": "Movie Franchise: Alpha (2010).mp4",
        "year": 2010,
        "source_kind": "cloud",
        "file_size": 100,
        "width": 1920,
        "height": 1080,
        "audio_codec": None,
        "video_codec": None,
        "container": "mp4",
    }
    beta_row = {
        "id": 2,
        "title": "Movie Franchise: Beta",
        "original_filename": "Movie Franchise: Beta (2010).mp4",
        "year": 2010,
        "source_kind": "cloud",
        "file_size": 100,
        "width": 1920,
        "height": 1080,
        "audio_codec": None,
        "video_codec": None,
        "container": "mp4",
    }

    assert _dedupe_group_key(alpha_row) != _dedupe_group_key(beta_row)


@pytest.mark.parametrize(
    "case_name",
    [
        "blade_runner_2049_number_preserved",
        "title_number_1917_preserved",
        "rocky_ii_roman_numeral",
        "harry_potter_part_one_preserved",
        "harry_potter_part_two_preserved",
        "one_piece_straw_hat_chase_dense_suffix",
    ],
)
def test_meaningful_title_numbers_remain_protected(case_name: str) -> None:
    case = CASES_BY_NAME[case_name]
    parsed = parse_media_title(
        title=case["title"],
        original_filename=case["original_filename"],
        year=case["year"],
    )

    assert parsed["display_title"] == case["expected_display_title"]
    assert "display_title_lost_meaningful_number_token" not in parsed["warnings"]
    assert parsed["suspicious_output"] is False


@pytest.mark.parametrize(
    ("title", "original_filename", "year", "expected_display_title", "expected_poster_title"),
    [
        (
            None,
            "the godfather 1972 4k-kc.mkv",
            1972,
            "The Godfather",
            "the godfather",
        ),
        (
            None,
            "harry potter and the deathly hallows part 1 1080p bluray x264.mkv",
            None,
            "Harry Potter and the Deathly Hallows Part 1",
            "harry potter and the deathly hallows part 1",
        ),
        (
            None,
            "harry potter and the deathly hallows part 2 1080p bluray x264.mkv",
            None,
            "Harry Potter and the Deathly Hallows Part 2",
            "harry potter and the deathly hallows part 2",
        ),
        (
            None,
            "rocky ii.1979.1080p.bluray.x264.mkv",
            None,
            "Rocky II",
            "rocky ii",
        ),
        (
            None,
            "rocky iii.1982.1080p.bluray.x264.mkv",
            None,
            "Rocky III",
            "rocky iii",
        ),
        (
            None,
            "blade runner 2049.2017.2160p.uhd.bluray.remux.mkv",
            None,
            "Blade Runner 2049",
            "blade runner 2049",
        ),
        (
            "ocean's eleven",
            "ocean's eleven [imdb-tt0240772] [2160p uhd bluray remux] [truehd atmos 7.1].mkv",
            None,
            "Ocean's Eleven",
            "ocean's eleven",
        ),
        (
            None,
            "spider-man.2002.1080p.bluray.remux.truehd.mkv",
            None,
            "Spider-Man",
            "spider-man",
        ),
    ],
)
def test_display_title_smart_cases_lowercase_inputs_without_changing_poster_identity(
    title: str | None,
    original_filename: str,
    year: int | None,
    expected_display_title: str,
    expected_poster_title: str,
) -> None:
    parsed = parse_media_title(
        title=title,
        original_filename=original_filename,
        year=year,
    )
    poster_identity = resolve_poster_match_identity(
        title=title,
        original_filename=original_filename,
        year=year,
    )

    assert parsed["display_title"] == expected_display_title
    assert parsed["base_title"] == expected_poster_title
    assert parsed["poster_match_title"] == expected_poster_title
    assert parsed["poster_match_identity"]["title"] == expected_poster_title
    assert poster_identity["title"] == expected_poster_title
    if any(char.isupper() for char in expected_display_title):
        assert parsed["display_title"] != parsed["poster_match_identity"]["title"]


def test_suspicious_output_is_flagged_for_hopeless_metadata_only_input() -> None:
    parsed = parse_media_title(
        title=None,
        original_filename="2160p.BluRay.REMUX.TrueHD.Atmos-FraMeSToR.mkv",
        year=None,
    )

    assert parsed["suspicious_output"] is True
    assert parsed["parser_version"] == TITLE_PARSER_VERSION
    assert any(
        warning.startswith("display_title_contains_") or warning == "display_title_implausibly_short"
        for warning in parsed["warnings"]
    )


def test_title_diagnostics_script_snapshot_output_is_stable(
    initialized_settings,
) -> None:
    now = utcnow_iso()
    with get_connection(initialized_settings) as connection:
        connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                file_size,
                file_mtime,
                duration_seconds,
                width,
                height,
                video_codec,
                audio_codec,
                container,
                year,
                created_at,
                updated_at,
                last_scanned_at
            ) VALUES (?, ?, ?, 'local', ?, ?, NULL, NULL, NULL, NULL, NULL, 'mkv', ?, ?, ?, ?)
            """,
            (
                "Harry Potter and the Deathly Hallows Part",
                "Harry.Potter.and.the.Deathly.Hallows.Part.1.2010.4K.UHD.2160p.REMUX.DV.DTS-HD.MA.7.1.Dual.PTBR-BrRemux.mkv",
                str(initialized_settings.media_root / "Harry.Potter.and.the.Deathly.Hallows.Part.1.2010.4K.UHD.2160p.REMUX.DV.DTS-HD.MA.7.1.Dual.PTBR-BrRemux.mkv"),
                1,
                1.0,
                2010,
                now,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                file_size,
                file_mtime,
                duration_seconds,
                width,
                height,
                video_codec,
                audio_codec,
                container,
                year,
                created_at,
                updated_at,
                last_scanned_at
            ) VALUES (?, ?, ?, 'local', ?, ?, NULL, NULL, NULL, NULL, NULL, 'mkv', ?, ?, ?, ?)
            """,
            (
                "Blade Runner 2049.2017.2160p.UHD.BluRay.REMUX",
                "Blade Runner 2049.2017.2160p.UHD.BluRay.REMUX.mkv",
                str(initialized_settings.media_root / "Blade Runner 2049.2017.2160p.UHD.BluRay.REMUX.mkv"),
                1,
                1.0,
                None,
                now,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                file_size,
                file_mtime,
                duration_seconds,
                width,
                height,
                video_codec,
                audio_codec,
                container,
                year,
                created_at,
                updated_at,
                last_scanned_at
            ) VALUES (?, ?, ?, 'local', ?, ?, NULL, NULL, NULL, NULL, NULL, 'mkv', ?, ?, ?, ?)
            """,
            (
                "One Piece Stampede - [TrueHD 5 1]-psychic",
                "One Piece Stampede - [TrueHD 5 1]-psychic.mkv",
                str(initialized_settings.media_root / "One Piece Stampede - [TrueHD 5 1]-psychic.mkv"),
                1,
                1.0,
                None,
                now,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO media_items (
                title,
                original_filename,
                file_path,
                source_kind,
                file_size,
                file_mtime,
                duration_seconds,
                width,
                height,
                video_codec,
                audio_codec,
                container,
                year,
                created_at,
                updated_at,
                last_scanned_at
            ) VALUES (?, ?, ?, 'local', ?, ?, NULL, NULL, NULL, NULL, NULL, 'mkv', ?, ?, ?, ?)
            """,
            (
                "the godfather  4k-kc",
                "the godfather 1972 4k-kc.mkv",
                str(initialized_settings.media_root / "the godfather 1972 4k-kc.mkv"),
                1,
                1.0,
                1972,
                now,
                now,
                now,
            ),
        )
        connection.commit()

    env = os.environ.copy()
    command = [
        sys.executable,
        "scripts/elvern-title-diagnostics.py",
        "--source-kind",
        "all",
        "--limit",
        "10",
        "--snapshot",
    ]

    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["parser_version"] == TITLE_PARSER_VERSION
    assert payload["filters"] == {
        "source_kind": "all",
        "only_suspicious": False,
        "limit": 10,
    }
    assert payload["summary"]["rows_checked"] >= 2
    assert payload["summary"]["rows_reported"] >= 2
    assert payload["summary"]["suspicious_rows"] <= payload["summary"]["rows_reported"]
    assert isinstance(payload["rows"], list)
    row_ids = [row["id"] for row in payload["rows"]]
    assert row_ids == sorted(row_ids)
    for row in payload["rows"]:
        assert sorted(row.keys()) == [
            "display_title",
            "display_title_changed",
            "id",
            "original_filename",
            "parse_confidence",
            "parser_version",
            "poster_match_identity",
            "source_kind",
            "stored_title",
            "stored_year",
            "suspicious_output",
            "title_source",
            "warnings",
        ]
        assert row["parser_version"] == TITLE_PARSER_VERSION
        assert sorted(row["poster_match_identity"].keys()) == ["title", "year"]

    rows_by_filename = {row["original_filename"]: row for row in payload["rows"]}
    assert rows_by_filename[
        "Harry.Potter.and.the.Deathly.Hallows.Part.1.2010.4K.UHD.2160p.REMUX.DV.DTS-HD.MA.7.1.Dual.PTBR-BrRemux.mkv"
    ]["display_title"] == "Harry Potter and the Deathly Hallows Part 1"
    assert rows_by_filename[
        "Harry.Potter.and.the.Deathly.Hallows.Part.1.2010.4K.UHD.2160p.REMUX.DV.DTS-HD.MA.7.1.Dual.PTBR-BrRemux.mkv"
    ]["poster_match_identity"] == {
        "title": "Harry Potter and the Deathly Hallows Part 1",
        "year": 2010,
    }
    assert rows_by_filename[
        "Blade Runner 2049.2017.2160p.UHD.BluRay.REMUX.mkv"
    ]["display_title"] == "Blade Runner 2049"
    assert rows_by_filename[
        "Blade Runner 2049.2017.2160p.UHD.BluRay.REMUX.mkv"
    ]["poster_match_identity"] == {
        "title": "Blade Runner 2049",
        "year": 2017,
    }
    assert rows_by_filename["One Piece Stampede - [TrueHD 5 1]-psychic.mkv"]["display_title"] == "One Piece Stampede"
    assert "metadata_bracket_suffix_removed" in rows_by_filename[
        "One Piece Stampede - [TrueHD 5 1]-psychic.mkv"
    ]["warnings"]
    assert "dash_release_group_suffix_removed" in rows_by_filename[
        "One Piece Stampede - [TrueHD 5 1]-psychic.mkv"
    ]["warnings"]
    assert rows_by_filename["the godfather 1972 4k-kc.mkv"]["display_title"] == "The Godfather"
    assert rows_by_filename["the godfather 1972 4k-kc.mkv"]["poster_match_identity"] == {
        "title": "the godfather",
        "year": 1972,
    }
    assert "standalone_release_year_cut" in rows_by_filename["the godfather 1972 4k-kc.mkv"]["warnings"]
    assert "technical_suffix_density_cut" in rows_by_filename["the godfather 1972 4k-kc.mkv"]["warnings"]

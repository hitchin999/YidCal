"""
custom_components/yidcal/yidcal_lib/places.py

Curated community-centroid database for snapping HA-configured coordinates to
verified luach-aligned coordinates. When a user's lat/lon is near a community
in this list, that community's centroid is used for all zmanim calculations.

Coordinate source: published Zmanim program place database.
Kiryas Joel was cross-verified against the South Fallsburg 5786 printed luach
(matches all sunset and candle-lighting times to the minute).

Matching strategy:
  1) CUSTOM_BBOX places (currently only Kiryas Joel) match if the user is
     anywhere inside the explicit bounding box. This is for places where a
     wide catch-all area is desired.
  2) Otherwise, the NEAREST place in PLACES within DEFAULT_RADIUS_KM wins.
     Distance-based matching avoids the bbox-overlap problem in dense areas
     like Brooklyn, Gush Dan, and the Catskills, where neighboring towns sit
     within a few km of each other.

HEBREW_NAMES holds the Hebrew/Yiddish form for the subset of places where the
DB provided one. Use get_hebrew_name(name) to retrieve, returning None if no
Hebrew name is on file.

Falls through to None if no place is within range; callers can then use a
reverse-geocoding fallback (Nominatim).
"""
from __future__ import annotations
import math

# Default snap radius. A user within this many km of a place's centroid will
# match that place. Tuned so that close-but-distinct communities (e.g. Monsey
# vs Spring Valley, BB vs RG) each match their own centroid when the user is
# actually in that community, while still tolerating GPS imprecision.
DEFAULT_RADIUS_KM: float = 5.0

# Custom bounding boxes for places that need wider, explicit coverage.
# Key: display name (must match the entry in PLACES exactly).
# Value: (lat_min, lat_max, lon_min, lon_max).
# Inside any custom box, that place is returned immediately (radius is ignored).
CUSTOM_BBOX: dict[str, tuple[float, float, float, float]] = {
    # Kiryas Joel: wide box covers the entire KJ-zone in Orange County, NY,
    # so users configured to surrounding addresses (Forest Glen, Larkin Drive,
    # Watergap, etc.) snap to KJ's luach-aligned centroid.
    "Kiryas Joel": (41.20, 41.45, -74.30, -74.00),
}

# (display_name, state_or_province, latitude, longitude)
# Coordinates are from the verified DB unless explicitly hand-tuned.
# Order within regions is for readability; with distance-based matching it
# only matters as a tiebreaker when two places are exactly equidistant.
PLACES: list[tuple[str, str, float, float]] = [

    # ─── USA — New York / Hudson Valley ───
    ("Kiryas Joel",      "NY",  41.3410,   -74.1679),
    ("Monsey",           "NY",  41.1200,   -74.0700),
    ("New Square",       "NY",  41.1300,   -74.0300),
    ("Spring Valley",    "NY",  41.1200,   -74.0500),
    ("Monroe",           "NY",  41.3292,   -74.1931),
    ("Chester",          "NY",  41.3833,   -74.2667),
    ("Goshen",           "NY",  41.4000,   -74.3333),
    ("Middletown",       "NY",  41.4500,   -74.4200),
    ("Bloomingburg",     "NY",  41.5528,   -74.4403),
    ("Newburgh",         "NY",  41.5000,   -74.0200),
    ("Poughkeepsie",     "NY",  41.7200,   -73.9300),
    ("Kingston",         "NY",  41.9300,   -74.0000),
    ("Hunter",           "NY",  42.2167,   -74.2500),
    ("Tannersville",     "NY",  42.2000,   -74.1500),
    ("Albany",           "NY",  42.6500,   -73.7500),

    # ─── USA — NY / Sullivan County (Catskills bungalow colonies) ───
    ("South Fallsburg",  "NY",  41.7200,   -74.6300),
    ("Woodbourne",       "NY",  41.7600,   -74.5800),
    ("Loch Sheldrake",   "NY",  41.7694,   -74.6569),
    ("Liberty",          "NY",  41.8000,   -74.7500),
    ("Ferndale",         "NY",  41.7736,   -74.7389),
    ("Monticello",       "NY",  41.6500,   -74.7000),
    ("Hurleyville",      "NY",  41.7347,   -74.6750),
    ("Woodridge",        "NY",  41.7200,   -74.5700),
    ("Swan Lake",        "NY",  41.7500,   -74.7833),
    ("White Lake",       "NY",  41.6833,   -74.8333),
    ("Kiamesha Lake",    "NY",  41.6833,   -74.6667),
    ("Parksville",       "NY",  41.8500,   -74.7500),
    ("Glen Wild",        "NY",  41.6539,   -74.5872),
    ("Rock Hill",        "NY",  41.6300,   -74.6000),
    ("Livingston Manor", "NY",  41.9000,   -74.8333),
    ("Kerhonkson",       "NY",  41.7750,   -74.3000),
    ("Ellenville",       "NY",  41.7200,   -74.4000),
    ("Napanoch",         "NY",  41.7417,   -74.3750),
    ("Greenfield Park",  "NY",  41.7250,   -74.4861),
    ("Spring Glen",      "NY",  41.6700,   -74.4300),

    # ─── USA — NYC ───
    ("Williamsburg",     "NY",  40.7167,   -73.9500),
    ("Borough Park",     "NY",  40.6333,   -73.9833),
    ("Flatbush",         "NY",  40.6500,   -73.9333),
    ("Far Rockaway",     "NY",  40.6000,   -73.7667),
    ("New York",         "NY",  40.7200,   -74.0200),
    ("Yonkers",          "NY",  40.9500,   -73.8833),
    ("Scarsdale",        "NY",  40.9800,   -73.8200),

    # ─── USA — NY Upstate ───
    ("Rochester",        "NY",  43.2000,   -77.6200),
    ("Buffalo",          "NY",  42.9000,   -78.8800),
    ("Syracuse",         "NY",  43.0500,   -76.1700),
    ("Binghamton",       "NY",  42.1000,   -75.9200),

    # ─── USA — New Jersey ───
    ("Lakewood",         "NJ",  40.1000,   -74.2200),
    ("Toms River",       "NJ",  39.9542,   -74.1931),
    ("Deal",             "NJ",  40.2500,   -74.0000),
    ("Passaic",          "NJ",  40.8639,   -74.1222),
    ("Teaneck",          "NJ",  40.8800,   -74.0100),
    ("Paterson",         "NJ",  40.9300,   -74.1500),
    ("Newark",           "NJ",  40.7300,   -74.1700),
    ("Elizabeth",        "NJ",  40.6667,   -74.2167),
    ("Jersey City",      "NJ",  40.7333,   -74.0667),
    ("Union City",       "NJ",  40.7667,   -74.0333),
    ("Linden",           "NJ",  40.6208,   -74.2361),
    ("Roselle",          "NJ",  40.6583,   -74.2650),
    ("Union",            "NJ",  40.6958,   -74.2639),

    # ─── USA — Pennsylvania / Mid-Atlantic ───
    ("Philadelphia",     "PA",  39.9500,   -75.1100),
    ("Pittsburgh",       "PA",  40.4300,   -80.0000),
    ("Scranton",         "PA",  41.4200,   -75.6700),
    ("Allentown",        "PA",  40.6200,   -75.5000),
    ("Erie",             "PA",  42.1200,   -80.0800),
    ("Baltimore",        "MD",  39.2800,   -76.6200),
    ("Washington",       "DC",  38.9000,   -77.0100),
    ("Arlington",        "VA",  38.9000,   -77.1000),
    ("Richmond",         "VA",  37.5000,   -77.4700),
    ("Norfolk",          "VA",  42.0200,   -97.4200),

    # ─── USA — New England ───
    ("Stamford",         "CT",  41.0500,   -73.5300),
    ("New Haven",        "CT",  41.3000,   -72.9300),
    ("Waterbury",        "CT",  41.5500,   -73.0500),
    ("Hartford",         "CT",  41.7600,   -72.6800),
    ("Bridgeport",       "CT",  41.1800,   -73.1800),
    ("Boston",           "MA",  42.3500,   -71.0700),
    ("Worcester",        "MA",  42.2800,   -71.8000),
    ("Springfield",      "MA",  42.1200,   -72.6000),
    ("Providence",       "RI",  41.8300,   -71.4200),

    # ─── USA — Southeast ───
    ("Miami",            "FL",  25.7600,   -80.2000),
    ("Orlando",          "FL",  28.5300,   -81.3800),
    ("Tampa",            "FL",  27.9500,   -82.4500),
    ("Jacksonville",     "FL",  30.3300,   -81.6700),
    ("Atlanta",          "GA",  33.7500,   -84.3800),
    ("Memphis",          "TN",  35.1300,   -90.0500),
    ("Nashville",        "TN",  36.1500,   -86.8000),
    ("New Orleans",      "LA",  29.9700,   -90.1200),
    ("Charlotte",        "NC",  35.0500,   -80.8300),
    ("Raleigh",          "NC",  35.7000,   -78.6700),

    # ─── USA — Midwest ───
    ("Chicago",          "IL",  41.8800,   -87.6300),
    ("Indianapolis",     "IN",  39.7600,   -86.1500),
    ("Cleveland",        "OH",  41.5000,   -81.6800),
    ("Cincinnati",       "OH",  39.1000,   -84.5200),
    ("Columbus",         "OH",  39.9500,   -83.0000),
    ("Toledo",           "OH",  41.6500,   -83.5300),
    ("Detroit",          "MI",  42.3300,   -83.0500),
    ("Ann Arbor",        "MI",  42.2833,   -83.7333),
    ("Milwaukee",        "WI",  43.0300,   -87.9200),
    ("Minneapolis",      "MN",  44.9800,   -93.2100),
    ("Omaha",            "NE",  41.2600,   -95.9500),
    ("St. Louis",        "MO",  38.6700,   -90.2500),
    ("Kansas City",      "MO",  39.1000,   -94.6000),

    # ─── USA — West ───
    ("Los Angeles",      "CA",  34.0500,  -118.2500),
    ("Hollywood",        "CA",  26.0167,   -80.1833),
    ("San Diego",        "CA",  32.7200,  -117.1500),
    ("San Francisco",    "CA",  37.8000,  -122.4000),
    ("Oakland",          "CA",  37.7800,  -122.2200),
    ("Sacramento",       "CA",  38.5800,  -121.5000),
    ("Phoenix",          "AZ",  33.4500,  -112.0800),
    ("Tucson",           "AZ",  32.2200,  -110.9700),
    ("Las Vegas",        "NV",  36.1800,  -115.1300),
    ("Denver",           "CO",  39.7200,  -105.0200),
    ("Dallas",           "TX",  32.7800,   -96.8000),
    ("Houston",          "TX",  29.7600,   -95.3600),
    ("Austin",           "TX",  30.2700,   -97.7500),
    ("San Antonio",      "TX",  29.4700,   -98.5100),
    ("El Paso",          "TX",  31.7500,  -106.5000),
    ("Seattle",          "WA",  47.6000,  -122.3300),
    ("Salt Lake City",   "UT",  40.7700,  -111.8800),
    ("Anchorage",        "AK",  61.2200,  -149.8800),
    ("Honolulu",         "HI",  21.3200,  -157.8700),

    # ─── Canada ───
    ("Toronto",          "ON",  43.6500,   -79.3800),
    ("Montreal",         "QC",  45.5100,   -73.5700),
    ("Vancouver",        "BC",  49.2600,  -123.1200),
    ("Calgary",          "AB",  51.0500,  -114.0800),
    ("Ottawa",           "ON",  45.4200,   -75.7000),
    ("Winnipeg",         "MB",  49.8800,   -97.1500),
    ("Hamilton",         "ON",  43.2500,   -79.8500),
    ("Edmonton",         "AB",  53.5500,  -113.4700),
    ("Quebec City",      "QC",  46.8100,   -71.2300),

    # ─── Israel — Frum centers first ───
    ("Bnei Brak",        "",    32.1000,    34.8583),
    ("Ramat Gan",        "",    32.0800,    34.8200),
    ("Givat Shmuel",     "",    32.0833,    34.8458),
    ("Givatayim",        "",    32.0639,    34.8083),
    ("Kiryat Ono",       "",    32.0700,    34.8556),
    ("Tel Aviv",         "",    32.0700,    34.7700),
    ("Holon",            "",    32.0111,    34.7583),
    ("Bat Yam",          "",    32.0200,    34.7500),
    ("Petach Tikva",     "",    32.0900,    34.8900),
    ("Rosh HaAyin",      "",    32.0958,    34.9528),
    ("Yehud",            "",    32.0292,    34.8944),
    ("Or Yehuda",        "",    32.0319,    34.8556),
    ("Kfar Chabad",      "",    31.9867,    34.8500),
    ("Jerusalem",        "",    31.7917,    35.2222),
    ("Tzur Hadassa",     "",    31.7167,    35.0972),
    ("Beitar Illit",     "",    31.7014,    35.1125),
    ("Chevron",          "",    31.5300,    35.1000),
    ("Kiryat Arba",      "",    31.5306,    35.1153),
    ("Modiin Illit",     "",    31.9333,    35.0417),
    ("Kiryat Sefer",     "",    31.9333,    35.0333),
    ("Beit Shemesh",     "",    31.7458,    34.9861),
    ("Shoham",           "",    32.0000,    34.9417),
    ("Achisamach",       "",    31.9306,    34.9111),
    ("Netanya",          "",    32.3400,    34.8550),
    ("Herzliya",         "",    32.1625,    34.8458),
    ("Kfar Saba",        "",    32.1833,    34.9111),
    ("Raanana",          "",    32.1875,    34.8833),
    ("Ramat Hasharon",   "",    32.1417,    34.8375),
    ("Hod Hasharon",     "",    32.1514,    34.8847),
    ("Elad",             "",    32.0500,    34.9556),
    ("Rishon LeTzion",   "",    31.9700,    34.8000),
    ("Rechovot",         "",    31.8944,    34.8083),
    ("Ramla",            "",    31.9250,    34.8653),
    ("Lod",              "",    31.9800,    34.8800),
    ("Yavne",            "",    31.8800,    34.7500),
    ("Gedera",           "",    31.8125,    34.7833),
    ("Ashdod",           "",    31.8000,    34.6472),
    ("Ashkelon",         "",    31.6625,    34.5639),
    ("Kiryat Gat",       "",    31.6111,    34.7708),
    ("Kiryat Malachi",   "",    31.7300,    34.7300),
    ("Yad Binyamin",     "",    31.7972,    34.8153),
    ("Beer Sheva",       "",    31.2458,    34.7958),
    ("Ofakim",           "",    31.3181,    34.6139),
    ("Netivot",          "",    31.4264,    34.5931),
    ("Dimona",           "",    31.0653,    35.0306),
    ("Arad",             "",    31.2542,    35.2167),
    ("Eilat",            "",    29.5583,    34.9500),
    ("Yerocham",         "",    30.9917,    34.9181),
    ("Hadera",           "",    32.4431,    34.9194),
    ("Pardes Chana",     "",    32.4750,    34.9833),
    ("Zichron Yaakov",   "",    32.5700,    34.9500),
    ("Haifa",            "",    32.8300,    34.9900),
    ("Kiryat Ata",       "",    32.8000,    35.1000),
    ("Kiryat Yam",       "",    32.8375,    35.0700),
    ("Kiryat Motzkin",   "",    32.8236,    35.0625),
    ("Kiryat Bialik",    "",    32.8278,    35.0819),
    ("Rechasim",         "",    32.7500,    35.1000),
    ("Nahariya",         "",    33.0100,    35.1000),
    ("Akko",             "",    32.9200,    35.0800),
    ("Afula",            "",    32.6111,    35.2972),
    ("Migdal HaEmek",    "",    32.6800,    35.2500),
    ("Nof HaGalil",      "",    32.7111,    35.3333),
    ("Nazareth",         "",    32.7000,    35.3000),
    ("Karmiel",          "",    32.9200,    35.3000),
    ("Kiryat Tivon",     "",    32.7208,    35.1361),
    ("Kiryat Shmona",    "",    33.2083,    35.5833),
    ("Metulla",          "",    33.2667,    35.5833),
    ("Chatzor HaGlilit", "",    33.0000,    35.5500),
    ("Tzfas",            "",    32.9667,    35.5000),
    ("Tveria",           "",    32.8000,    35.5333),
    ("Meron",            "",    32.9810,    35.4408),
    ("Yavniel",          "",    32.7097,    35.5000),
    ("Beit Shean",       "",    32.5042,    35.5083),
    ("Ariel",            "",    32.1056,    35.1764),
    ("Immanuel",         "",    32.1597,    35.1306),
    ("Kedumim",          "",    32.2125,    35.1583),
    ("Karnei Shomron",   "",    32.1694,    35.0972),

    # ─── UK ───
    ("London",           "",    51.5700,    -0.1400),
    ("Manchester",       "",    53.5194,    -2.2667),
    ("Gateshead",        "",    54.9700,    -1.6200),
    ("Leeds",            "",    53.8300,    -1.5800),
    ("Liverpool",        "",    53.4200,    -2.9200),
    ("Glasgow",          "",    55.8800,    -4.2500),
    ("Edinburgh",        "",    55.9500,    -3.2200),
    ("Birmingham",       "",    52.5000,    -1.8300),
    ("Brighton",         "",    50.8300,    -0.1300),
    ("Bournemouth",      "",    50.7200,    -1.8700),

    # ─── Belgium / Netherlands / Luxembourg ───
    ("Antwerp",          "",    51.2100,     4.4200),
    ("Brussels",         "",    50.8300,     4.3300),
    ("Liege",            "",    50.6300,     5.5800),
    ("Amsterdam",        "",    52.3700,     4.9000),
    ("Rotterdam",        "",    51.9200,     4.4700),
    ("Luxembourg",       "",    49.6000,     6.1500),

    # ─── Switzerland ───
    ("Zurich",           "",    47.3800,     8.5300),
    ("Basel",            "",    47.5500,     7.5800),
    ("Geneva",           "",    46.2000,     6.1500),
    ("Bern",             "",    46.9500,     7.4300),
    ("Lugano",           "",    46.0100,     8.9700),
    ("Lucerne",          "",    47.0600,     8.3200),
    ("Davos",            "",    46.8000,     9.8300),

    # ─── Austria / Germany / France ───
    ("Vienna",           "",    48.2200,    16.3300),
    ("Salzburg",         "",    47.8000,    13.0300),
    ("Linz",             "",    48.3000,    14.3000),
    ("Graz",             "",    47.0800,    15.4500),
    ("Berlin",           "",    52.5200,    13.4000),
    ("Frankfurt",        "",    50.1200,     8.6600),
    ("Munich",           "",    48.1300,    11.5700),
    ("Hamburg",          "",    53.5500,     9.9800),
    ("Cologne",          "",    50.9300,     6.9800),
    ("Dusseldorf",       "",    51.2000,     6.7800),
    ("Leipzig",          "",    51.3200,    12.3300),
    ("Paris",            "",    48.8700,     2.3300),
    ("Strasbourg",       "",    48.5800,     7.7500),
    ("Marseille",        "",    43.3000,     5.4000),
    ("Lyon",             "",    45.7500,     4.8500),
    ("Nice",             "",    43.7000,     7.2500),
    ("Toulouse",         "",    43.6000,     1.4300),
    ("Bordeaux",         "",    44.8300,    -0.5700),

    # ─── Italy / Spain / Portugal / Greece ───
    ("Milan",            "",    45.4700,     9.2000),
    ("Rome",             "",    41.9000,    12.4800),
    ("Naples",           "",    40.8500,    14.2800),
    ("Turin",            "",    45.0500,     7.6600),
    ("Florence",         "",    43.7700,    11.2500),
    ("Venice",           "",    45.4500,    12.3500),
    ("Livorno",          "",    43.5500,    10.3200),
    ("Palermo",          "",    38.1200,    13.3500),
    ("Madrid",           "",    40.4000,    -3.6800),
    ("Barcelona",        "",    41.3800,     2.1800),
    ("Valencia",         "",    39.4700,    -0.3700),
    ("Lisbon",           "",    38.7200,    -9.1300),
    ("Porto",            "",    41.1500,    -8.6167),
    ("Athens",           "",    37.9700,    23.7200),
    ("Thessaloniki",     "",    40.6300,    22.9300),

    # ─── Eastern Europe ───
    ("Budapest",         "",    47.5000,    19.0800),
    ("Debrecen",         "",    47.5300,    21.6300),
    ("Szeged",           "",    46.2500,    20.1500),
    ("Prague",           "",    50.0800,    14.4300),
    ("Brno",             "",    49.2000,    16.6200),
    ("Warsaw",           "",    52.2500,    21.0000),
    ("Krakow",           "",    50.0500,    19.9700),
    ("Gdansk",           "",    54.3800,    18.6700),
    ("Wroclaw",          "",    51.1000,    17.0000),
    ("Lodz",             "",    51.7700,    19.5000),
    ("Lublin",           "",    51.2500,    22.5800),
    ("Bratislava",       "",    48.1417,    17.1014),
    ("Kosice",           "",    48.7200,    21.2500),
    ("Bucharest",        "",    44.4300,    26.1000),
    ("Iasi",             "",    47.1500,    27.6333),
    ("Sofia",            "",    42.6800,    23.3200),

    # ─── Scandinavia / Baltics / Ireland ───
    ("Stockholm",        "",    59.3300,    18.0500),
    ("Gothenburg",       "",    57.7200,    11.9700),
    ("Malmo",            "",    55.6000,    13.0000),
    ("Copenhagen",       "",    55.6600,    12.5800),
    ("Oslo",             "",    59.9200,    10.7500),
    ("Bergen",           "",    60.3833,     5.3333),
    ("Helsinki",         "",    60.1700,    24.9700),
    ("Dublin",           "",    53.3300,    -6.2500),
    ("Cork",             "",    51.9000,    -8.4700),
    ("Reykjavik",        "",    64.1500,   -21.9500),
    ("Riga",             "",    56.9500,    24.1000),
    ("Vilnius",          "",    54.6800,    25.3200),
    ("Kaunas",           "",    54.8667,    23.9167),
    ("Tallinn",          "",    59.4200,    24.7500),

    # ─── Ukraine / Russia / Belarus / Caucasus ───
    ("Kiev",             "",    50.4300,    30.5200),
    ("Odessa",           "",    46.4700,    30.7300),
    ("Lvov",             "",    49.8300,    24.0000),
    ("Uman",             "",    48.7300,    30.2300),
    ("Moscow",           "",    55.7500,    37.5800),
    ("St Petersburg",    "",    59.9200,    30.2500),
    ("Nizhniy Novgorod", "",    56.3167,    44.0000),
    ("Novosibirsk",      "",    55.0167,    82.9333),
    ("Rostov",           "",    57.1833,    39.4000),
    ("Samara",           "",    53.2167,    50.1750),
    ("Kazan",            "",    55.7500,    49.1300),
    ("Minsk",            "",    53.9000,    27.5700),
    ("Brest",            "",    52.1333,    23.6667),
    ("Istanbul",         "",    41.0100,    28.9700),
    ("Ankara",           "",    39.9300,    32.8700),
    ("Izmir",            "",    38.4200,    27.1500),
    ("Tbilisi",          "",    41.7200,    44.8200),
    ("Baku",             "",    40.3800,    49.8500),
    ("Yerevan",          "",    40.1667,    44.5167),

    # ─── Australia / New Zealand ───
    ("Melbourne",        "",   -37.8200,   144.9700),
    ("Sydney",           "",   -33.8700,   151.2200),
    ("Brisbane",         "",   -27.4700,   153.0300),
    ("Perth",            "",   -31.9300,   115.8300),
    ("Adelaide",         "",   -34.9200,   138.5800),
    ("Canberra",         "",   -35.2800,   149.1300),
    ("Hobart",           "",   -42.8800,   147.3200),
    ("Darwin",           "",   -12.4583,   130.8500),
    ("Auckland",         "",   -36.8800,   174.7500),
    ("Wellington",       "",   -41.3000,   174.7700),

    # ─── South Africa ───
    ("Johannesburg",     "",   -26.2000,    28.0417),
    ("Cape Town",        "",   -33.9200,    18.4333),
    ("Durban",           "",   -29.8333,    30.9333),
    ("Pretoria",         "",   -25.7500,    28.2000),
    ("Port Elizabeth",   "",   -33.9500,    25.5833),

    # ─── Latin America ───
    ("Monterrey",        "",    25.6833,  -100.3167),
    ("Guadalajara",      "",    20.6667,  -103.3500),
    ("Cancun",           "",    21.1333,   -86.8167),
    ("Tijuana",          "",    32.4833,  -117.1667),
    ("Buenos Aires",     "",   -34.6000,   -58.4500),
    ("Cordoba",          "",   -31.4167,   -64.1833),
    ("Rosario",          "",   -32.9500,   -60.6700),
    ("Mendoza",          "",   -32.9000,   -68.8333),
    ("Santiago",         "",   -33.4500,   -70.6700),
    ("Valparaiso",       "",   -33.0300,   -71.6300),
    ("Lima",             "",   -12.0500,   -77.0500),
    ("Caracas",          "",    10.5000,   -66.9300),
    ("Bogota",           "",     4.6000,   -74.0800),
    ("Medellin",         "",     6.2500,   -75.5833),
    ("Quito",            "",    -0.2200,   -78.5000),
    ("Sao Paulo",        "",   -23.5300,   -46.6200),
    ("Rio de Janeiro",   "",   -22.9000,   -43.2500),
    ("Brasilia",         "",   -15.7800,   -47.9200),
    ("Curitiba",         "",   -25.4333,   -49.2833),
    ("Porto Alegre",     "",   -30.0700,   -51.1800),
    ("Salvador",         "",   -12.9667,   -38.5000),
    ("Recife",           "",    -8.0500,   -34.9000),
    ("Belo Horizonte",   "",   -19.9167,   -43.9333),
    ("Montevideo",       "",   -34.8800,   -56.1800),
    ("Asuncion",         "",   -25.3000,   -57.6333),
    ("La Paz",           "",   -16.5000,   -68.1500),
    ("Panama City",      "",     8.9500,   -79.5417),
    ("Havana",           "",    23.1300,   -82.3600),

    # ─── Asia ───
    ("Hong Kong",        "",    22.2500,   114.1500),
    ("Beijing",          "",    39.9200,   116.4200),
    ("Shanghai",         "",    31.2300,   121.4700),
    ("Shenzhen",         "",    22.5500,   114.1000),
    ("Tokyo",            "",    35.7000,   139.7700),
    ("Osaka",            "",    34.6700,   135.5000),
    ("Kyoto",            "",    35.0000,   135.7500),
    ("Nagoya",           "",    35.1333,   136.8833),
    ("Singapore",        "",     1.2800,   103.8500),
    ("Bangkok",          "",    13.7500,   100.5200),
    ("Kuala Lumpur",     "",     3.1500,   101.7200),
    ("Jakarta",          "",    -6.1750,   106.8333),
    ("Manila",           "",    14.5800,   120.9800),
    ("Seoul",            "",    37.5500,   126.9700),
    ("Taipei",           "",    25.0500,   121.5000),
    ("Mumbai",           "",    18.9700,    72.8300),
    ("Delhi",            "",    28.6700,    77.2200),
    ("Kolkata",          "",    22.5300,    88.3700),
    ("Chennai",          "",    13.0800,    80.2800),
    ("Bangalore",        "",    12.9800,    77.5800),
    ("Karachi",          "",    24.8700,    67.0500),
    ("Lahore",           "",    31.5800,    74.3000),
    ("Islamabad",        "",    33.7167,    73.0667),
    ("Hanoi",            "",    21.0300,   105.8500),

    # ─── Middle East / North Africa ───
    ("Dubai",            "",    25.2333,    55.2833),
    ("Abu Dhabi",        "",    24.4667,    54.4167),
    ("Doha",             "",    25.2500,    51.6000),
    ("Manama",           "",    26.2167,    50.5833),
    ("Muscat",           "",    23.6167,    58.6333),
    ("Kuwait City",      "",    29.3333,    47.9750),
    ("Amman",            "",    31.9500,    35.9300),
    ("Beirut",           "",    33.8800,    35.5000),
    ("Damascus",         "",    33.5000,    36.3000),
    ("Baghdad",          "",    33.3500,    44.4200),
    ("Tehran",           "",    35.6700,    51.4300),
    ("Riyadh",           "",    24.6300,    46.7200),
    ("Mecca",            "",    21.4500,    39.8200),
    ("Medina",           "",    24.5000,    39.5833),
    ("Cairo",            "",    30.0500,    31.2500),
    ("Alexandria",       "",    31.2000,    29.9000),
    ("Casablanca",       "",    33.6000,    -7.6167),
    ("Rabat",            "",    34.0300,    -6.8500),
    ("Marrakech",        "",    31.6300,    -8.0000),
    ("Fez",              "",    34.0500,    -5.0000),
    ("Tunis",            "",    36.8000,    10.1800),
    ("Algiers",          "",    36.7700,     3.0500),
    ("Oran",             "",    35.7000,    -0.6500),
    ("Tripoli",          "",    32.9000,    13.1800),
    ("Addis Ababa",      "",     9.0250,    38.7500),
    ("Nairobi",          "",    -1.2800,    36.8200),
    ("Lagos",            "",     6.4500,     3.4000),
    ("Abuja",            "",     9.1750,     7.1667),
    ("Accra",            "",     5.5500,    -0.2200),
    ("Dakar",            "",    14.6700,   -17.4300),
]


# Hebrew / Yiddish names from the source DB. Only places where the DB provided
# an actual Hebrew-alphabet form are included here; Latin-alphabet "alternate
# names" (historical forms like Breslau, Leningrad) are filtered out.
#
# Many Eastern European entries use chassidic Yiddish forms (Lemberg for Lvov,
# Munkacs for Mukacevo, Lizhensk for Lezajsk, etc.) rather than the modern name.
#
# Easy to extend by hand: add an entry keyed on the display name from PLACES.
# Catskills towns, most US cities, and most generic world cities currently have
# no Hebrew form on record.
HEBREW_NAMES: dict[str, str] = {

    # ─── USA — New York / Hudson Valley ───
    "Kiryas Joel":                   "קרית יואל",
    "Monsey":                        "מאנסי",
    "Monroe":                        "מאנרא",

    # ─── USA — NYC ───
    "Williamsburg":                  "וויליאמסבורג",
    "Borough Park":                  "בארא פארק",
    "New York":                      "ניו יארק",

    # ─── Canada ───
    "Montreal":                      "מאנטריאל",

    # ─── Israel — Frum centers first ───
    "Bnei Brak":                     "בני ברק",
    "Ramat Gan":                     "רמת גן",
    "Givat Shmuel":                  "גבעת שמואל",
    "Givatayim":                     "גבעתיים",
    "Kiryat Ono":                    "קרית אונו",
    "Tel Aviv":                      "תל אביב",
    "Holon":                         "חולון",
    "Bat Yam":                       "בת ים",
    "Petach Tikva":                  "פתח תקוה",
    "Rosh HaAyin":                   "ראש העין",
    "Yehud":                         "יהוד",
    "Or Yehuda":                     "אור יהודה",
    "Kfar Chabad":                   "כפר חבד",
    "Jerusalem":                     "ירושלים",
    "Tzur Hadassa":                  "צור הדסה",
    "Beitar Illit":                  "ביתר",
    "Chevron":                       "חברון",
    "Kiryat Arba":                   "קרית ארבע",
    "Modiin Illit":                  "מודיעין עילית",
    "Kiryat Sefer":                  "קרית ספר",
    "Beit Shemesh":                  "בית שמש",
    "Shoham":                        "שוהם",
    "Achisamach":                    "אחיסמך",
    "Netanya":                       "נתניה",
    "Herzliya":                      "הרצליה",
    "Kfar Saba":                     "כפר סבא",
    "Raanana":                       "רעננה",
    "Ramat Hasharon":                "רמת השרון",
    "Hod Hasharon":                  "הוד השרון",
    "Elad":                          "אלעד",
    "Rishon LeTzion":                "ראשון לציון",
    "Rechovot":                      "רחובות",
    "Ramla":                         "רמלה",
    "Lod":                           "לוד",
    "Yavne":                         "יבנה",
    "Gedera":                        "גדרה",
    "Ashdod":                        "אשדוד",
    "Ashkelon":                      "אשקלון",
    "Kiryat Gat":                    "קרית גת",
    "Kiryat Malachi":                "קרית מלאכי",
    "Yad Binyamin":                  "יד בנימין",
    "Beer Sheva":                    "באר שבע",
    "Ofakim":                        "אופקים",
    "Netivot":                       "נתיבות",
    "Dimona":                        "דימונה",
    "Arad":                          "ערד",
    "Eilat":                         "אילת",
    "Yerocham":                      "ירוחם",
    "Hadera":                        "חדרה",
    "Pardes Chana":                  "פרדס חנה",
    "Zichron Yaakov":                "זכרון יעקב",
    "Haifa":                         "חיפה",
    "Kiryat Ata":                    "קרית אתא",
    "Kiryat Yam":                    "קרית ים",
    "Kiryat Motzkin":                "קרית מוצקין",
    "Kiryat Bialik":                 "קרית ביאליק",
    "Rechasim":                      "רכסים",
    "Nahariya":                      "נהריה",
    "Akko":                          "עכו",
    "Afula":                         "עפולה",
    "Migdal HaEmek":                 "מגדל העמק",
    "Nof HaGalil":                   "נצרת עילית",
    "Nazareth":                      "נצרת",
    "Karmiel":                       "כרמיאל",
    "Kiryat Tivon":                  "קרית טבעון",
    "Kiryat Shmona":                 "קרית שמונה",
    "Metulla":                       "מטולה",
    "Chatzor HaGlilit":              "חצור הגלילית",
    "Tzfas":                         "צפת",
    "Tveria":                        "טבריה",
    "Meron":                         "מירון",
    "Yavniel":                       "יבניאל",
    "Beit Shean":                    "בית שאן",
    "Ariel":                         "אריאל",
    "Immanuel":                      "עמנואל",
    "Kedumim":                       "קדומים",
    "Karnei Shomron":                "קרני שומרון",

    # ─── UK ───
    "London":                        "לונדון",
    "Manchester":                    "מאנשעסטער",
    "Gateshead":                     "גייטסהעד",

    # ─── Belgium / Netherlands / Luxembourg ───
    "Antwerp":                       "אנטווערפען",
    "Amsterdam":                     "אמסטערדאם",

    # ─── Switzerland ───
    "Zurich":                        "ציריך",
    "Basel":                         "באזעל",
    "Lucerne":                       "לוצערן",

    # ─── Austria / Germany / France ───
    "Vienna":                        "וויען",
    "Berlin":                        "בערלין",
    "Frankfurt":                     "פראנקפורט",
    "Munich":                        "מינכען",
    "Paris":                         "פאריז",
    "Strasbourg":                    "שטראסבורג",

    # ─── Italy / Spain / Portugal / Greece ───
    "Lisbon":                        "ליסאבון",

    # ─── Eastern Europe ───
    "Budapest":                      "בודאפעסט",
    "Debrecen":                      "דעברעצין",
    "Szeged":                        "סעגעדין",
    "Prague":                        "פראג",
    "Brno":                          "ברין",
    "Warsaw":                        "ווארשא",
    "Krakow":                        "קראקא",
    "Gdansk":                        "דאנציג",
    "Lodz":                          "לאדז",
    "Lublin":                        "לובלין",
    "Bratislava":                    "פרעשבורג",
    "Kosice":                        "קאשוי",
    "Bucharest":                     "בוקארעסט",
    "Iasi":                          "יאסי",

    # ─── Scandinavia / Baltics / Ireland ───
    "Vilnius":                       "ווילנא",
    "Kaunas":                        "קובנא",

    # ─── Ukraine / Russia / Belarus / Caucasus ───
    "Kiev":                          "קיעוו",
    "Lvov":                          "לעמבערג",
    "Uman":                          "אומאן",
    "Minsk":                         "מינסק",

    # ─── Australia / New Zealand ───
    "Melbourne":                     "מעלבורן",

    # ─── Latin America ───
    "Sao Paulo":                     "סאו פאולא",

    # ─── Middle East / North Africa ───
    "Damascus":                      "דמשק",
    "Baghdad":                       "באגדד",
    "Cairo":                         "קאהיר",
    "Alexandria":                    "אלכסנדריה",
}


def get_hebrew_name(name: str) -> str | None:
    """Return the Hebrew/Yiddish form for a place name, or None if not on file."""
    return HEBREW_NAMES.get(name)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    earth_r = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * earth_r * math.asin(math.sqrt(a))


def find_place(latitude: float, longitude: float) -> tuple[str, str, float, float] | None:
    """Snap (lat, lon) to a curated community centroid.

    1) Custom-bbox places (KJ) match if (lat, lon) is inside the explicit box.
    2) Otherwise, the nearest entry in PLACES within DEFAULT_RADIUS_KM wins.

    Returns (name, state, snapped_lat, snapped_lon) on match, or None.
    """
    # Phase 1: custom bbox places (wide catch-all coverage)
    for name, state, p_lat, p_lon in PLACES:
        bbox = CUSTOM_BBOX.get(name)
        if bbox is None:
            continue
        lat_min, lat_max, lon_min, lon_max = bbox
        if lat_min <= latitude <= lat_max and lon_min <= longitude <= lon_max:
            return name, state, p_lat, p_lon

    # Phase 2: nearest non-custom place within DEFAULT_RADIUS_KM
    best: tuple[str, str, float, float] | None = None
    best_dist = DEFAULT_RADIUS_KM
    for name, state, p_lat, p_lon in PLACES:
        if name in CUSTOM_BBOX:
            continue
        d = _haversine_km(latitude, longitude, p_lat, p_lon)
        if d <= best_dist:
            best_dist = d
            best = (name, state, p_lat, p_lon)
    return best

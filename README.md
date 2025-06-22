# YidCal is a Yiddish Calendar Integration for Home Assistant

A custom Home Assistant integration that provides:

* **No Melucha** (`binary_sensor.yidcal_no_melucha`) (e.g., on Shabbos and Yom Tov)  
* **Holiday Sensor** (`sensor.yidcal_holiday`) with boolean attributes for every holiday, including:  
  א׳ סליחות, ערב ראש השנה, ראש השנה א׳, ראש השנה ב׳, ראש השנה א׳ וב׳, מוצאי ראש השנה, צום גדליה, שלוש עשרה מדות, ערב יום כיפור, יום הכיפורים, מוצאי יום הכיפורים, ערב סוכות, סוכות א׳, סוכות ב׳, סוכות א׳ וב׳, א׳ דחול המועד סוכות, ב׳ דחול המועד סוכות, ג׳ דחול המועד סוכות, ד׳ דחול המועד סוכות, חול המועד סוכות, הושענא רבה, שמיני עצרת, שמחת תורה, מוצאי סוכות, ערב חנוכה, חנוכה, שובבים, שובבים ת״ת, צום עשרה בטבת, ט״ו בשבט, תענית אסתר, פורים, שושן פורים, ליל בדיקת חמץ, ערב פסח, פסח א׳, פסח ב׳, פסח א׳ וב׳, א׳ דחול המועד פסח, ב׳ דחול המועד פסח, ג׳ דחול המועד פסח, ד׳ דחול המועד פסח, חול המועד פסח, שביעי של פסח, אחרון של פסח, מוצאי פסח, ל״ג בעומר, ערב שבועות, שבועות א׳, שבועות ב׳, שבועות א׳ וב׳, מוצאי שבועות, צום שבעה עשר בתמוז, מוצאי צום שבעה עשר בתמוז, ערב תשעה באב, תשעה באב, תשעה באב נדחה, מוצאי תשעה באב, ראש חודש  
* **Erev** (`binary_sensor.yidcal_erev`) Turns on at the Alos Erev Shabbos, Yom Tov, etc., via the day-label sensor and dedicated binary sensors  
* **Full Molad Display** (`sensor.yidcal_molad` → `friendly` attribute) Full human-friendly Molad string in Yiddish  
* **Parsha** (`sensor.yidcal_parsha`) weekly Torah portion  
* **Rosh Chodesh Today** (`sensor.yidcal_rosh_chodesh_today`) i.e.: `א' ד'ראש חודש שבט` if today (after nightfall) is Rosh Chodesh  
* **Shabbos Mevorchim** and **Upcoming Shabbos Mevorchim** indicators (booleans)  
* **Rosh Chodesh** sensor with nightfall and midnight attributes  
* **Special Shabbos** sensor for Shabbat specials (שבת זכור, שבת נחמו, etc.)  
* **Sefiras HaOmer** counters in Yiddish with the option to remove nikud (הַיּוֹם אַרְבָּעִים יוֹם שֶׁהֵם חֲמִשָּׁה שָׁבוּעוֹת וַחֲמִשָּׁה יָמִים לָעֹֽמֶר and הוֹד שֶׁבְּיְסוֹד)  
* **Day Label Yiddish** (`sensor.yidcal_day_label`) Daily label in Yiddish (e.g. זונטאג, מאנטאג, ערש"ק, מוצש"ק)  
* **Date** (`sensor.yidcal_date`) Current Hebrew date in Yiddish (e.g., כ״ה חשון תשפ״ה)  
* **Zman Erev** (`sensor.yidcal_zman_erev`) Next candle-lighting timestamp (Shabbos or Yom Tov eve)  
* **Zman Motzi** (`sensor.yidcal_zman_motzi`) Next havdalah timestamp (Shabbos or Yom Tov end)  
* **Perek Avos**: current Perek rendered in אבות פרק ה׳  
* **Morid Geshem/Tal Sensor** (`sensor.yidcal_morid_geshem_or_tal`) Indicates when to change the prayer between “Morid HaGeshem”/“Morid HaTal”  
* **Tal U’Matar** (`sensor.yidcal_tal_umatar`) Indicates when to change the prayer between “V’sen Tal u’Matar”/“V’sen Beracha”  
* **No Music** (`binary_sensor.yidcal_no_music`) Indicates when music is prohibited (e.g., in Sefirah, three weeks)  
* **Upcoming Shabbos Mevorchim** (`binary_sensor.yidcal_upcoming_shabbos_mevorchim`) `on` if the upcoming Shabbos is Mevorchim  
* **Shabbos Mevorchim** (`binary_sensor.yidcal_shabbos_mevorchim`) `on` if today is Shabbos Mevorchim  
* **Special Prayer Sensor** (`sensor.yidcal_special_prayer`) Aggregates special insertions (e.g., ותן טל, יעלה ויבוא, על הניסים)  
* **Special Shabbos Sensor** (`sensor.yidcal_special_shabbos`) Special Shabbat names (שבת זכור, שבת נחמו, etc.)  
* **Sefirah Counter** (`sensor.yidcal_sefirah_counter`) Day-count of Sefiras HaOmer  
* **Sefirah Middos** (`sensor.yidcal_sefirah_counter_middos`) Middos (qualities) of the day in the Omer count  

All date calculations are standalone (no external Jewish-calendar integration) and use your Home Assistant latitude, longitude & timezone.

---

## Location Resolution

To ensure you calculate sunrise/sunset on the correct center of your municipality for the Zmanim Sensors (and fix boroughs like Brooklyn in NYC):

1. **Reverse lookup** your HA’s latitude/longitude via Nominatim (OSM) to pull the OSM “city” or—if in New York City—the `city_district` (Brooklyn, Queens, etc.).  
2. **Forward geocode** only `"City, State"` (no ZIP, no bias) via Nominatim’s `geocode(exactly_one=True)` to snap to the official polygon centroid.  
3. Use **TimezoneFinder** to resolve your timezone from the final lat/lon.  

---

## Configuration Options

After adding the integration via UI, go to **Settings → Devices & Services → YidCal → Options** to set:

| Option                                          | Default | Description                                                                                                |
| ----------------------------------------------- | ------- | ---------------------------------------------------------------------------------------------------------- |
| `וויפיל מינוט פארן שקיעה איז הדלקת הנרות`       | 15      | Minutes before sunset for Erev Shabbos                                                                     |
| `וויפיל מינוט נאכן שקיעה איז מוצאי`             | 72      | Minutes after sunset for Motzaei Shabbos                                                                   |
| `נעם אראפ די נְקֻודּוֹת`                        | false   | Remove Hebrew vowel points from Omer text                                                                  |
| `צולייגען באזונדערע סענסאָרס פאר די ימים טובים` | true    | Add/remove separate binary sensors for each holiday (they always show as attributes in the holiday sensor) |

> ⚠️ **Important:** If you previously enabled separate holiday binary sensors and later disable them in Options, those entities will **not** auto-delete. You must manually remove them via **Settings → Entities**, or delete and re-add the integration with the holiday sensors option turned off.

---

## Requirements

* HA 2023.7+  
* Python 3.10+  
* **HACS** recommended  
* Dependencies installed via manifest:
  * `hdate[astral]==1.1.0`  
  * `pyluach==2.2.0`  
  * `zmanim==0.3.1`  
  * `timezonefinder==6.5.9`  
  * `geopy==2.4.1`

---

## Installation

### HACS (Recommended)

1. Go to **HACS → Integrations → ⋮ → Custom repositories**  
2. Add: `https://github.com/hitchin999/yidcal` (type: Integration)  
3. Install **YidCal**  
4. Restart Home Assistant  
5. **Settings → Devices & Services → Add Integration → YidCal**

### Manual

1. Copy `custom_components/yidcal/` to `config/custom_components/`  
2. Restart Home Assistant  
3. Add integration via UI as above

---

## Lovelace Examples

```yaml
# Molad + Rosh Chodesh + Parsha
type: markdown
content: |
  🌙 {{ states('sensor.yidcal_molad') }}
  📖 {{ states('sensor.yidcal_parsha') }}
  📆 ראש חודש: {{ state_attr('sensor.yidcal_molad','rosh_chodesh') }}

# Rosh Chodesh Today Indicator
- R"Ch Today: {{ states('binary_sensor.yidcal_rosh_chodesh_today') }}

# Shabbos Mevorchim
- ש"מ: {{ states('binary_sensor.yidcal_shabbos_mevorchim') }}
- Upcoming ש"מ: {{ states('binary_sensor.yidcal_upcoming_shabbos_mevorchim') }}

# Special Shabbos
- {{ states('sensor.yidcal_special_shabbos') }}

# Omer Counters
- ספירה: {{ states('sensor.yidcal_sefirah_counter') }}
- מידות: {{ states('sensor.yidcal_sefirah_counter_middos') }}

# Yiddish Day & Date
- היום: {{ states('sensor.yidcal_day_label') }}
- תאריך: {{ states('sensor.yidcal_date') }}

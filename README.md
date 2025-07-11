[![Total Downloads](https://img.shields.io/github/downloads/hitchin999/YidCal/total.svg?label=Total%20Downloads&style=for-the-badge&color=blue)](https://github.com/hitchin999/YidCal/releases)

[![Active YidCal Installs][yidcal-badge]][yidcal-analytics]

[yidcal-badge]: https://img.shields.io/badge/dynamic/json?label=Active%20Installs&url=https%3A%2F%2Fanalytics.home-assistant.io%2Fcustom_integrations.json&query=%24.yidcal.total&style=for-the-badge&color=blue
[yidcal-analytics]: https://analytics.home-assistant.io/integration/yidcal

# YidCal is a Yiddish Calendar Integration for Home Assistant

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=hitchin999&repository=YidCal&category=Integration)

A custom Home Assistant integration that provides:

* **No Melucha** (`binary_sensor.yidcal_no_melucha`) (e.g., on Shabbos and Yom Tov)
* **Holiday Sensor** (`sensor.yidcal_holiday`) with boolean attributes for every holiday, including:
  א׳ סליחות, ערב ראש השנה, ראש השנה א׳, ראש השנה ב׳, ראש השנה א׳ וב׳, מוצאי ראש השנה, צום גדליה, שלוש עשרה מדות, ערב יום כיפור, יום הכיפורים, מוצאי יום הכיפורים, ערב סוכות, סוכות א׳, סוכות ב׳, סוכות א׳ וב׳, א׳ דחול המועד סוכות, ב׳ דחול המועד סוכות, ג׳ דחול המועד סוכות, ד׳ דחול המועד סוכות, חול המועד סוכות, הושענא רבה, שמיני עצרת, שמחת תורה, מוצאי סוכות, ערב חנוכה, חנוכה, שובבים, שובבים ת״ת, צום עשרה בטבת, ט״ו בשבט, תענית אסתר, פורים, שושן פורים, ליל בדיקת חמץ, ערב פסח, פסח א׳, פסח ב׳, פסח א׳ וב׳, א׳ דחול המועד פסח, ב׳ דחול המועד פסח, ג׳ דחול המועד פסח, ד׳ דחול המועד פסח, חול המועד פסח, שביעי של פסח, אחרון של פסח, מוצאי פסח, ל״ג בעומר, ערב שבועות, שבועות א׳, שבועות ב׳, שבועות א׳ וב׳, מוצאי שבועות, צום שבעה עשר בתמוז, מוצאי צום שבעה עשר בתמוז, ערב תשעה באב, תשעה באב, תשעה באב נדחה, מוצאי תשעה באב, ראש חודש
* **Erev** (`binary_sensor.yidcal_erev`) Turns on at the Alos Erev Shabbos, Yom Tov, etc., via the day-label sensor and dedicated binary sensors
* **Molad** (`sensor.yidcal_molad` → `friendly` attribute) Full human-friendly Molad string in Yiddish
* **Full Display Sensor** (sensor.yidcal_full_display) displays it all in one (e.g פרייטאג פרשת קרח ~ ב׳ ד׳ראש חודש תמוז)
* **Parsha** (`sensor.yidcal_parsha`) weekly Torah portion
* **Rosh Chodesh Today** (`sensor.yidcal_rosh_chodesh_today`) i.e.: `א' ד'ראש חודש שבט` if today (after nightfall) is Rosh Chodesh
* **Special Shabbos** sensor for Shabbat specials (שבת זכור, שבת נחמו, etc.)
* **Sefiras HaOmer** counters in Yiddish with the option to remove nikud (הַיּוֹם אַרְבָּעִים יוֹם שֶׁהֵם חֲמִשָּׁה שָׁבוּעוֹת וַחֲמִשָּׁה יָמִים לָעֹֽמֶר and הוֹד שֶׁבְּיְסוֹד)
* **Day Label Yiddish** (`sensor.yidcal_day_label_yiddish`) Daily label in Yiddish (e.g. זונטאג, מאנטאג, ערש"ק, מוצש"ק)
* **Day Label Hebrew** (`sensor.yidcal_day_label_hebrew`) Daily label in Hebrew (e.g. יום א' יום ב)
* **Daily Tehilim** (`sensor.yidcal_tehilim_daily`) Five-chapter rotation of Tehilim (e.g. א–ה, ו–ט)
* **Date** (`sensor.yidcal_date`) Current Hebrew date in Yiddish (e.g., כ"ה חשון תשפ"ה)
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
* **Zman Talis & Tefilin** (`sensor.yidcal_zman_tallis_tefilin`) – Misheyakir: Alos HaShachar + configured offset
* **Sof Zman Krias Shma (MGA)** (`sensor.yidcal_sof_zman_krias_shma_mga`) – end-of-Shema, Magen Avraham
* **Sof Zman Krias Shma (GRA)** (`sensor.yidcal_sof_zman_krias_shma_gra`) – end-of-Shema, Vilna Ga’on
* **Sof Zman Tefilah (MGA)** (`sensor.yidcal_sof_zman_tefilah_mga`) – end-of-prayer, Magen Avraham
* **Sof Zman Tefilah (GRA)** (`sensor.yidcal_sof_zman_tefilah_gra`) – end-of-prayer, Vilna Ga’on
* **Zman Netz HaChamah** (`sensor.yidcal_netz`) – sunrise
* **Zman Alos HaShachar** (`sensor.yidcal_alos`) – dawn
* **Zman Chatzos** (`sensor.yidcal_chatzos_hayom`) – halakhic midday
* **Zman Plag HaMincha** (`sensor.yidcal_plag_mincha`) – 10¾-sha‘ot
* **Zman Shkiat HaChamah** (`sensor.yidcal_shkia`) – sunset
* **Zman Maariv +60m** (`sensor.yidcal_zman_maariv_60`) – 60 min after sunset
* **Zman Maariv R"T** (`sensor.yidcal_zman_maariv_rt`) – 72 min after sunset
* **Zman Chatzos Hayom** (`sensor.yidcal_chatzos_hayom`) – midnight of night,
* **Zman Mincha Gedola** (`sensor.yidcal_mincha_gedola`) – earliest Mincha
* **Zman Mincha Ketana** (`sensor.yidcal_mincha_ketana`) – preferred Mincha
* **Zman Chatzos HaLaila** (`sensor.yidcal_chatzos_haleila`) – midnight of night
* **Upcoming Holiday Sensor** (`binary_sensor.yidcal_upcoming_holiday`)
* **Sof Zman Achilas Chumetz** (`sensor.yidcal_zman_achilas_chumetz`)
* **Sof Zman Sreifes Chumetz** (`sensor.yidcal_zman_sreifes_chumetz`)
* **Ishpizin** (`sensor.yidcal_ishpizin`) - אושפיזא דאברהם, אושפיזא דיצחק
* **Nine Days** (`binary_sensor.yidcal_nine_days`) - turns on Rosh Chodesh Av & turns off 10 Av at Chatzos.
  
*All date calculations are standalone (no external Jewish-calendar integration) and use your Home Assistant latitude, longitude & timezone.*

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
| `וויפיל מינוט נאכן עלות איז טלית ותפילין`       | 22      | Minutes after Alos HaShachar for Talis & Tefilin (Misheyakir)                                              |
| `נעם אראפ די נְקֻודּוֹת`                        | false   | Remove Hebrew vowel points from Omer text                                                                  |
| `צולייגען באזונדערע סענסאָרס פאר די ימים טובים` | true    | Add/remove separate binary sensors for each holiday (they always show as attributes in the holiday sensor) |
| `Full Display Sensor וויזוי דו ווילסט זעהן דעם טאג ביי די`          | yiddish | Choose how to display the day label (Yiddish or Hebrew)                                                    |

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

````yaml
type: conditional
conditions:
  - condition: state
    entity: sensor.yidcal_holiday
    attribute: מען פאַסט אויס און
    state_not: ""
card:
  type: horizontal-stack
  cards:
    - type: markdown
      content: >
        {% set hhmm = state_attr('sensor.yidcal_holiday', 'מען פאַסט אויס און')
        %}

        {% if hhmm %}

        <center>
          <b><font size="2">מען פאַסט אויס און</font></b><br><br>
          <ha-icon icon="mdi:clock-end" style="width:24px;height:24px;"></ha-icon><br><br>
          <b><font size="5">{{ hhmm }}</font></b>
        </center>

        {% endif %}
      text_only: true
````
> _By Yoel Goldstein / Vaayer LLC_

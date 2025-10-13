# YidCal is a Yiddish Calendar Integration for Home Assistant

[![Peak Release Downloads](https://img.shields.io/badge/dynamic/json?style=for-the-badge&label=Peak%20Release%20Downloads&url=https%3A%2F%2Fraw.githubusercontent.com%2Fhitchin999%2Fyidcal-data%2Fmain%2Fbadge%2Fpeak_release_downloads.json&query=%24.value&color=blue)](https://github.com/hitchin999/YidCal/releases)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=hitchin999&repository=YidCal&category=Integration)

A custom Home Assistant integration that provides:

* **No Melucha** (`binary_sensor.yidcal_no_melucha`) (e.g., on Shabbos and Yom Tov)
* **Holiday Sensor** (`sensor.yidcal_holiday`) with boolean attributes for every holiday, including:
  א׳ סליחות, ערב ראש השנה, ראש השנה א׳, ראש השנה ב׳, ראש השנה א׳ וב׳, מוצאי ראש השנה, צום גדליה, שלוש עשרה מדות, ערב יום כיפור, יום הכיפורים, מוצאי יום הכיפורים, ערב סוכות, סוכות, סוכות א׳, סוכות ב׳, סוכות א׳ וב׳, א׳ דחול המועד סוכות, ב׳ דחול המועד סוכות, ג׳ דחול המועד סוכות, ד׳ דחול המועד סוכות, חול המועד סוכות, שבת חול המועד סוכות, הושענא רבה, שמיני עצרת, שמחת תורה, שמיני עצרת/שמחת תורה, מוצאי סוכות, אסרו חג סוכות, ערב חנוכה, חנוכה, זאת חנוכה, שובבים, שובבים ת״ת, צום עשרה בטבת, ט״ו בשבט, תענית אסתר, פורים, שושן פורים, ליל בדיקת חמץ, ערב פסח, פסח, פסח א׳, פסח ב׳, פסח א׳ וב׳, א׳ דחול המועד פסח, ב׳ דחול המועד פסח, ג׳ דחול המועד פסח, ד׳ דחול המועד פסח, חול המועד פסח, שבת חול המועד פסח, שביעי של פסח, אחרון של פסח, שביעי/אחרון של פסח, מוצאי פסח, אסרו חג פסח, פסח שני, ל״ג בעומר, ערב שבועות, שבועות א׳, שבועות ב׳, שבועות א׳ וב׳, מוצאי שבועות, אסרו חג שבועות, צום שבעה עשר בתמוז, מוצאי צום שבעה עשר בתמוז, ערב תשעה באב, תשעה באב, תשעה באב נדחה, מוצאי תשעה באב, יום כיפור קטן, ראש חודש, שבת ראש חודש
* **Erev** (`binary_sensor.yidcal_erev`)
  Turns **on** at **Alos** on qualifying Erev days (Erev-Shabbos or weekday Erev-Yom-Tov) and **off** at **candle-lighting**.
  * **Attribute `Eruv_Tavshilin`** – **true only from Alos to Shkiah** on Erev-Yom-Tov.
* **Motzi** (`binary_sensor.yidcal_motzi`) Turns on at night Motzi Shabbos, Yom Tov. Turns off at 2AM
* **Molad** (`sensor.yidcal_molad` → `friendly` attribute) Full human-friendly Molad string in Yiddish
* **Full Display Sensor** (`sensor.yidcal_full_display`) displays it all in one (e.g פרייטאג פרשת קרח ~ ב׳ ד׳ראש חודש תמוז)
* **Parsha** (`sensor.yidcal_parsha`) weekly Torah portion
* **Rosh Chodesh Today** (`sensor.yidcal_rosh_chodesh_today`) i.e.: `א' ד'ראש חודש שבט` if today (after nightfall) is Rosh Chodesh
* **Special Shabbos** sensor for Shabbat specials (שבת זכור, שבת נחמו, etc.)
* **Sefiras HaOmer** counters in Yiddish with the option to remove nikud (הַיּוֹם אַרְבָּעִים יוֹם שֶׁהֵם חֲמִשָּׁה שָׁבוּעוֹת וַחֲמִשָּׁה יָמִים לָעֹֽמֶר and הוֹד שֶׁבְּיְסוֹד)
* **Day Label Yiddish** (`sensor.yidcal_day_label_yiddish`) Daily label in Yiddish (e.g. זונטאג, מאנטאג, ערש"ק, מוצש"ק)
* **Day Label Hebrew** (`sensor.yidcal_day_label_hebrew`) Daily label in Hebrew (e.g. יום א' יום ב)
* **Daily Tehilim** (`sensor.yidcal_tehilim_daily`) Five-chapter rotation of Tehilim (e.g. א–ה, ו–ט)
* **Date** (`sensor.yidcal_date`) Current Hebrew date in Yiddish (e.g., כ"ה חשון תשפ"ה)
* **Zman Erev** (`sensor.yidcal_zman_erev`) — **Next candle-lighting timestamp**
  * **What it shows:** The current day’s candle-lighting for **Shabbos or Yom Tov**.
    * **Erev Shabbos / Erev Yom Tov (weekday):** `sunset − candlelighting_offset`
    * **Between Yom Tov days (Night 2)** and **Motzi Shabbos → Yom Tov:** `sunset + havdalah_offset`
  * **When it updates:**
    * At **local 12:00 AM**, it flips to the lighting for **that civil day** when applicable.
    * If **today has no lighting**, it **holds the most recent lighting** and only jumps forward at **the first midnight after Motzi** (the day right after Shabbos or the final Yom-Tov day).
    * During **Shabbos/Yom-Tov day**, it keeps showing **yesterday’s lighting** until midnight (prevents jumping mid-day).
  * **Attributes:**
    * `Zman_Erev_With_Seconds` – ISO local time (unrounded)
    * `Zman_Erev_Simple` – HH\:MM (local)
    * `City`, `Latitude`, `Longitude`
    * **Static “Day” rows for Shabbos↔Yom-Tov clusters** (always present; empty when not applicable):
      * `Day_1_Label`, `Day_1_Simple`
      * `Day_2_Label`, `Day_2_Simple`
      * `Day_3_Label`, `Day_3_Simple`
      * Labels auto-select: **“Shabbos”**, **“Yom Tov – Night 1”**, **“Yom Tov – Night 2”**, or **“Motzi Shabbos → Yom Tov”** as appropriate.
* **Zman Motzi** (`sensor.yidcal_zman_motzi`) — **Next havdalah timestamp**
  * **What it shows:** The **earliest** of:
    1. **End of the next Yom-Tov span**: `sunset(last day) + havdalah_offset`
    2. **Next Motzi Shabbos**: `sunset(Saturday) + havdalah_offset`
       (When currently inside a Yom-Tov span, it targets that span’s end.)
  * **When it updates:**
    * Holds **tonight’s havdalah** and then rolls at **local 12:00 AM** after that night.
  * **Attributes:**
    * `Zman_Motzi_With_Seconds` – ISO local time (unrounded)
    * `Zman_Motzi_Simple` – HH\:MM (local)
    * `City`, `Latitude`, `Longitude`
* **Perek Avos**: current Perek rendered in אבות פרק ה׳
* **Morid Geshem/Tal Sensor** (`sensor.yidcal_morid_geshem_or_tal`) Indicates when to change the prayer between “Morid HaGeshem”/“Morid HaTal”
* **Tal U’Matar** (`sensor.yidcal_tal_umatar`) Indicates when to change the prayer between “V’sen Tal u’Matar”/“V’sen Beracha”
* **No Music** (`binary_sensor.yidcal_no_music`) Indicates when music is prohibited (e.g., in Sefirah, three weeks)
* **Upcoming Shabbos Mevorchim** (`binary_sensor.yidcal_upcoming_shabbos_mevorchim`) `on` if the upcoming Shabbos is Mevorchim
* **Shabbos Mevorchim** (`binary_sensor.yidcal_shabbos_mevorchim`) `on` if today is Shabbos Mevorchim
* **Special Prayer Sensor** (`sensor.yidcal_special_prayer`)  
  * Aggregates all liturgical insertions (e.g., מוריד הגשם / מוריד הטל, ותן טל ומטר לברכה / ותן ברכה, יעלה ויבוא, על הניסים, עננו, נחם)  
  * **Attribute `הושענות`** – shows the daily Hoshana during Sukkot (e.g., למען אמתך, אבן שתיה, אום נצורה)
* **Special Shabbos Sensor** (`sensor.yidcal_special_shabbos`) Special Shabbat names (שבת זכור, שבת נחמו, etc.)
* **Sefirah Counter** (`sensor.yidcal_sefirah_counter`) Day-count of Sefiras HaOmer
* **Sefirah Middos** (`sensor.yidcal_sefirah_counter_middos`) Middos (qualities) of the day in the Omer count
* **Slichos** (`binary_sensor.yidcal_slichos`) 
  * Continuous Selichos window: turns **on** at **Alef Slichos** Motzaei-Shabbos (havdalah) and stays on until **Erev Yom Kippur** candle-lighting; auto-**off** on any intervening **Shabbos** or **Rosh Hashanah (1–2 Tishrei)**.  
  * **Attribute `Selichos_Label`** – Hebrew label for the current Selichos day (e.g., סליחות ליום א׳, סליחות לערב ר״ה, סליחות ליום חמישי מעשי״ת)
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
* **Zman Tzies Hakochavim** (`sensor.yidcal_tzies_hakochavim`) – stars emergence (sunset + havdalah_offset from config flow)
* **Zman Chatzos Hayom** (`sensor.yidcal_chatzos_hayom`) – midnight of night,
* **Zman Mincha Gedola** (`sensor.yidcal_mincha_gedola`) – earliest Mincha
* **Zman Mincha Ketana** (`sensor.yidcal_mincha_ketana`) – preferred Mincha
* **Zman Chatzos HaLaila** (`sensor.yidcal_chatzos_haleila`) – midnight of night
* **Upcoming Yom Tov Sensor** (`binary_sensor.yidcal_upcoming_yomtov`)
  * **What it shows:** ON/OFF for the run-up to the next target.
    * `Next_Holiday` – one of: **ראש השנה, יום כיפור, סוכות, שבת חול המועד סוכות, שמיני עצרת, שמחת תורה, פסח, שבת חול המועד פסח, שביעי של פסח, אחרון של פסח, שבועות**
    * `Date` – target civil date
    * `Next_On` – next future activation (always forward-looking)
  * **When it turns ON/OFF:**
    * **ON:** **12:00 AM** after the latest **Motzi** (Shabbos or Yom Tov), leading into the next target (includes **שבת חול המועד**).
    * **OFF:** At **candle-lighting** of the target’s **erev** (sunset − `candlelighting_offset`).
* **Upcoming Holiday Sensor** * (`sensor.yidcal_upcoming_holiday`)
* **Exposes:** all holiday flags as **booleans** (True/False). Use these in automations.
* **State:** a short, readable list of the labels of upcoming holidays that are **active right now** (e.g., `ערב סוכות, סוכות א׳`).
* **Behavior:** pre-activates up to your **Lookahead days**; updates nightly at **12:02 AM**; honors your candle-lighting & havdalah offsets.

* **Sof Zman Achilas Chumetz** (`sensor.yidcal_zman_achilas_chumetz`)
* **Sof Zman Sreifes Chumetz** (`sensor.yidcal_zman_sreifes_chumetz`)
* **Ishpizin** (`sensor.yidcal_ishpizin`) - אושפיזא דאברהם, אושפיזא דיצחק
* **Nine Days** (`binary_sensor.yidcal_nine_days`) - turns on Rosh Chodesh Av & turns off 10 Av at Chatzos.
* **Day Type** (`binary_sensor.yidcal_day_type`) Indicates the type of the current day (Any Other Day, Shabbos, Yom Tov, Shabbos & Yom Tov, Erev, Motzi, Fast Day, Chol Hamoed, Shabbos & Chol Hamoed)
* **Yurtzeit Sensor** (`sensor.yidcal_yurtzeit`) - Displays today's yurtzeits (flipping at sunset + havdalah_offset), with attributes for each yurtzeit name.
* **Yurtzeit Weekly Sensor** (`sensor.yidcal_yurtzeits_weekly`) - Displays weekly yurtzeits (flipping at Saturday sunset + havdalah_offset), with attributes for each day's yurtzeits.
  
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

| Option                                                            | Default   | Description                                                                                                                    |
| ----------------------------------------------------------------- | --------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `וויפיל מינוט פארן שקיעה איז הדלקת הנרות`                         | `15`      | Minutes before sunset for Erev Shabbos / Yom-Tov candle-lighting                                                               |
| `וויפיל מינוט נאכן שקיעה איז מוצאי`                               | `72`      | Minutes after sunset for Motzaei Shabbos / Yom-Tov havdalah                                                                    |
| `וויפיל מינוט נאכן עלות איז טלית ותפילין`                         | `22`      | Minutes after **Alos HaShachar** for Talis & Tefilin (Misheyakir)                                                              |
| `נעם אראפ די נְקֻודּוֹת`                                          | `false`   | Remove Hebrew vowel-points (nikud) from Omer text                                                                              |
| `צולייגען באזונדערע סענסאָרס פאר די ימים טובים`                   | `true`    | Add/remove individual binary sensors for each holiday (holidays always remain as attributes)                                   |
| `Full Display Sensor וויזוי דו ווילסט זעהן דעם טאג ביי די`        | `yiddish` | Choose Yiddish (`זונטאג, מאנטאג`) or Hebrew (`יום א׳, יום ב׳`) labels for the day-of-week sensor                             |
| `צולייגען די וועכנטליכע יארצייטן סענסאר`                         | `false`   | Enable the weekly Yahrzeit summary sensor                                                                                      |
| `ווען זאל זיך די סליחות טאג טוישן`                               | `זמן הבדלה`| When the Selichos label advances to the next day: `havdalah` = after sunset + havdalah-offset; `midnight` = at 12 AM local time |
> ⚠️ **Important:** If you previously enabled separate holiday binary sensors and later disable them in Options, those entities will **not** auto-delete. You must manually remove them via **Settings → Entities**, or delete and re-add the integration with the holiday sensors option turned off.

---

## Yurtzeit Customization

The Yurtzeit sensor (`sensor.yidcal_yurtzeit`) pulls names from a GitHub-hosted JSON file by default. You can add custom names or mute existing ones using text files in your Home Assistant config directory.

Upon installation or restart, the integration automatically creates a `/config/www/yidcal-data/` folder with two sample files:

- `custom_yahrtzeits.txt`: For adding your own Yurtzeit names.
- `muted_yahrtzeits.txt`: For hiding specific names from the sensor.

### Editing Instructions

1. **Locate the Files**: Use HA's File Editor add-on, SSH, or a file transfer tool (e.g., FileZilla) to access `/config/www/yidcal-data/custom_yahrtzeits.txt` and `/config/www/yidcal-data/muted_yahrtzeits.txt`.

2. **Custom Yurtzeits (Add Names)**:
   - Format: `Hebrew Date: Full Name` (one per line).
   - Example:
     ```
     ט"ו תמוז: רבי פלוני בן רבי אלמוני זי"ע [מחבר ספר דוגמא] תש"א
     י"ז תמוז: רבי דוגמא בן רבי משל זי"ע תרצ"ב
     ```
   - Custom names are **added** to the existing GitHub names for that date.
   - Comment out lines with `#` to ignore them (samples are commented by default).

3. **Muted Yurtzeits (Hide Names)**:
   - Format: Full **exact** name (one per line, no date needed).
   - Copy the exact name from the sensor attributes or GitHub JSON.
   - Example:
     ```
     רבי פלוני בן רבי אלמוני זי"ע [מחבר ספר דוגמא] תש"א
     רבי דוגמא בן רבי משל זי"ע תרצ"ב
     ```
   - Muted names are hidden globally (from both GitHub and custom lists).
   - Comment out lines with `#` to ignore them.

4. **Save and Restart**: After editing, save the files and restart Home Assistant (Configuration > Server Controls > Restart). Changes load only on restart.

5. **Tips**:
   - Files support Hebrew/UTF-8; use a text editor that handles it well.
   - Invalid lines (e.g., bad format) are silently skipped.
   - If files don't exist, restart HA to regenerate samples.
   - For large lists, edit on your computer and upload (ensure permissions allow writing if needed).

---

## Requirements

* HA 2023.7+
* Python 3.10+
* **HACS** recommended
* Dependencies installed via manifest:

  * `hdate[astral]==1.1.2`
  * `pyluach==2.2.0`
  * `zmanim==0.3.1`
  * `timezonefinder==5.2.0`
  * `geopy==2.4.1`

---

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant.
2. Search for "YidCal" in the Integrations section.
3. Install **YidCal**.
4. Restart Home Assistant.
5. **Settings → Devices & Services → Add Integration → YidCal**

---

## Lovelace Example For Fast In/Out Countdown Timers

````yaml
type: conditional
conditions:
  - condition: or
    conditions:
      - condition: state
        entity: sensor.yidcal_holiday
        attribute: מען פאַסט אַן און
        state_not: ""
      - condition: state
        entity: sensor.yidcal_holiday
        attribute: מען פאַסט אויס און
        state_not: ""
card:
  type: horizontal-stack
  cards:
    - type: markdown
      content: >
        {% set start = state_attr('sensor.yidcal_holiday', 'מען פאַסט אַן און') %}
        {% set end = state_attr('sensor.yidcal_holiday', 'מען פאַסט אויס און') %}

        {% if start %}
        <center>
          <b><font size="2">מען פאַסט אַן און</font></b><br><br>
          <ha-icon icon="mdi:clock-start" style="width:24px;height:24px;"></ha-icon><br><br>
          <b><font size="5">{{ start }}</font></b>
        </center>
        {% elif end %}
        <center>
          <b><font size="2">מען פאַסט אויס און</font></b><br><br>
          <ha-icon icon="mdi:clock-end" style="width:24px;height:24px;"></ha-icon><br><br>
          <b><font size="5">{{ end }}</font></b>
        </center>
        {% endif %}
      text_only: true
````
> _By Yoel Goldstein / Vaayer LLC_

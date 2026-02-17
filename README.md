# YidCal is a Yiddish Calendar Integration for Home Assistant

[![Peak Release Downloads](https://img.shields.io/badge/dynamic/json?style=for-the-badge\&label=Peak%20Release%20Downloads\&url=https%3A%2F%2Fraw.githubusercontent.com%2Fhitchin999%2Fyidcal-data%2Fmain%2Fbadge%2Fpeak_release_downloads.json\&query=%24.value\&color=blue)](https://github.com/hitchin999/YidCal/releases)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=hitchin999&repository=YidCal&category=Integration)

> [!WARNING]
> <img src="https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/svg/1f1ee-1f1f1.svg" alt="Israel flag" width="18"> **Israel option is available, but is experimental / not fully tested.**
> If you’re in Israel and notice any incorrect times or holiday behavior, please open a GitHub issue (include your city, date, and expected vs actual).
> **Important for Israel users (offsets):**
>
> * **Candle-lighting offset:** default is **15 minutes**. In Israel most people use **30–40 minutes** before sunset, so change this in Options.
> * **Havdalah offset:** default is **72 minutes**. Adjust if your minhag differs.

A custom Home Assistant integration that provides a full Yiddish/Hebrew Jewish calendar experience, including holiday spans, zmanim, fast timers, special Shabbos indicators, and automation-friendly binary sensors.

*All date calculations are standalone (no external Jewish-calendar integration) and use your Home Assistant latitude, longitude & timezone.*

---

## Devices / Services layout

Entities are grouped into these Devices/Services for clarity in Home Assistant’s “by device/service” views:

* **YidCal** — regular daily/weekly sensors
* **YidCal — Display** — sensors mainly used for dashboards
* **YidCal — Holiday Attribute Sensors** — sensors created from Holiday sensor attributes (only if enabled)
* **YidCal — Special Binary Sensors** — binary sensors such as Slichos, Eruv Tavshilin, etc.
* **YidCal — Zmanim** — all zmanim sensors

*(Existing entity IDs stay the same; grouping is for organization.)*

---

## What YidCal provides (high-level)

### Core binary sensors

* **No Melucha** (`binary_sensor.yidcal_no_melucha`) (e.g., on Shabbos and Yom Tov)
* **No Melucha – Regular Shabbos** (`binary_sensor.yidcal_no_melucha_regular_shabbos`)
  ON every **regular Shabbos** from **Friday candle lighting** to **Saturday havdalah** — **not** when Shabbos is **Yom Tov** (but **on** for **Shabbos Chol HaMoed**).
  *Attributes:* `Now`, `Window_Start`, `Window_End`
* **No Melucha – Yom Tov** (`binary_sensor.yidcal_no_melucha_yomtov`)
  ON for any contiguous **Yom Tov** span from **candle before first day** (sunset of day−1 − candle offset) through **havdalah after last day** (sunset + havdalah offset) — **regardless of weekday**.
  *Attributes:* `Now`, `Window_Start`, `Window_End`
* **Bishul Allowed** (`binary_sensor.yidcal_bishul_allowed`)
  Usually **ON**; **OFF on Shabbos and Yom Kippur**. Perfect for percolators with **auto-fill valves**.
  *Attributes:* `Now`, `Next_Off_Window_Start`, `Next_Off_Window_End`
* **3 Days Yom Tov** (`binary_sensor.yidcal_three_day_yomtov`)
  ON from **candle-lighting** through **Alos** the morning after a continuous Shabbos + Yom Tov block (3+ days of no melacha). Only fires when the block contains both a pure Shabbos (not also YT) and at least one Yom Tov day.
  *Attributes:* `שבת ואח"כ יום טוב` (Shabbos first), `יום טוב ואח"כ שבת` (YT first)

---

## Holiday Sensor attributes list (updated)

* **Holiday Sensor** (`sensor.yidcal_holiday`) with boolean attributes for every holiday, including (all default `false`):

  * א׳ סליחות
  * ערב ראש השנה
  * ראש השנה א׳
  * ראש השנה ב׳
  * ראש השנה א׳ וב׳
  * מוצאי ראש השנה
  * עשרת ימי תשובה
  * צום גדליה
  * שלוש עשרה מדות
  * ערב יום כיפור
  * יום הכיפורים
  * מוצאי יום הכיפורים
  * ערב סוכות
  * סוכות (כל חג)
  * סוכות א׳
  * סוכות ב׳
  * סוכות א׳ וב׳
  * א׳ דחול המועד סוכות
  * ב׳ דחול המועד סוכות
  * ג׳ דחול המועד סוכות
  * ד׳ דחול המועד סוכות
  * חול המועד סוכות
  * שבת חול המועד סוכות
  * הושענא רבה
  * שמיני עצרת
  * שמחת תורה
  * שמיני עצרת/שמחת תורה
  * מוצאי סוכות
  * אסרו חג סוכות
  * ערב חנוכה
  * חנוכה
  * ערב שבת חנוכה
  * שבת חנוכה
  * שבת חנוכה ראש חודש
  * א׳ דחנוכה
  * ב׳ דחנוכה
  * ג׳ דחנוכה
  * ד׳ דחנוכה
  * ה׳ דחנוכה
  * ו׳ דחנוכה
  * ז׳ דחנוכה
  * זאת חנוכה
  * מוצאי חנוכה
  * שובבים
  * שובבים ת"ת
  * צום עשרה בטבת
  * חמשה עשר בשבט
  * תענית אסתר מוקדם
  * שבת ערב פורים
  * תענית אסתר
  * פורים
  * שושן פורים
  * מוצאי שושן פורים
  * ליל בדיקת חמץ
  * ערב פסח מוקדם
  * שבת ערב פסח
  * ערב פסח
  * פסח (כל חג)
  * פסח א׳
  * פסח ב׳
  * פסח א׳ וב׳
  * א׳ דחול המועד פסח
  * ב׳ דחול המועד פסח
  * ג׳ דחול המועד פסח
  * ד׳ דחול המועד פסח
  * חול המועד פסח
  * שבת חול המועד פסח
  * שביעי של פסח
  * אחרון של פסח
  * שביעי/אחרון של פסח
  * מוצאי פסח
  * אסרו חג פסח
  * פסח שני
  * ל"ג בעומר
  * מוצאי ל"ג בעומר
  * ערב שבועות
  * שבועות א׳
  * שבועות ב׳
  * שבועות א׳ וב׳
  * מוצאי שבועות
  * אסרו חג שבועות
  * צום שבעה עשר בתמוז
  * מוצאי צום שבעה עשר בתמוז
  * ערב תשעה באב
  * תשעה באב
  * תשעה באב נדחה
  * מוצאי תשעה באב
  * ט"ו באב
  * יום כיפור קטן
  * ראש חודש
  * שבת ראש חודש
  * ערב שבת
  * ערב יום טוב
  * מוצאי שבת
  * מוצאי יום טוב
  * ערב שבת שחל ביום טוב
  * ערב יום טוב שחל בשבת
  * מוצאי שבת שחל ביום טוב
  * מוצאי יום טוב שחל בשבת

> If enabled in config flow options, many of these attributes can also be exposed as **separate binary sensors** under **YidCal — Holiday Attribute Sensors**.

---

## Erev / Motzi sensors (timing notes)

* **Erev** (`binary_sensor.yidcal_erev`)
  Turns **on** at **Alos** on qualifying Erev days (Erev-Shabbos or weekday Erev-Yom-Tov) and turns **off** at **candle-lighting**.

  **Holiday Erev binary sensors (attribute-derived)**: these specific Erev holiday binaries now start at **Tzeis the night before** (instead of Alos):

  * ערב ראש השנה
  * ערב סוכות
  * ערב פסח מוקדם
  * ערב פסח
  * ערב שבועות

  **Note:** The **Day Type “Erev”** behavior remains **Alos → candle-lighting**.

* **Motzi** (`binary_sensor.yidcal_motzi`)
  Turns on after **Havdalah**, and stays **ON until Alos**.
  *Attribute:* `יקנה"ז` *(added on this Motzi binary sensor — not on the Zman Motzi sensor).*

  **Motzi holiday sensors (attribute-derived) — Shabbos overlap rule:**
  If a holiday’s “motzi moment” is swallowed by Shabbos (example: the holiday ends **Friday night after sunset**), then its **Motzi-holiday sensor is skipped entirely** (you just continue with regular Shabbos → Motzi Shabbos).
  **Exception — major Yom Tov deferral:** For מוצאי ראש השנה, מוצאי סוכות, מוצאי פסח, מוצאי שבועות, and מוצאי יום הכיפורים, if YT ends Friday going into a 3-day block, the motzei sensor **defers to Motzaei Shabbos** (Saturday havdalah → Sunday Alos) instead of being skipped.
  **Exception:** In a **Purim Meshulash** year, `מוצאי שושן פורים` turns on **Sunday Tzeis → Monday Alos**.

---

## Key daily sensors

* **Molad** (`sensor.yidcal_molad` → `friendly` attribute) Full human-friendly Molad string in Yiddish
* **Full Display Sensor** (`sensor.yidcal_full_display`) displays it all in one (e.g פרייטאג פרשת קרח ~ ב׳ ד׳ראש חודש תמוז)
* **Parsha** (`sensor.yidcal_parsha`) weekly Torah portion
* **Rosh Chodesh Today** (`sensor.yidcal_rosh_chodesh_today`) i.e.: `א' ד'ראש חודש שבט` if today (after nightfall) is Rosh Chodesh
* **Perek Avos**: current Perek rendered in אבות פרק ה׳
* **Morid Geshem/Tal Sensor** (`sensor.yidcal_morid_geshem_or_tal`) Indicates when to change the prayer between “Morid HaGeshem”/“Morid HaTal”
* **Tal U’Matar** (`sensor.yidcal_tal_umatar`) Indicates when to change the prayer between “V’sen Tal u’Matar”/“V’sen Beracha”
* **No Music** (`binary_sensor.yidcal_no_music`) Indicates when music is prohibited (e.g., in Sefirah, Three Weeks)
* **Upcoming Shabbos Mevorchim** (`binary_sensor.yidcal_upcoming_shabbos_mevorchim`) `on` if the upcoming Shabbos is Mevorchim
* **Shabbos Mevorchim** (`binary_sensor.yidcal_shabbos_mevorchim`) `on` if today is Shabbos Mevorchim
* **Special Prayer Sensor** (`sensor.yidcal_special_prayer`)

  * Aggregates liturgical insertions (מוריד הגשם/הטל, ותן טל ומטר/ותן ברכה, יעלה ויבוא, על הניסים, עננו, נחם, etc.)
  * **Attribute `הושענות`** – daily Hoshana during Sukkot
  * Adds **פרשת המן** on **ג׳ בשלח**
* **Special Shabbos Sensor** (`sensor.yidcal_special_shabbos`) Special Shabbat names (שבת זכור, שבת נחמו, etc.)
* **Sefirah Counter** (`sensor.yidcal_sefirah_counter`) Day-count of Sefiras HaOmer
* **Sefirah Middos** (`sensor.yidcal_sefirah_counter_middos`) Middos (qualities) of the day in the Omer count
* **Tehilim Daily** (`sensor.yidcal_tehilim_daily`) Five-chapter rotation of Tehilim (e.g. א–ה, ו–ט)
* **Tehilim Daily - Pupa** (`sensor.yidcal_tehilim_daily_pupa`)
* **Date** (`sensor.yidcal_date`) Current Hebrew date in Yiddish (e.g., כ"ה חשון תשפ"ה)
* **Day Label Yiddish** (`sensor.yidcal_day_label_yiddish`) (e.g. זונטאג, מאנטאג, ערש"ק, מוצש"ק)
* **Day Label Hebrew** (`sensor.yidcal_day_label_hebrew`) (e.g. יום א' יום ב)
* **Nine Days** (`binary_sensor.yidcal_nine_days`) turns on Rosh Chodesh Av & turns off 10 Av at Chatzos.
* **Day Type** (`binary_sensor.yidcal_day_type`) Indicates the type of the current day (Any Other Day, Shabbos, Yom Tov, Shabbos & Yom Tov, Erev, Motzi, Fast Day, Chol Hamoed, Shabbos & Chol Hamoed)
* **Longer Shachris** (`binary_sensor.yidcal_longer_shachris`) – ON **4 AM–2 PM local** on **Rosh Chodesh, Chanukah, Chol Hamoed (Pesach/Sukkos), Purim, and Tisha B’Av (incl. nidcheh)**. Always OFF on Shabbos/Yom Tov.

### Hebrew date helper sensors

* **Chodesh** (`sensor.yidcal_chodesh`) — current Hebrew month
* **Yom L’Chodesh** (`sensor.yidcal_yom_lchodesh`) — current Hebrew day-of-month

---

## Special Binary Sensors

* **Slichos** (`binary_sensor.yidcal_slichos`)

  * Continuous Selichos window: turns **on** at **Alef Slichos** Motzaei-Shabbos (havdalah) and stays on until **Erev Yom Kippur** candle-lighting; auto-**off** on any intervening **Shabbos** or **Rosh Hashanah (1–2 Tishrei)**.
  * **Attribute `Selichos_Label`** – Hebrew label for the current Selichos day (e.g., סליחות ליום א׳, סליחות לערב ר״ה, סליחות ליום חמישי מעשי״ת)

* **Eruv Tavshilin** (`binary_sensor.eruv_tavshilin`)
  **On only from Alos to Tzeis** on Erev-Yom-Tov for a Yom Tov thats going into a Shabbos.

* **DST** (`binary_sensor.yidcal_dst`) *(New in v0.5.7)*
  **ON** when the configured timezone is currently observing Daylight Saving Time, **OFF** otherwise. Updates every 60 seconds.
  *Attributes:* `Now`, `UTC_Offset`, `DST_Offset`, `Timezone`

* **Erev After Chatzos** (`binary_sensor.yidcal_erev_after_chatzos`) *(New in v0.5.7)*
  **ON** when all of the following are true: today is Erev Shabbos or Erev Yom Tov (and not itself Shabbos/YT), current time is **after Chatzos HaYom** (midday), and current time is **before** the erev window end (candle-lighting / early start).
  *Attributes:* `Now`, `Is_Erev_Day`, `Chatzos`, `Erev_Window_End`, `Activation_Logic`

* **Season** (`sensor.yidcal_season`) *(New in v0.5.7)*
  State: **"בין פסח לסוכות"** or **"בין סוכות לפסח"** — easy to use in automation triggers/conditions.
  *Boolean attributes:* `Pesach_to_Sukkos`, `Sukkos_to_Pesach`, `Pesach_till_Shvuos`, `Shvuos_till_Rosh_Hashanah`, `After_Shvuos_till_DST_OFF`, `DST_OFF_till_Pesach`, `DST_ON_till_Pesach`, `DST_OFF_till_Chanukah`

* **Longer Shabbos Shachris** (`binary_sensor.yidcal_longer_shabbos_shachris`) *(New in v0.5.7)*
  **ON for the entire Shabbos** (candle-lighting → havdalah) when the davening is longer due to: שבת שקלים/זכור/פרה/החודש, שבת הגדול, שבת ראש חודש, פורים משולש, שבת מברכים, שבת חנוכה, שבת חנוכה ראש חודש, שבת חול המועד סוכות/פסח. Always **OFF** on weekdays (use the existing **Longer Shachris** sensor for weekday scenarios).
  *Attributes:* `Now`, `Window_Start`, `Window_End`, `Reason`, `Activation_Logic`
---

## Day Type (timing notes)

* **Day Type** (`binary_sensor.yidcal_day_type`)
  **Notes:**

  * **Motzi (Day Type)** still turns off at **2:00 AM**.
  * **Minor fast days** show **Fast Day starting at 2:00 AM** (instead of waiting until Alos).

---

## Zmanim

### Zman Erev (Candle Lighting)

* **Zman Erev (Candle Lighting)** (`sensor.yidcal_zman_erev`) — **Next candle-lighting timestamp**

  * **What it shows:** The current day’s candle-lighting for **Shabbos or Yom Tov**.

    * **Erev Shabbos / Erev Yom Tov (weekday):** `sunset − candlelighting_offset`
    * **Between Yom Tov days (Night 2)** and **Motzi Shabbos → Yom Tov:** `sunset + havdalah_offset`
  * **When it updates:**

    * At **local 12:00 AM**, it flips to the lighting for **that civil day** when applicable.
    * If **today has no lighting**, it **holds the most recent lighting** and only jumps forward at **the first midnight after Motzi** (the day right after Shabbos or the final Yom-Tov day).
    * During **Shabbos/Yom-Tov day**, it keeps showing **yesterday’s lighting** until midnight (prevents jumping mid-day).
  * **Attributes:**

    * `Zman_Erev_With_Seconds` – ISO local time (unrounded)
    * `Zman_Erev_Simple` – HH:MM (local)
    * `City`, `Latitude`, `Longitude`
    * **Static “Day” rows for Shabbos↔Yom-Tov clusters** (always present; empty when not applicable):

      * `Day_1_Label`, `Day_1_Simple`
      * `Day_2_Label`, `Day_2_Simple`
      * `Day_3_Label`, `Day_3_Simple`

### Zman Motzi (Havdalah)

* **Zman Motzi (Havdalah)** (`sensor.yidcal_zman_motzi`) — **Next havdalah timestamp**

  * **What it shows:** The **earliest** of:

    1. **End of the next Yom-Tov span**: `sunset(last day) + havdalah_offset`
    2. **Next Motzi Shabbos**: `sunset(Saturday) + havdalah_offset`
  * **When it updates:**

    * Holds **tonight’s havdalah** and then rolls at **local 12:00 AM** after that night.
  * **Attributes:**

    * `Zman_Motzi_With_Seconds` – ISO local time (unrounded)
    * `Zman_Motzi_Simple` – HH:MM (local)
    * `City`, `Latitude`, `Longitude`

### Zmanim device sensors

* **Zman Talis & Tefilin** (`sensor.yidcal_zman_tallis_tefilin`) – Misheyakir: Alos HaShachar + configured offset
* **Sof Zman Krias Shma (MGA)** (`sensor.yidcal_sof_zman_krias_shma_mga`) – end-of-Shema, Magen Avraham
* **Sof Zman Krias Shma (GRA)** (`sensor.yidcal_sof_zman_krias_shma_gra`) – end-of-Shema, Vilna Ga’on
* **Sof Zman Tefilah (MGA)** (`sensor.yidcal_sof_zman_tefilah_mga`) – end-of-prayer, Magen Avraham
* **Sof Zman Tefilah (GRA)** (`sensor.yidcal_sof_zman_tefilah_gra`) – end-of-prayer, Vilna Ga’on
* **Zman Netz HaChamah** (`sensor.yidcal_netz`) – sunrise
* **Zman Alos HaShachar** (`sensor.yidcal_alos`) – dawn
* **Zman Chatzos** (`sensor.yidcal_chatzos_hayom`) – halakhic midday
* **Zman Shkiat HaChamah** (`sensor.yidcal_shkia`) – sunset
* **Zman Maariv +60m** (`sensor.yidcal_zman_maariv_60`) – 60 min after sunset
* **Zman Maariv R"T** (`sensor.yidcal_zman_maariv_rt`) – 72 min after sunset
* **Zman Tzies Hakochavim** (`sensor.yidcal_tzies_hakochavim`) – stars emergence (sunset + havdalah_offset)
* **Zman Mincha Gedola** (`sensor.yidcal_mincha_gedola`) – earliest Mincha
* **Zman Mincha Ketana** (`sensor.yidcal_mincha_ketana`) – preferred Mincha
* **Zman Chatzos HaLaila** (`sensor.yidcal_chatzos_haleila`) – midnight of night
* **Plag HaMincha (MGA)** (`sensor.yidcal_plag_hamincha`) *(friendly name changed only)*
* **Plag HaMincha (GRA)** (`sensor.yidcal_plag_hamincha_gra`) *(new)*

> Note: You may see some sensors with “Simple” attributes (Today/Tomorrow/Yesterday). Those are affected by the **Simple Zmanim time format** option below.

### Simple Zmanim time format (new)

You can choose a time format in config flow options: **12-hour (AM/PM)** or **24-hour**.
This affects only the **Simple** attributes (e.g., `Alos Simple`, `Tomorrows Simple`, `Yesterdays Simple`).
ISO/With-Seconds timestamps remain controlled by Home Assistant’s global date/time display settings.

---

## Upcoming sensors

* **Upcoming Yom Tov Sensor** (`binary_sensor.yidcal_upcoming_yomtov`)

  * **Attributes:** `Next_Holiday`, `Date`, `Next_On`
  * **ON:** **12:00 AM** after the latest Motzi (Shabbos or Yom Tov), leading into the next target
  * **OFF:** at **candle-lighting** of the target’s erev (sunset − offset)
* **Upcoming Holiday Sensor** (`sensor.yidcal_upcoming_holiday`)

  * **Exposes:** all holiday flags as booleans (True/False)
  * **State:** readable list of active upcoming labels
  * **Behavior:** pre-activates up to your lookahead days; updates nightly at **12:02 AM**; honors offsets

---

## Display device extras

### Krias HaTorah Sensor (New in v0.5.4)

* **Krias HaTorah** (`sensor.yidcal_krias_hatorah`) — summarizes today’s (or the next) קריאת התורה: how many ס"ת, which parsha(s) and reasons (Shabbos, Yom Tov, fast day, חנוכה, ראש חודש, נשיאים בניסן, etc.), with separate attributes for שחרית / מנחה / ערבית.
* **Minhag toggles (config flow):**
  * `?ליינט מען קרבנות אום שלוש עשרה מדות` — optionally add **קרבנות** at מנחה on שלוש עשרה מדות days.
  * `?ליינט מען משנה תורה הושענא רבה ביינאכט` — optionally add **משנה תורה** reading for הושענא רבה by night.
  * Both default to **false**, so you can enable them only if it matches your shul’s minhag.


### Haftorah Sensor

* **Haftorah** (`sensor.yidcal_haftorah`) — Haftarah reading for the relevant Shabbos (including special Haftaros when applicable).
* **Minhag selection:** controlled by the config-flow option **`הפטרה סענסאר מנהג`**:
  * אשכנזי (default)
  * ספרדי

### Fast countdown sensors

* **Fast Starts In** (`sensor.yidcal_fast_starts_in`)
  Countdown timer for when a fast begins.
  **Note:** This sensor starts showing a value **6 hours before a fast starts**.
* **Fast Ends In** (`sensor.yidcal_fast_ends_in`)
  Countdown timer for when a fast ends.

*(Both are under the **YidCal — Display** device.)*

### Friday Is Rosh Chodesh

Shows the reminder: **“שניידן די נעגל, האר היינט לכבוד שבת”** when applicable:

* If upcoming Rosh Chodesh is **Fri (1 day)** or **Fri/Sat (2 days)** → shows on **Thursday**
* If upcoming Rosh Chodesh is **Thu/Fri (2 days)** → shows on **Wednesday**

### Daf HaYomi (New in v0.5.7)

* **Daf HaYomi** (`sensor.yidcal_daf_hayomi`) — Today's Daf Yomi page, computed from the standard 2,711-day cycle (14th cycle started Jan 5, 2020). **Enabled by default** — can be toggled via config flow.
  State: `"ברכות דף ב׳"` (masechta + daf in Hebrew numerals).
  *Attributes:* `Masechta`, `Masechta_English`, `Daf`, `Daf_Hebrew`, `Cycle_Number`, `Day_In_Cycle`

### Amud HaYomi (New in v0.5.7)

* **Amud HaYomi** (`sensor.yidcal_amud_hayomi`) — Today's Amud HaYomi (Dirshu cycle), one amud per day, 7 days/week. Cycle 1 started October 15, 2023 (1 Cheshvan 5784) with Berachos 2a. Always enabled (no config toggle).
  State: `"פסחים דף ק״ג עמוד ב"` (masechta + daf + amud side).
  *Attributes:* `Masechta`, `Masechta_English`, `Daf`, `Daf_Hebrew`, `Amud` (א/ב), `Amud_English` (a/b), `Cycle_Number`, `Day_In_Cycle`

---

## Yurtzeit sensors

* **Yurtzeit Sensor** (`sensor.yidcal_yurtzeit`) - Displays today's yurtzeits (flipping at sunset + havdalah_offset), with attributes for each yurtzeit name.
* **Yurtzeit Weekly Sensor** (`sensor.yidcal_yurtzeits_weekly`) - Displays weekly yurtzeits (flipping at Saturday sunset + havdalah_offset), with attributes for each day's yurtzeits.

### Yurtzeit setup (config flow)

During setup, you can choose to add:

* **Daily**, **Weekly**, **Both**, or **None**

If you enable **any** Yurtzeit sensor, you must select a **database**. You can run daily+weekly using either database, or both.

---

## Translations (config-flow only)

* `he.json` – עברית
* `en.json` – Yiddish
* `en-GB.json` – English

*(Currently only for config flow options.)*

---

## Location Resolution

To ensure you calculate sunrise/sunset on the correct center of your municipality for the Zmanim Sensors (and fix boroughs like Brooklyn in NYC):

1. **Reverse lookup** your HA’s latitude/longitude via Nominatim (OSM) to pull the OSM “city” or—if in New York City—the `city_district` (Brooklyn, Queens, etc.).
2. **Forward geocode** only `"City, State"` (no ZIP, no bias) via Nominatim’s `geocode(exactly_one=True)` to snap to the official polygon centroid.
3. Use **TimezoneFinder** to resolve your timezone from the final lat/lon.

---

## Configuration Options

After adding the integration via UI, go to **Settings → Devices & Services → YidCal → Options**:

| Option                                                     | Default     | Description                                                                                                                             |
| ---------------------------------------------------------- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `אויב איר זענט אין ארץ ישראל, צייכנט דאס`                  | `false`     | Enable **Israel** rules (experimental / not fully tested).                                                                              |
| `וויפיל מינוט פארן שקיעה איז הדלקת הנרות`                  | `15`        | Minutes before sunset for Erev Shabbos / Yom-Tov candle-lighting (**Israel users usually set 30–40**).                                  |
| `וויפיל מינוט נאכן שקיעה איז מוצאי`                        | `72`        | Minutes after sunset for Motzaei Shabbos / Yom-Tov havdalah (**adjust for local minhag**).                                              |
| `וויפיל מינוט נאכן עלות איז טלית ותפילין`                  | `22`        | Minutes after **Alos HaShachar** for Talis & Tefilin (Misheyakir).                                                                      |
| `נעם אראפ די נְקֻודּוֹת`                                   | `false`     | Remove Hebrew vowel-points (nikud) from Omer text.                                                                                      |
| `צולייגען באזונדערע סענסאָרס פאר די ימים טובים`            | `true`      | Add/remove individual binary sensors for each holiday (holidays always remain as attributes).                                           |
| `Full Display Sensor וויזוי דו ווילסט זעהן דעם טאג ביי די` | `yiddish`   | Choose Yiddish (`זונטאג, מאנטאג`) or Hebrew (`יום א׳, יום ב׳`) day labels.                                                              |
| `צייט־פארמאט (נאר פאר Simple Zmanim)`                      | `12-hour`   | Format for **Simple** Zmanim attributes only: **12-hour (AM/PM)** or **24-hour**.                                                       |
| `ווען זאל זיך די סליחות טאג טוישן`                         | `זמן הבדלה` | When the Selichos label advances: `havdalah` (after sunset + offset) or `midnight` (12 AM).                                             |
| `Upcoming Holiday Sensor וויפיל טעג פאראויס זאל קוקן די`   | `2`         | How many **halachic days** ahead Upcoming Holiday pre-activates (range **1–14**). Updates nightly at **12:02 AM** and respects offsets. |
| `הפטרה סענסאר מנהג`                                        | `אשכנזי`    | Choose the minhag used for the Haftorah sensor: `אשכנזי` or `ספרדי`.                                                                   |
| `?ליינט מען קרבנות אום שלוש עשרה מדות`                     | `false`     | Include **קרבנות** in the Krias HaTorah sensor at מנחה on **שלוש עשרה מדות** days if your shul leins it from the בימה.                |
| `?ליינט מען משנה תורה הושענא רבה ביינאכט`                  | `false`     | Include **משנה תורה** in the Krias HaTorah sensor for **הושענא רבה ביינאכט** if your minhag is to lein it (not just say it privately). |

> ⚠️ **Important:** If you previously enabled separate holiday binary sensors and later disable them in Options, those entities will **not** auto-delete. Remove them manually via **Settings → Entities**, or delete and re-add the integration with the option turned off.

---

## Early Shabbos & Early Yom Tov (New) 
> Wasn't fully tested. If anything needs corrections, please report a GitHub issue.

YidCal can calculate an **earlier “start time”** for entering Shabbos or certain Yomim Tovim, based on either **Plag HaMincha** or a **fixed clock time**.

> **Setup note:** Early Shabbos / Early Yom Tov cannot be configured during the initial integration setup.
> After YidCal is installed, go to **Settings → Devices & Services → Integrations → YidCal**, then click the **Settings (gear) icon** to configure it under **Options**.

### What this feature provides

When enabled, YidCal adds:

* **Select: Early Shabbos Override** (`select.yidcal_early_shabbos_override`)
* **Select: Early Yom Tov Override** (`select.yidcal_early_yomtov_override`)
* **Sensor: Early Shabbos/Yom Tov Start Time** (`sensor.yidcal_early_shabbos_yt_start_time`)

The sensor state is a timestamp representing the **next effective start time**, and includes rich attributes explaining *why* that time was chosen.

### Override selects (runtime controls)

Each select supports:

* `Auto`
* `Force early`
* `Force regular`

### Sensor attributes (high-level)

`sensor.yidcal_early_shabbos_yt_start_time` includes:

* `effective_shabbos_start_by_date` / `effective_yomtov_start_by_date`
* `early_shabbos_override`, `early_yomtov_override`
* `next_effective_start_kind`
* `next_effective_start_description` and `summary`

### Integration behavior with Erev / Day Type / No Melucha

When enabled, the **effective start time** becomes the source of truth for:

* when **Erev** turns **off**
* when **Day Type** flips to **Shabbos / Yom Tov**
* when **No Melucha** begins

---

## Yurtzeit Customization

The Yurtzeit sensor pulls names from a GitHub-hosted JSON file by default. You can add custom names or mute existing ones using text files in your Home Assistant config directory.

Upon installation or restart, the integration automatically creates a `/config/www/yidcal-data/` folder with two sample files:

* `custom_yahrtzeits.txt`: For adding your own Yurtzeit names.
* `muted_yahrtzeits.txt`: For hiding specific names from the sensor.

### Editing Instructions

1. **Locate the Files**: Use HA's File Editor add-on, SSH, or a file transfer tool (e.g., FileZilla) to access:

   * `/config/www/yidcal-data/custom_yahrtzeits.txt`
   * `/config/www/yidcal-data/muted_yahrtzeits.txt`

2. **Custom Yurtzeits (Add Names)**:

   * Format: `Hebrew Date: Full Name` (one per line).
   * Example:

     ```
     ט"ו תמוז: רבי פלוני בן רבי אלמוני זי"ע [מחבר ספר דוגמא] תש"א
     י"ז תמוז: רבי דוגמא בן רבי משל זי"ע תרצ"ב
     ```
   * Custom names are **added** to the existing GitHub names for that date.
   * Comment out lines with `#` to ignore them.

3. **Muted Yurtzeits (Hide Names)**:

   * Format: Full **exact** name (one per line, no date needed).
   * Example:

     ```
     רבי פלוני בן רבי אלמוני זי"ע [מחבר ספר דוגמא] תש"א
     רבי דוגמא בן רבי משל זי"ע תרצ"ב
     ```
   * Muted names are hidden globally (from both GitHub and custom lists).
   * Comment out lines with `#` to ignore them.

4. **Save and Restart**: After editing, save the files and restart Home Assistant. Changes load only on restart.

5. **Tips**:

   * Files support Hebrew/UTF-8; use a text editor that handles it well.
   * Invalid lines are silently skipped.
   * If files don't exist, restart HA to regenerate samples.

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

## Lovelace Example For Fast Starts/Ends In Countdown Timers

```yaml
type: conditional
conditions:
  - condition: or
    conditions:
      - condition: state
        entity: sensor.yidcal_fast_starts_in
        state_not: ""
      - condition: state
        entity: sensor.yidcal_fast_ends_in
        state_not: ""
card:
  type: horizontal-stack
  cards:
    - type: markdown
      content: >
        {% set start = states('sensor.yidcal_fast_starts_in') %}
        {% set end = states('sensor.yidcal_fast_ends_in') %}

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
```

> *By Yoel Goldstein / Vaayer LLC*

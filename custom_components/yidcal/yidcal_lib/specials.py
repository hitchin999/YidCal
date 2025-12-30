from datetime import date, timedelta
from pyluach import dates, parshios, hebrewcal

def _upcoming_shabbos(g: date) -> date:
    """Return the upcoming Shabbat (Saturday) for a Gregorian date g (inclusive)."""
    wd = g.weekday()               # Mon=0 ... Sat=5, Sun=6
    delta = (5 - wd) % 7
    return g + timedelta(days=delta)

def _month_length_safe(y: int, m: int) -> int:
    """Return 29 or 30 safely without constructing invalid HebrewDate(…, 30) on 29-day months."""
    try:
        dates.HebrewDate(y, m, 30)   # will raise if month has only 29
        return 30
    except Exception:
        return 29

def get_special_shabbos_name(today: date | dates.GregorianDate | dates.HebrewDate | None = None,
                             is_in_israel: bool = False) -> str:
    # --- normalize 'today' into a Python date ---
    if today is None:
        today_date = date.today()
    elif isinstance(today, date):
        today_date = today
    else:
        if isinstance(today, dates.GregorianDate):
            today_date = today.to_pydate()
        elif isinstance(today, dates.HebrewDate):
            today_date = today.to_greg().to_pydate()
        else:
            raise ValueError("Unsupported date type for 'today'")

    # --- ALWAYS compute against the actual Shabbos we're evaluating ---
    shabbat_date = _upcoming_shabbos(today_date)
    greg_shabbat = dates.GregorianDate.from_pydate(shabbat_date)
    shabbat_heb = greg_shabbat.to_heb()

    events = []
    Y = shabbat_heb.year

    # -----------------------------
    # Four Parshiyos / seasonal
    # -----------------------------
    adar_month = 13 if hebrewcal.Year(Y).leap else 12

    # Shabbos Shekalim: before/around RC Adar
    rc_adar = dates.HebrewDate(Y, adar_month, 1).to_pydate()
    if 0 <= (rc_adar - shabbat_date).days <= 6:
        events.append("שבת שקלים")

    # Shabbos Zachor: before Purim (Adar 14)
    purim = dates.HebrewDate(Y, adar_month, 14).to_pydate()
    if 1 <= (purim - shabbat_date).days <= 6:
        events.append("שבת זכור")

    # Shabbos HaChodesh: before/around RC Nissan
    rc_nisan = dates.HebrewDate(Y, 1, 1).to_pydate()
    if 0 <= (rc_nisan - shabbat_date).days <= 6:
        events.append("שבת החודש")

    # Shabbos Parah: the Shabbos before HaChodesh if not already HaChodesh
    # (look-ahead one week)
    next_week_date = shabbat_date + timedelta(days=7)
    rc_nisan2 = dates.HebrewDate(dates.GregorianDate.from_pydate(next_week_date).to_heb().year, 1, 1).to_pydate()
    if "שבת החודש" not in events and 0 <= (rc_nisan2 - next_week_date).days <= 6:
        events.append("שבת פרה")

    # Shabbos HaGadol: before Pesach (Nissan 15)
    pesach = dates.HebrewDate(Y, 1, 15).to_pydate()
    if 0 < (pesach - shabbat_date).days <= 8:
        events.append("שבת הגדול")

    # Shabbos Shuvah: between R"H and YK (Tishrei 3–9)
    if shabbat_heb.month == 7 and 3 <= shabbat_heb.day <= 9:
        events.append("שבת שובה")

    # Shabbos Chazon: Shabbos before Tisha B'Av (Av 9 within coming week)
    tisha_bav = dates.HebrewDate(Y, 5, 9).to_pydate()
    if 0 <= (tisha_bav - shabbat_date).days <= 6:
        events.append("שבת חזון")

    # Shabbos Nachamu: Shabbos after Tisha B'Av (Av 10–16)
    if shabbat_heb.month == 5 and 10 <= shabbat_heb.day <= 16:
        events.append("שבת נחמו")

    # Shabbos Chazak: Vayechi/Bechukosai/Masei/V'zos HaBracha endings
    # Use EY/Chul cycle
    parsha_indices = parshios.getparsha(greg_shabbat, israel=is_in_israel)
    chazak_ports = {11, 22, 32, 42}
    if parsha_indices and any(idx in chazak_ports for idx in parsha_indices):
        events.append("שבת חזק")
        
    # Shabbos Shirah: Parshas Beshalach
    if parsha_indices and 15 in parsha_indices:
        events.append("שבת שירה")

    # Purim Meshulash (show everywhere): when the evaluated Shabbos is 15 Adar (or 15 Adar II in a leap year)
    if shabbat_heb.month == adar_month and shabbat_heb.day == 15:
        events.append("פורים משולש")

    # -----------------------------
    # Shabbos Rosh Chodesh (not in Tishrei)
    # -----------------------------
    if shabbat_heb.month != 7:
        length_cur = _month_length_safe(shabbat_heb.year, shabbat_heb.month)
        if shabbat_heb.day == 1 or (shabbat_heb.day == 30 and length_cur == 30):
            events.append("שבת ראש חודש")

    # -----------------------------
    # Shabbos Mevorchim (skip Tishrei)
    # -----------------------------
    # Next month/year
    if shabbat_heb.month == 13 or (shabbat_heb.month == 12 and not hebrewcal.Year(shabbat_heb.year).leap):
        next_month_num = 1
        next_month_year = shabbat_heb.year + 1
    else:
        next_month_num = shabbat_heb.month + 1
        next_month_year = shabbat_heb.year

    if next_month_num != 7:  # skip Mevorchim for Tishrei
        # Earliest RC date for the *upcoming* month (30th of current, if exists, and 1st of next)
        rc_gdays = []
        length_cur = _month_length_safe(shabbat_heb.year, shabbat_heb.month)
        if length_cur == 30:
            rc_gdays.append(dates.HebrewDate(shabbat_heb.year, shabbat_heb.month, 30).to_pydate())
        rc_gdays.append(dates.HebrewDate(next_month_year, next_month_num, 1).to_pydate())

        first_rc = min(rc_gdays)  # earliest RC day (Gregorian date)
        first_wd = first_rc.weekday()  # Mon=0 … Sat=5, Sun=6

        if first_wd == 5:
            mevorchim_date = first_rc - timedelta(days=7)
        else:
            days_back = (first_wd - 5) % 7
            mevorchim_date = first_rc - timedelta(days=days_back)

        if shabbat_date == mevorchim_date:
            # Month label in Hebrew
            if next_month_num == 12:
                month_name = "אדר א׳" if hebrewcal.Year(next_month_year).leap else "אדר"
            elif next_month_num == 13:
                month_name = "אדר ב׳"
            else:
                month_names = {
                    1: "ניסן",  2: "אייר",   3: "סיון",
                    4: "תמוז",  5: "אב",    6: "אלול",
                    7: "תשרי",  8: "חשון",  9: "כסלו",
                    10: "טבת",  11: "שבט",
                }
                month_name = month_names.get(next_month_num, "")
            if month_name:
                events.append(f"מברכים חודש {month_name}")

    # Return a single string with ASCII hyphen (the sensor now splits both)
    return "-".join(events) if events else ""

from datetime import date, timedelta
from pyluach import dates, parshios, hebrewcal

def get_special_shabbos_name(today: date = None) -> str:
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

    greg_today = dates.GregorianDate.from_pydate(today_date)
    next_shabbat = greg_today.shabbos()
    shabbat_date = next_shabbat.to_pydate()
    shabbat_heb = next_shabbat.to_heb()

    events = []
    Y = shabbat_heb.year

    adar_month = 13 if hebrewcal.Year(Y).leap else 12
    rc_adar = dates.HebrewDate(Y, adar_month, 1).to_pydate()
    delta_days = (rc_adar - shabbat_date).days
    if 0 <= delta_days <= 6:
        events.append("שבת שקלים")

    purim = dates.HebrewDate(Y, adar_month, 14).to_pydate()
    delta_days = (purim - shabbat_date).days
    if 1 <= delta_days <= 6:
        events.append("שבת זכור")

    rc_nisan = dates.HebrewDate(Y, 1, 1).to_pydate()
    delta_days = (rc_nisan - shabbat_date).days
    if 0 <= delta_days <= 6:
        events.append("שבת החודש")

    next_week_date = shabbat_date + timedelta(days=7)
    next_shabbat2 = dates.GregorianDate.from_pydate(next_week_date)
    next_shabbat2_heb = next_shabbat2.to_heb()
    rc_nisan2 = dates.HebrewDate(next_shabbat2_heb.year, 1, 1).to_pydate()
    delta_next = (rc_nisan2 - next_week_date).days
    if 0 <= delta_next <= 6 and "שבת החודש" not in events:
        events.append("שבת פרה")

    pesach = dates.HebrewDate(Y, 1, 15).to_pydate()
    delta_days = (pesach - shabbat_date).days
    if 0 < delta_days <= 8:
        events.append("שבת הגדול")

    if shabbat_heb.month == 7 and 3 <= shabbat_heb.day <= 9:
        events.append("שבת שובה")

    tisha_bav = dates.HebrewDate(Y, 5, 9).to_pydate()
    delta_days = (tisha_bav - shabbat_date).days
    if 0 <= delta_days <= 6:
        events.append("שבת חזון")

    if shabbat_heb.month == 5 and 10 <= shabbat_heb.day <= 16:
        events.append("שבת נחמו")

    parsha_indices = parshios.getparsha(next_shabbat)
    chazak_ports = {11, 22, 32, 42}
    if parsha_indices and any(idx in chazak_ports for idx in parsha_indices):
        events.append("שבת חזק")

    if ((not hebrewcal.Year(Y).leap and shabbat_heb.month == 12 and shabbat_heb.day == 15) or
        (hebrewcal.Year(Y).leap and shabbat_heb.month == 13 and shabbat_heb.day == 15)):
        events.append("פורים משולש")

    # Add check for שבת ראש חודש
    if shabbat_heb.month != 7:  # Exclude for Tishrei (Rosh Hashanah)
        month_length = 30 if dates.HebrewDate(shabbat_heb.year, shabbat_heb.month, 30).day == 30 else 29
        if shabbat_heb.day == 1 or (shabbat_heb.day == 30 and month_length == 30):
            events.append("שבת ראש חודש")

    if shabbat_heb.month == 13 or (shabbat_heb.month == 12 and not hebrewcal.Year(shabbat_heb.year).leap):
        next_month_num = 1
        next_month_year = shabbat_heb.year + 1
    else:
        next_month_num = shabbat_heb.month + 1
        next_month_year = shabbat_heb.year

    next_rc_date = dates.HebrewDate(next_month_year, next_month_num, 1).to_pydate()
    delta_days = (next_rc_date - shabbat_date).days
    if 1 <= delta_days <= 7 and next_month_num != 7:
        if next_month_num == 12:
            month_name = "אדר א׳" if hebrewcal.Year(next_month_year).leap else "אדר"
        elif next_month_num == 13:
            month_name = "אדר ב׳"
        else:
            month_names = {
                1: "ניסן",  2: "אייר",   3: "סיון",
                4: "תמוז",  5: "אב",    6: "אלול",
                7: "תשרי",  8: "חשון", 9: "כסלו",
                10: "טבת", 11: "שבט"
            }
            month_name = month_names.get(next_month_num, "")
        if month_name:
            events.append(f"מברכים חודש {month_name}")

    return "-".join(events) if events else ""

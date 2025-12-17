# krias_hatorah_data.py
"""
Comprehensive Torah Reading Data for YidCal Integration.
All readings use consistent sifrei_torah structure.

Each sefer in sifrei_torah has:
- sefer_number: 1, 2, or 3
- opening_words: first words of reading
- sefer: בראשית/שמות/ויקרא/במדבר/דברים
- parsha_source: which parsha (used for scroll anchor)
- reason: why this sefer
- aliyos: which aliyos
"""

# ══════════════════════════════════════════════════════════════════════════════
# WEEKLY PARSHIYOT - Now with BOTH Hebrew and English keys
# ══════════════════════════════════════════════════════════════════════════════

PARSHIYOT: dict[str, dict] = {
    # BEREISHIS - Hebrew keys
    "בראשית": {"opening_words": "בראשית ברא אלקים את השמים ואת הארץ", "sefer": "בראשית", "english": "Bereishis", "order": 1},
    "נח": {"opening_words": "אלה תולדות נח נח איש צדיק", "sefer": "בראשית", "english": "Noach", "order": 2},
    "לך לך": {"opening_words": "ויאמר ה׳ אל אברם לך לך מארצך", "sefer": "בראשית", "english": "Lech Lecha", "order": 3},
    "וירא": {"opening_words": "וירא אליו ה׳ באלוני ממרא", "sefer": "בראשית", "english": "Vayeira", "order": 4},
    "חיי שרה": {"opening_words": "ויהיו חיי שרה מאה שנה", "sefer": "בראשית", "english": "Chayei Sarah", "order": 5},
    "תולדות": {"opening_words": "ואלה תולדות יצחק בן אברהם", "sefer": "בראשית", "english": "Toldos", "order": 6},
    "ויצא": {"opening_words": "ויצא יעקב מבאר שבע וילך חרנה", "sefer": "בראשית", "english": "Vayeitzei", "order": 7},
    "וישלח": {"opening_words": "וישלח יעקב מלאכים לפניו", "sefer": "בראשית", "english": "Vayishlach", "order": 8},
    "וישב": {"opening_words": "וישב יעקב בארץ מגורי אביו", "sefer": "בראשית", "english": "Vayeishev", "order": 9},
    "מקץ": {"opening_words": "ויהי מקץ שנתים ימים ופרעה חולם", "sefer": "בראשית", "english": "Mikeitz", "order": 10},
    "ויגש": {"opening_words": "ויגש אליו יהודה ויאמר בי אדוני", "sefer": "בראשית", "english": "Vayigash", "order": 11},
    "ויחי": {"opening_words": "ויחי יעקב בארץ מצרים שבע עשרה שנה", "sefer": "בראשית", "english": "Vayechi", "order": 12},
    # SHEMOS
    "שמות": {"opening_words": "ואלה שמות בני ישראל הבאים מצרימה", "sefer": "שמות", "english": "Shemos", "order": 13},
    "וארא": {"opening_words": "וידבר אלקים אל משה ויאמר אליו אני ה׳", "sefer": "שמות", "english": "Va'eira", "order": 14},
    "בא": {"opening_words": "ויאמר ה׳ אל משה בא אל פרעה", "sefer": "שמות", "english": "Bo", "order": 15},
    "בשלח": {"opening_words": "ויהי בשלח פרעה את העם", "sefer": "שמות", "english": "Beshalach", "order": 16},
    "יתרו": {"opening_words": "וישמע יתרו כהן מדין חותן משה", "sefer": "שמות", "english": "Yisro", "order": 17},
    "משפטים": {"opening_words": "ואלה המשפטים אשר תשים לפניהם", "sefer": "שמות", "english": "Mishpatim", "order": 18},
    "תרומה": {"opening_words": "וידבר ה׳ אל משה לאמר דבר אל בני ישראל ויקחו לי תרומה", "sefer": "שמות", "english": "Terumah", "order": 19},
    "תצוה": {"opening_words": "ואתה תצוה את בני ישראל", "sefer": "שמות", "english": "Tetzaveh", "order": 20},
    "כי תשא": {"opening_words": "כי תשא את ראש בני ישראל", "sefer": "שמות", "english": "Ki Sisa", "order": 21},
    "ויקהל": {"opening_words": "ויקהל משה את כל עדת בני ישראל", "sefer": "שמות", "english": "Vayakhel", "order": 22},
    "פקודי": {"opening_words": "אלה פקודי המשכן משכן העדות", "sefer": "שמות", "english": "Pekudei", "order": 23},
    "ויקהל-פקודי": {"opening_words": "ויקהל משה את כל עדת בני ישראל", "sefer": "שמות", "english": "Vayakhel-Pekudei", "order": 22},
    # VAYIKRA
    "ויקרא": {"opening_words": "ויקרא אל משה וידבר ה׳ אליו", "sefer": "ויקרא", "english": "Vayikra", "order": 24},
    "צו": {"opening_words": "צו את אהרן ואת בניו לאמר", "sefer": "ויקרא", "english": "Tzav", "order": 25},
    "שמיני": {"opening_words": "ויהי ביום השמיני קרא משה", "sefer": "ויקרא", "english": "Shemini", "order": 26},
    "תזריע": {"opening_words": "אשה כי תזריע וילדה זכר", "sefer": "ויקרא", "english": "Tazria", "order": 27},
    "מצורע": {"opening_words": "זאת תהיה תורת המצורע ביום טהרתו", "sefer": "ויקרא", "english": "Metzora", "order": 28},
    "תזריע-מצורע": {"opening_words": "אשה כי תזריע וילדה זכר", "sefer": "ויקרא", "english": "Tazria-Metzora", "order": 27},
    "אחרי מות": {"opening_words": "אחרי מות שני בני אהרן", "sefer": "ויקרא", "english": "Acharei Mos", "order": 29},
    "קדושים": {"opening_words": "דבר אל כל עדת בני ישראל קדושים תהיו", "sefer": "ויקרא", "english": "Kedoshim", "order": 30},
    "אחרי מות-קדושים": {"opening_words": "אחרי מות שני בני אהרן", "sefer": "ויקרא", "english": "Acharei Mos-Kedoshim", "order": 29},
    "אמור": {"opening_words": "ויאמר ה׳ אל משה אמור אל הכהנים", "sefer": "ויקרא", "english": "Emor", "order": 31},
    "בהר": {"opening_words": "וידבר ה׳ אל משה בהר סיני לאמר", "sefer": "ויקרא", "english": "Behar", "order": 32},
    "בחוקותי": {"opening_words": "אם בחוקותי תלכו ואת מצותי תשמרו", "sefer": "ויקרא", "english": "Bechukosai", "order": 33},
    "בהר-בחוקותי": {"opening_words": "וידבר ה׳ אל משה בהר סיני לאמר", "sefer": "ויקרא", "english": "Behar-Bechukosai", "order": 32},
    # BAMIDBAR
    "במדבר": {"opening_words": "וידבר ה׳ אל משה במדבר סיני", "sefer": "במדבר", "english": "Bamidbar", "order": 34},
    "נשא": {"opening_words": "נשא את ראש בני גרשון", "sefer": "במדבר", "english": "Naso", "order": 35},
    "בהעלותך": {"opening_words": "בהעלותך את הנרות אל מול פני המנורה", "sefer": "במדבר", "english": "Beha'aloscha", "order": 36},
    "שלח": {"opening_words": "שלח לך אנשים ויתורו את ארץ כנען", "sefer": "במדבר", "english": "Shelach", "order": 37},
    "קרח": {"opening_words": "ויקח קרח בן יצהר בן קהת", "sefer": "במדבר", "english": "Korach", "order": 38},
    "חוקת": {"opening_words": "זאת חוקת התורה אשר צוה ה׳", "sefer": "במדבר", "english": "Chukas", "order": 39},
    "בלק": {"opening_words": "וירא בלק בן צפור את כל אשר עשה ישראל", "sefer": "במדבר", "english": "Balak", "order": 40},
    "חוקת-בלק": {"opening_words": "זאת חוקת התורה אשר צוה ה׳", "sefer": "במדבר", "english": "Chukas-Balak", "order": 39},
    "פינחס": {"opening_words": "פינחס בן אלעזר בן אהרן הכהן", "sefer": "במדבר", "english": "Pinchas", "order": 41},
    "מטות": {"opening_words": "וידבר משה אל ראשי המטות לבני ישראל", "sefer": "במדבר", "english": "Mattos", "order": 42},
    "מסעי": {"opening_words": "אלה מסעי בני ישראל אשר יצאו", "sefer": "במדבר", "english": "Masei", "order": 43},
    "מטות-מסעי": {"opening_words": "וידבר משה אל ראשי המטות לבני ישראל", "sefer": "במדבר", "english": "Mattos-Masei", "order": 42},
    # DEVARIM
    "דברים": {"opening_words": "אלה הדברים אשר דבר משה", "sefer": "דברים", "english": "Devarim", "order": 44},
    "ואתחנן": {"opening_words": "ואתחנן אל ה׳ בעת ההיא לאמר", "sefer": "דברים", "english": "Va'eschanan", "order": 45},
    "עקב": {"opening_words": "והיה עקב תשמעון את המשפטים", "sefer": "דברים", "english": "Eikev", "order": 46},
    "ראה": {"opening_words": "ראה אנכי נותן לפניכם היום ברכה", "sefer": "דברים", "english": "Re'eh", "order": 47},
    "שופטים": {"opening_words": "שופטים ושוטרים תתן לך בכל שעריך", "sefer": "דברים", "english": "Shoftim", "order": 48},
    "כי תצא": {"opening_words": "כי תצא למלחמה על אויביך", "sefer": "דברים", "english": "Ki Seitzei", "order": 49},
    "כי תבוא": {"opening_words": "והיה כי תבוא אל הארץ", "sefer": "דברים", "english": "Ki Savo", "order": 50},
    "נצבים": {"opening_words": "אתם נצבים היום כולכם לפני ה׳", "sefer": "דברים", "english": "Nitzavim", "order": 51},
    "וילך": {"opening_words": "וילך משה וידבר את הדברים האלה", "sefer": "דברים", "english": "Vayeilech", "order": 52},
    "נצבים-וילך": {"opening_words": "אתם נצבים היום כולכם לפני ה׳", "sefer": "דברים", "english": "Nitzavim-Vayeilech", "order": 51},
    "האזינו": {"opening_words": "האזינו השמים ואדברה ותשמע הארץ", "sefer": "דברים", "english": "Ha'azinu", "order": 53},
    "וזאת הברכה": {"opening_words": "וזאת הברכה אשר ברך משה איש האלקים", "sefer": "דברים", "english": "V'Zos HaBracha", "order": 54},
}

# English to Hebrew mapping for pyluach output
ENGLISH_TO_HEBREW_PARSHA: dict[str, str] = {
    "Bereishis": "בראשית", "Noach": "נח", "Lech Lecha": "לך לך", "Vayeira": "וירא",
    "Chayei Sarah": "חיי שרה", "Toldos": "תולדות", "Vayeitzei": "ויצא", "Vayishlach": "וישלח",
    "Vayeishev": "וישב", "Mikeitz": "מקץ", "Vayigash": "ויגש", "Vayechi": "ויחי",
    "Shemos": "שמות", "Va'eira": "וארא", "Bo": "בא", "Beshalach": "בשלח",
    "Yisro": "יתרו", "Mishpatim": "משפטים", "Terumah": "תרומה", "Tetzaveh": "תצוה",
    "Ki Sisa": "כי תשא", "Vayakhel": "ויקהל", "Pekudei": "פקודי",
    "Vayakhel, Pekudei": "ויקהל-פקודי",
    "Vayikra": "ויקרא", "Tzav": "צו", "Shemini": "שמיני", "Tazria": "תזריע",
    "Metzora": "מצורע", "Tazria, Metzora": "תזריע-מצורע",
    "Acharei Mos": "אחרי מות", "Kedoshim": "קדושים", "Acharei Mos, Kedoshim": "אחרי מות-קדושים",
    "Emor": "אמור", "Behar": "בהר", "Bechukosai": "בחוקותי", "Behar, Bechukosai": "בהר-בחוקותי",
    "Bamidbar": "במדבר", "Nasso": "נשא", "Beha'aloscha": "בהעלותך", "Shelach": "שלח",
    "Korach": "קרח", "Chukas": "חוקת", "Balak": "בלק", "Chukas, Balak": "חוקת-בלק",
    "Pinchas": "פינחס", "Mattos": "מטות", "Masei": "מסעי", "Mattos, Masei": "מטות-מסעי",
    "Devarim": "דברים", "Va'eschanan": "ואתחנן", "Eikev": "עקב", "Re'eh": "ראה",
    "Shoftim": "שופטים", "Ki Seitzei": "כי תצא", "Ki Savo": "כי תבוא",
    "Nitzavim": "נצבים", "Vayeilech": "וילך", "Nitzavim, Vayeilech": "נצבים-וילך",
    "Ha'azinu": "האזינו", "V'Zos HaBracha": "וזאת הברכה",
}

CHANUKAH_READINGS: dict[int, dict] = {
    1: {"opening_words": "ויהי ביום כלות משה להקים את המשכן", "sefer": "במדבר", "parsha_source": "נשא", "reason": "א׳ דחנוכה", "display_title": "א׳ דחנוכה", "aliyah_count": 3, "has_maftir": False},
    2: {"opening_words": "ביום השני הקריב נתנאל בן צוער", "sefer": "במדבר", "parsha_source": "נשא", "reason": "ב׳ דחנוכה", "display_title": "ב׳ דחנוכה", "aliyah_count": 3, "has_maftir": False},
    3: {"opening_words": "ביום השלישי נשיא לבני זבולון", "sefer": "במדבר", "parsha_source": "נשא", "reason": "ג׳ דחנוכה", "display_title": "ג׳ דחנוכה", "aliyah_count": 3, "has_maftir": False},
    4: {"opening_words": "ביום הרביעי נשיא לבני ראובן", "sefer": "במדבר", "parsha_source": "נשא", "reason": "ד׳ דחנוכה", "display_title": "ד׳ דחנוכה", "aliyah_count": 3, "has_maftir": False},
    5: {"opening_words": "ביום החמישי נשיא לבני שמעון", "sefer": "במדבר", "parsha_source": "נשא", "reason": "ה׳ דחנוכה", "display_title": "ה׳ דחנוכה", "aliyah_count": 3, "has_maftir": False},
    6: {"opening_words": "ביום הששי נשיא לבני גד", "sefer": "במדבר", "parsha_source": "נשא", "reason": "ו׳ דחנוכה", "display_title": "ו׳ דחנוכה", "aliyah_count": 3, "has_maftir": False},
    7: {"opening_words": "ביום השביעי נשיא לבני אפרים", "sefer": "במדבר", "parsha_source": "נשא", "reason": "ז׳ דחנוכה", "display_title": "ז׳ דחנוכה", "aliyah_count": 3, "has_maftir": False},
    8: {
        "opening_words": "ביום השמיני נשיא לבני מנשה",
        "sefer": "במדבר",
        "parsha_source": "נשא",  # Still נשא for anchor purposes
        "reason": "זאת חנוכה",
        "display_title": "זאת חנוכה",
        "aliyah_count": 3,
        "has_maftir": False,
        "extends_to_beha_aloscha": True,
        # Day 8 reading goes from נשא into בהעלותך
        "ending_parsha": "בהעלותך",
    },
}

ROSH_CHODESH_READING: dict = {
    "weekday": {"opening_words": "וידבר ה׳ אל משה לאמר צו את בני ישראל", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "ראש חודש", "display_title": "ראש חודש", "aliyah_count": 4, "has_maftir": False},
    "shabbos_maftir": {"opening_words": "וביום השבת שני כבשים בני שנה", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "מפטיר ראש חודש"},
}

FAST_DAY_READING: dict = {"opening_words": "ויחל משה את פני ה׳ אלקיו", "sefer": "שמות", "parsha_source": "כי תשא", "aliyah_count": 3, "has_maftir": False}

YOM_KIPPUR_READINGS: dict = {
    "shacharis": {
        "display_title": "יום הכיפורים שחרית", "reason": "יום הכיפורים", "aliyah_count": 6, "has_maftir": True,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "אחרי מות שני בני אהרן", "sefer": "ויקרא", "parsha_source": "אחרי מות", "reason": "סדר העבודה", "aliyos": "6 עליות"},
            {"sefer_number": 2, "opening_words": "ובעשור לחודש השביעי הזה", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "מפטיר - קרבנות היום", "aliyos": "מפטיר"},
        ],
    },
    "mincha": {
        "display_title": "יום הכיפורים מנחה", "reason": "מנחה יום הכיפורים", "aliyah_count": 3, "has_maftir": False,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "וידבר ה׳ אל משה לאמר דבר אל בני ישראל ואמרת אליהם אני ה׳ אלקיכם", "sefer": "ויקרא", "parsha_source": "אחרי מות", "reason": "פרשת עריות", "aliyos": "3 עליות"},
        ],
    },
}

TISHA_BAV_READINGS: dict = {
    "shacharis": {
        "display_title": "תשעה באב שחרית", "reason": "תשעה באב", "aliyah_count": 3, "has_maftir": False,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "כי תוליד בנים ובני בנים ונושנתם", "sefer": "דברים", "parsha_source": "ואתחנן", "reason": "תוכחה", "aliyos": "3 עליות"},
        ],
    },
    "mincha": {
        "display_title": "תשעה באב מנחה", "reason": "תשעה באב מנחה", "aliyah_count": 3, "has_maftir": False,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "ויחל משה את פני ה׳ אלקיו", "sefer": "שמות", "parsha_source": "כי תשא", "reason": "ויחל", "aliyos": "3 עליות"},
        ],
    },
}

ROSH_HASHANAH_READINGS: dict = {
    "day_1": {
        "display_title": "א׳ דראש השנה", "reason": "א׳ דראש השנה", "aliyah_count": 5, "has_maftir": True,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "וה׳ פקד את שרה כאשר אמר", "sefer": "בראשית", "parsha_source": "וירא", "reason": "לידת יצחק", "aliyos": "5 עליות"},
            {"sefer_number": 2, "opening_words": "ובחודש השביעי באחד לחודש", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "מפטיר - קרבנות היום", "aliyos": "מפטיר"},
        ],
    },
    "day_2": {
        "display_title": "ב׳ דראש השנה", "reason": "ב׳ דראש השנה", "aliyah_count": 5, "has_maftir": True,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "ויהי אחר הדברים האלה והאלקים נסה את אברהם", "sefer": "בראשית", "parsha_source": "וירא", "reason": "עקידת יצחק", "aliyos": "5 עליות"},
            {"sefer_number": 2, "opening_words": "ובחודש השביעי באחד לחודש", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "מפטיר - קרבנות היום", "aliyos": "מפטיר"},
        ],
    },
}

SUKKOS_READINGS: dict = {
    "day_1": {
        "display_title": "א׳ דסוכות", "reason": "א׳ דסוכות", "aliyah_count": 5, "has_maftir": True,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "שור או כשב או עז כי יולד", "sefer": "ויקרא", "parsha_source": "אמור", "reason": "פרשת המועדות", "aliyos": "5 עליות"},
            {"sefer_number": 2, "opening_words": "ובחמשה עשר יום לחודש השביעי", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "מפטיר - קרבנות היום", "aliyos": "מפטיר"},
        ],
    },
    "day_2_diaspora": {
        "display_title": "ב׳ דסוכות", "reason": "ב׳ דסוכות", "aliyah_count": 5, "has_maftir": True,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "שור או כשב או עז כי יולד", "sefer": "ויקרא", "parsha_source": "אמור", "reason": "פרשת המועדות", "aliyos": "5 עליות"},
            {"sefer_number": 2, "opening_words": "ובחמשה עשר יום לחודש השביעי", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "מפטיר - קרבנות היום", "aliyos": "מפטיר"},
        ],
    },
    "chol_hamoed_1": {"display_title": "א׳ דחול המועד סוכות", "reason": "חול המועד סוכות", "aliyah_count": 4, "has_maftir": False, "sifrei_torah": [{"sefer_number": 1, "opening_words": "וביום השני פרים בני בקר שנים עשר", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "קרבנות היום", "aliyos": "4 עליות"}]},
    "chol_hamoed_2": {"display_title": "ב׳ דחול המועד סוכות", "reason": "חול המועד סוכות", "aliyah_count": 4, "has_maftir": False, "sifrei_torah": [{"sefer_number": 1, "opening_words": "וביום השלישי פרים עשתי עשר", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "קרבנות היום", "aliyos": "4 עליות"}]},
    "chol_hamoed_3": {"display_title": "ג׳ דחול המועד סוכות", "reason": "חול המועד סוכות", "aliyah_count": 4, "has_maftir": False, "sifrei_torah": [{"sefer_number": 1, "opening_words": "וביום הרביעי פרים עשרה", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "קרבנות היום", "aliyos": "4 עליות"}]},
    "chol_hamoed_4": {"display_title": "ד׳ דחול המועד סוכות", "reason": "חול המועד סוכות", "aliyah_count": 4, "has_maftir": False, "sifrei_torah": [{"sefer_number": 1, "opening_words": "וביום החמישי פרים תשעה", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "קרבנות היום", "aliyos": "4 עליות"}]},
    "chol_hamoed_5_israel": {"display_title": "ה׳ דחול המועד סוכות", "reason": "חול המועד סוכות", "aliyah_count": 4, "has_maftir": False, "sifrei_torah": [{"sefer_number": 1, "opening_words": "וביום הששי פרים שמונה", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "קרבנות היום", "aliyos": "4 עליות"}]},
    "hoshana_rabbah": {"display_title": "הושענא רבה", "reason": "הושענא רבה", "aliyah_count": 4, "has_maftir": False, "sifrei_torah": [{"sefer_number": 1, "opening_words": "וביום השביעי פרים שבעה", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "קרבנות היום", "aliyos": "4 עליות"}]},
    "shabbos_chol_hamoed": {
        "display_title": "שבת חול המועד סוכות",
        "reason": "שבת חול המועד",
        "aliyah_count": 7,
        "has_maftir": True,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "ראה אתה אומר אלי העל את העם הזה", "sefer": "שמות", "parsha_source": "כי תשא", "reason": "י״ג מדות", "aliyos": "7 עליות"},
            {"sefer_number": 2, "opening_words": "וביום השני פרים בני בקר", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "מפטיר - קרבנות היום", "aliyos": "מפטיר"},
        ],
    },
}

SHMINI_ATZERES_READINGS: dict = {
    "shemini_atzeres_diaspora": {
        "display_title": "שמיני עצרת", "reason": "שמיני עצרת", "aliyah_count": 5, "has_maftir": True,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "כל הבכור אשר יולד בבקרך ובצאנך", "sefer": "דברים", "parsha_source": "ראה", "reason": "פרשת הרגלים", "aliyos": "5 עליות"},
            {"sefer_number": 2, "opening_words": "ביום השמיני עצרת תהיה לכם", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "מפטיר - קרבנות היום", "aliyos": "מפטיר"},
        ],
    },
    "simchas_torah_night_diaspora": {
        "display_title": "ליל שמחת תורה", "reason": "ליל שמחת תורה - הקפות", "aliyah_count": 3, "has_maftir": False,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "וזאת הברכה אשר ברך משה איש האלקים", "sefer": "דברים", "parsha_source": "וזאת הברכה", "reason": "הקפות", "aliyos": "3 עליות"},
        ],
    },
    "simchas_torah_diaspora": {
        "display_title": "שמחת תורה", "reason": "שמחת תורה", "aliyah_count": 5, "has_maftir": True,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "וזאת הברכה אשר ברך משה איש האלקים", "sefer": "דברים", "parsha_source": "וזאת הברכה", "reason": "חתן תורה", "aliyos": "כל העליות + חתן תורה"},
            {"sefer_number": 2, "opening_words": "בראשית ברא אלקים את השמים ואת הארץ", "sefer": "בראשית", "parsha_source": "בראשית", "reason": "חתן בראשית", "aliyos": "חתן בראשית"},
            {"sefer_number": 3, "opening_words": "ביום השמיני עצרת תהיה לכם", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "מפטיר", "aliyos": "מפטיר"},
        ],
    },
    "shemini_atzeres_night_israel": {
        "display_title": "ליל שמיני עצרת / שמחת תורה", "reason": "ליל שמחת תורה - הקפות", "aliyah_count": 3, "has_maftir": False,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "וזאת הברכה אשר ברך משה איש האלקים", "sefer": "דברים", "parsha_source": "וזאת הברכה", "reason": "הקפות", "aliyos": "3 עליות"},
        ],
    },
    "shemini_atzeres_israel": {
        "display_title": "שמיני עצרת / שמחת תורה", "reason": "שמיני עצרת / שמחת תורה", "aliyah_count": 5, "has_maftir": True,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "וזאת הברכה אשר ברך משה איש האלקים", "sefer": "דברים", "parsha_source": "וזאת הברכה", "reason": "חתן תורה", "aliyos": "כל העליות + חתן תורה"},
            {"sefer_number": 2, "opening_words": "בראשית ברא אלקים את השמים ואת הארץ", "sefer": "בראשית", "parsha_source": "בראשית", "reason": "חתן בראשית", "aliyos": "חתן בראשית"},
            {"sefer_number": 3, "opening_words": "ביום השמיני עצרת תהיה לכם", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "מפטיר", "aliyos": "מפטיר"},
        ],
    },
}

# ══════════════════════════════════════════════════════════════════════════════
# OPTIONAL MINHAG-BASED READINGS (configurable)
# ══════════════════════════════════════════════════════════════════════════════

# Korbanos reading at Mincha on Erev Yom Kippur (י"ג מדות)
KORBANOS_READING: dict = {
    "display_title": "קרבנות", "reason": "קרבנות - י\"ג מדות", "aliyah_count": 0, "has_maftir": False,
    "sifrei_torah": [
        {"sefer_number": 1, "opening_words": "וידבר ה׳ אל משה לאמר צו את בני ישראל", "sefer": "שמות", "parsha_source": "כי תשא", "reason": "קרבנות", "aliyos": ""},
    ],
}

# Mishne Torah reading on night of Hoshana Rabba
MISHNE_TORAH_READING: dict = {
    "display_title": "משנה תורה", "reason": "ליל הושענא רבה - משנה תורה", "aliyah_count": 0, "has_maftir": False,
    "sifrei_torah": [
        {"sefer_number": 1, "opening_words": "אלה הדברים אשר דבר משה אל כל ישראל", "sefer": "דברים", "parsha_source": "דברים", "reason": "משנה תורה", "aliyos": ""},
    ],
}

PESACH_READINGS: dict = {
    "day_1": {
        "display_title": "א׳ דפסח", "reason": "א׳ דפסח", "aliyah_count": 5, "has_maftir": True,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "משכו וקחו לכם צאן למשפחותיכם", "sefer": "שמות", "parsha_source": "בא", "reason": "קרבן פסח", "aliyos": "5 עליות"},
            {"sefer_number": 2, "opening_words": "והקרבתם אשה עולה לה׳", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "מפטיר - קרבנות היום", "aliyos": "מפטיר"},
        ],
    },
    "day_2_diaspora": {
        "display_title": "ב׳ דפסח", "reason": "ב׳ דפסח", "aliyah_count": 5, "has_maftir": True,
        "sifrei_torah": [
            # FIXED: Day 2 diaspora reads from Emor (פרשת המועדות), not Mishpatim
            {"sefer_number": 1, "opening_words": "שור או כשב או עז כי יולד", "sefer": "ויקרא", "parsha_source": "אמור", "reason": "פרשת המועדות", "aliyos": "5 עליות"},
            {"sefer_number": 2, "opening_words": "והקרבתם אשה עולה לה׳", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "מפטיר - קרבנות היום", "aliyos": "מפטיר"},
        ],
    },
    "chol_hamoed_1": {"display_title": "א׳ דחול המועד פסח", "reason": "חול המועד פסח", "aliyah_count": 4, "has_maftir": False, "sifrei_torah": [{"sefer_number": 1, "opening_words": "קדש לי כל בכור פטר כל רחם", "sefer": "שמות", "parsha_source": "בא", "reason": "קדש לי", "aliyos": "4 עליות"}]},
    "chol_hamoed_2": {"display_title": "ב׳ דחול המועד פסח", "reason": "חול המועד פסח", "aliyah_count": 4, "has_maftir": False, "sifrei_torah": [{"sefer_number": 1, "opening_words": "אם כסף תלוה את עמי", "sefer": "שמות", "parsha_source": "משפטים", "reason": "אם כסף תלוה", "aliyos": "4 עליות"}]},
    "chol_hamoed_3": {"display_title": "ג׳ דחול המועד פסח", "reason": "חול המועד פסח", "aliyah_count": 4, "has_maftir": False, "sifrei_torah": [{"sefer_number": 1, "opening_words": "פסל לך שני לוחות אבנים", "sefer": "שמות", "parsha_source": "כי תשא", "reason": "פסל לך", "aliyos": "4 עליות"}]},
    "chol_hamoed_4": {"display_title": "ד׳ דחול המועד פסח", "reason": "חול המועד פסח", "aliyah_count": 4, "has_maftir": False, "sifrei_torah": [{"sefer_number": 1, "opening_words": "וידבר ה׳ אל משה לאמר קדש לי כל בכור", "sefer": "במדבר", "parsha_source": "בהעלותך", "reason": "בהעלותך", "aliyos": "4 עליות"}]},
    "shabbos_chol_hamoed": {
        "display_title": "שבת חול המועד פסח", "reason": "שבת חול המועד", "aliyah_count": 7, "has_maftir": True,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "ראה אתה אומר אלי העל את העם הזה", "sefer": "שמות", "parsha_source": "כי תשא", "reason": "י״ג מדות", "aliyos": "7 עליות"},
            {"sefer_number": 2, "opening_words": "והקרבתם אשה עולה לה׳", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "מפטיר - קרבנות היום", "aliyos": "מפטיר"},
        ],
    },
    "day_7": {
        "display_title": "שביעי של פסח", "reason": "שביעי של פסח", "aliyah_count": 5, "has_maftir": True,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "ויהי בשלח פרעה את העם", "sefer": "שמות", "parsha_source": "בשלח", "reason": "שירת הים", "aliyos": "5 עליות"},
            {"sefer_number": 2, "opening_words": "והקרבתם אשה עולה לה׳", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "מפטיר - קרבנות היום", "aliyos": "מפטיר"},
        ],
    },
    "day_8_diaspora": {
        "display_title": "אחרון של פסח", "reason": "אחרון של פסח", "aliyah_count": 5, "has_maftir": True,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "כל הבכור אשר יולד בבקרך ובצאנך", "sefer": "דברים", "parsha_source": "ראה", "reason": "פרשת הרגלים", "aliyos": "5 עליות"},
            {"sefer_number": 2, "opening_words": "והקרבתם אשה עולה לה׳", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "מפטיר - קרבנות היום", "aliyos": "מפטיר"},
        ],
    },
}

SHAVUOS_READINGS: dict = {
    "day_1": {
        "display_title": "שבועות", "reason": "שבועות", "aliyah_count": 5, "has_maftir": True,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "בחודש השלישי לצאת בני ישראל מארץ מצרים", "sefer": "שמות", "parsha_source": "יתרו", "reason": "עשרת הדברות", "aliyos": "5 עליות"},
            {"sefer_number": 2, "opening_words": "וביום הביכורים בהקריבכם מנחה חדשה", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "מפטיר - קרבנות היום", "aliyos": "מפטיר"},
        ],
    },
    "day_2_diaspora": {
        "display_title": "ב׳ דשבועות", "reason": "ב׳ דשבועות", "aliyah_count": 5, "has_maftir": True,
        "sifrei_torah": [
            {"sefer_number": 1, "opening_words": "כל הבכור אשר יולד בבקרך ובצאנך", "sefer": "דברים", "parsha_source": "ראה", "reason": "פרשת הרגלים", "aliyos": "5 עליות"},
            {"sefer_number": 2, "opening_words": "וביום הביכורים בהקריבכם מנחה חדשה", "sefer": "במדבר", "parsha_source": "פינחס", "reason": "מפטיר - קרבנות היום", "aliyos": "מפטיר"},
        ],
    },
}

PURIM_READING: dict = {
    "display_title": "פורים", "reason": "פורים", "aliyah_count": 3, "has_maftir": False,
    "sifrei_torah": [{"sefer_number": 1, "opening_words": "ויבא עמלק וילחם עם ישראל ברפידים", "sefer": "שמות", "parsha_source": "בשלח", "reason": "מלחמת עמלק", "aliyos": "3 עליות"}],
}

SPECIAL_PARSHIYOS: dict = {
    "shekalim": {"opening_words": "כי תשא את ראש בני ישראל", "sefer": "שמות", "parsha_source": "כי תשא", "reason": "פרשת שקלים"},
    "zachor": {"opening_words": "זכור את אשר עשה לך עמלק", "sefer": "דברים", "parsha_source": "כי תצא", "reason": "פרשת זכור"},
    "parah": {"opening_words": "זאת חוקת התורה", "sefer": "במדבר", "parsha_source": "חוקת", "reason": "פרשת פרה"},
    "hachodesh": {"opening_words": "החודש הזה לכם ראש חדשים", "sefer": "שמות", "parsha_source": "בא", "reason": "פרשת החודש"},
}

# Scroll anchor sources - used for prep_now logic
SCROLL_ANCHORS: dict[str, str] = {
    "fast_day": "כי תשא",
    "rosh_chodesh": "פינחס",
    "chanukah": "נשא",
    "purim": "בשלח",
    "tisha_bav_shacharis": "ואתחנן",
    "tisha_bav_mincha": "כי תשא",
}

# Nesiim readings for 1-13 Nissan (minhag, no aliyos/brachos)
NESIIM_READINGS: dict[int, dict] = {
    1: {"nasi": "יהודה", "opening_words": "ויהי המקריב ביום הראשון את קרבנו נחשון בן עמינדב למטה יהודה", "pesukim": "במדבר ז:יב-יז"},
    2: {"nasi": "יששכר", "opening_words": "ביום השני הקריב נתנאל בן צוער נשיא יששכר", "pesukim": "במדבר ז:יח-כג"},
    3: {"nasi": "זבולון", "opening_words": "ביום השלישי נשיא לבני זבולון אליאב בן חלון", "pesukim": "במדבר ז:כד-כט"},
    4: {"nasi": "ראובן", "opening_words": "ביום הרביעי נשיא לבני ראובן אליצור בן שדיאור", "pesukim": "במדבר ז:ל-לה"},
    5: {"nasi": "שמעון", "opening_words": "ביום החמישי נשיא לבני שמעון שלומיאל בן צורישדי", "pesukim": "במדבר ז:לו-מא"},
    6: {"nasi": "גד", "opening_words": "ביום הששי נשיא לבני גד אליסף בן דעואל", "pesukim": "במדבר ז:מב-מז"},
    7: {"nasi": "אפרים", "opening_words": "ביום השביעי נשיא לבני אפרים אלישמע בן עמיהוד", "pesukim": "במדבר ז:מח-נג"},
    8: {"nasi": "מנשה", "opening_words": "ביום השמיני נשיא לבני מנשה גמליאל בן פדהצור", "pesukim": "במדבר ז:נד-נט"},
    9: {"nasi": "בנימין", "opening_words": "ביום התשיעי נשיא לבני בנימין אבידן בן גדעוני", "pesukim": "במדבר ז:ס-סה"},
    10: {"nasi": "דן", "opening_words": "ביום העשירי נשיא לבני דן אחיעזר בן עמישדי", "pesukim": "במדבר ז:סו-עא"},
    11: {"nasi": "אשר", "opening_words": "ביום עשתי עשר יום נשיא לבני אשר פגעיאל בן עכרן", "pesukim": "במדבר ז:עב-עז"},
    12: {"nasi": "נפתלי", "opening_words": "ביום שנים עשר יום נשיא לבני נפתלי אחירע בן עינן", "pesukim": "במדבר ז:עח-פג"},
    13: {"nasi": "סיכום", "opening_words": "זאת חנוכת המזבח ביום המשח אותו", "pesukim": "במדבר ז:פד-פט"},
}

MONDAY = 0
THURSDAY = 3
FRIDAY = 4
SATURDAY = 5
SUNDAY = 6

HEBREW_DAYS = {0: "יום ב׳", 1: "יום ג׳", 2: "יום ד׳", 3: "יום ה׳", 4: "יום ו׳", 5: "שבת", 6: "יום א׳"}
HEBREW_MONTHS = {1: "ניסן", 2: "אייר", 3: "סיון", 4: "תמוז", 5: "אב", 6: "אלול", 7: "תשרי", 8: "חשון", 9: "כסלו", 10: "טבת", 11: "שבט", 12: "אדר", 13: "אדר ב׳"}
"""
custom_components/yidcal/yidcal_lib/erev_motzi_options.py

The fixed candle-lighting and Motzi opinions that ``sensor.yidcal_
zman_erev`` and ``sensor.yidcal_zman_motzi`` publish as attributes,
alongside whatever the user configured.

Same shape as ``alos_options`` / ``tzeis_options``: a data table plus a
resolver, no astronomy and no Home Assistant. Adding an opinion is one
row.

WHAT THESE ARE. Both sensors already publish ONE time — the one built
from the configured ``candlelighting_offset`` / ``havdalah_offset``.
These attributes publish the same moment under the OTHER common
opinions, so a dashboard can show a second shita, or a household can
compare before setting the config, without standing up more entities.
The state is untouched.

ANCHORING. Every opinion here is measured from shkia on the SAME civil
date the sensor's own state was built from — not from "today". The
sensors hand that date in. That is what keeps an alternative honest
during a freeze: on Shabbos afternoon the Erev sensor is frozen on
Friday's lighting, so the alternatives describe Friday too, not the
Shabbos that Friday's shkia has nothing to do with.

  erev     minutes BEFORE shkia
  motzi    minutes AFTER shkia, or degrees below the horizon

A degree opinion has no value on a date where the sun never reaches
that depression. Those are OMITTED by the caller rather than
substituted, the same rule ``all_tzeis_for_date`` follows.

PROVENANCE. 15 and 18 minutes, and 50 / 60 / 72 minutes and 8.5°, are
the standard printed opinions. Any row marked ``unverified=True`` came
from a single second-hand report and has NOT been confirmed against a
published luach — see the note on that row before relying on it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OffsetOpinion:
    key: str          # short identifier
    kind: str         # "fixed" (minutes) | "degrees"
    value: float      # minutes, or degrees below the horizon
    attr: str         # attribute name published on the sensor
    hebrew: str
    english: str
    unverified: bool = False   # True → sourced second-hand, not from a luach


# ── Erev / hadlakas neiros: minutes BEFORE shkia ──
#
# Nothing in this list may be negative. Hadlakas neiros is by definition
# before shkia, so an "after shkia" candle-lighting figure is not a
# stricter opinion, it is a different quantity that has been mislabelled
# somewhere upstream. Keep such a number out until it is resolved rather
# than publishing it under a candle-lighting attribute name.
EREV_OPINIONS: list[OffsetOpinion] = [
    OffsetOpinion(
        "min_15", "fixed", 15, "Zman_Erev_15_Minutes",
        "15 מינוט פאר שקיעה", "15 minutes before sunset",
    ),
    OffsetOpinion(
        "min_18", "fixed", 18, "Zman_Erev_18_Minutes",
        "18 מינוט פאר שקיעה", "18 minutes before sunset",
    ),
    # Reported as the Viznitz figure. The source said "30 min after
    # shkiah"; BEFORE is the only reading under which it is a
    # candle-lighting time at all, and a half hour before shkia is a
    # well-attested custom independently of Viznitz. The direction is
    # therefore settled. The ATTRIBUTION is not: no published Viznitz
    # luach was found confirming it. Named for both the attribution and
    # the number so a later correction renames the key and breaks a
    # template loudly rather than serving a wrong time under a
    # right-looking name.
    OffsetOpinion(
        "viznitz_30", "fixed", 30, "Zman_Erev_Viznitz_30_Minutes",
        "30 מינוט פאר שקיעה (וויזניץ)", "30 minutes before sunset (Viznitz)",
        unverified=True,
    ),
]


# ── Motzi / havdalah: after shkia ──
MOTZI_OPINIONS: list[OffsetOpinion] = [
    OffsetOpinion(
        "min_50", "fixed", 50, "Zman_Motzi_50_Minutes",
        "50 מינוט נאך שקיעה", "50 minutes after sunset",
    ),
    # Reported as the Viznitz figure. NOT confirmed against a published
    # Viznitz luach — kept under a name that says both the attribution
    # and the number, so that if the number turns out to be wrong the
    # rename breaks any template loudly instead of quietly serving a
    # wrong time under a right-looking name.
    OffsetOpinion(
        "viznitz_54", "fixed", 54, "Zman_Motzi_Viznitz_54_Minutes",
        "54 מינוט נאך שקיעה (וויזניץ)", "54 minutes after sunset (Viznitz)",
        unverified=True,
    ),
    OffsetOpinion(
        "min_60", "fixed", 60, "Zman_Motzi_60_Minutes",
        "60 מינוט נאך שקיעה", "60 minutes after sunset",
    ),
    OffsetOpinion(
        "min_72", "fixed", 72, "Zman_Motzi_72_Minutes",
        "72 מינוט נאך שקיעה", "72 minutes after sunset",
    ),
    OffsetOpinion(
        "deg_8_5", "degrees", 8.5, "Zman_Motzi_8_5_Degrees",
        "8.5° אונטערן האריזאנט", "8.5 degrees below the horizon",
    ),
]


EREV_BY_KEY: dict[str, OffsetOpinion] = {o.key: o for o in EREV_OPINIONS}
MOTZI_BY_KEY: dict[str, OffsetOpinion] = {o.key: o for o in MOTZI_OPINIONS}

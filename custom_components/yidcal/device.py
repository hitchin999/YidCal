# yidcal/device.py

from homeassistant.helpers.device_registry import DeviceInfo

DOMAIN = "yidcal"

class YidCalDevice:
    """Mixin for all YidCal entities to share one DeviceInfo."""

    _attr_device_info = DeviceInfo(
        identifiers={(DOMAIN, "yidcal_main")},
        name="YidCal",
        manufacturer="Yoel Goldstein/Vaayer LLC",
        model="A Yiddish Calendar Integration",
        entry_type="service",
    )

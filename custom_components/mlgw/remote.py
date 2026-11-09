"""Send named BEO4 commands to individual MasterLink products."""

import asyncio
from collections.abc import Iterable
from typing import Any

from homeassistant.components.remote import (
    ATTR_DELAY_SECS,
    ATTR_DEVICE,
    ATTR_HOLD_SECS,
    ATTR_NUM_REPEATS,
    DEFAULT_DELAY_SECS,
    RemoteEntity,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    BEO4_CMDS,
    DOMAIN,
    MLGW_GATEWAY,
    MLGW_GATEWAY_CONFIGURATION_DATA,
    ml_destselectordict,
)

# These names share codes with FUNCTION_8 and FUNCTION_10, respectively,
# and are consequently overwritten when beo4_commanddict is constructed.
COMMANDS = {**BEO4_CMDS, "SHIFT-PLAY": 0x22, "SHIFT-GOTO": 0x24}
COMMANDS.pop("<ALL>", None)
COMMANDS.update({str(digit): digit for digit in range(10)})

_LOCK_KEY = "remote_command_lock"


def _parse_options(device: str | None) -> tuple[int, int, int]:
    """Parse explicit routing fields; never infer routing from speaker state."""
    options = {}
    if device is not None:
        for field in device.split(";"):
            key, separator, value = field.partition("=")
            key, value = key.strip().lower(), value.strip().lower()
            if not separator or key not in {"destination", "secondary", "link"}:
                raise ServiceValidationError(f"Invalid BEO4 option: {field!r}")
            if key in options or not value:
                raise ServiceValidationError(f"Invalid or duplicate BEO4 option: {key}")
            options[key] = value
    destination = options.get("destination", ml_destselectordict[0x01].lower())
    dest = next(
        (code for code, name in ml_destselectordict.items()
         if name.lower() == destination),
        None,
    )
    if dest is None:
        raise ServiceValidationError(f"Unknown BEO4 destination: {destination}")
    flags = []
    for name in ("secondary", "link"):
        value = options.get(name, "false")
        if value not in {"true", "false", "0", "1"}:
            raise ServiceValidationError(f"BEO4 {name} must be true, false, 0, or 1")
        flags.append(int(value in {"true", "1"}))
    if flags[0] and dest != 0x05:
        raise ServiceValidationError(
            "Secondary sources require destination=v.tape/v.mem"
        )
    return dest, flags[0], flags[1]


def _create_remotes(data, async_add_entities, serial):
    """Build remotes from configuration, independently of media player setup."""
    lock = data.setdefault(_LOCK_KEY, asyncio.Lock())
    gateway = data[MLGW_GATEWAY]
    async_add_entities([
        MasterLinkRemote(gateway, product, zone, serial, lock)
        for zone in data[MLGW_GATEWAY_CONFIGURATION_DATA]["zones"]
        for product in zone.get("products", [])
    ])


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up remotes for a gateway config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    _create_remotes(data, async_add_entities, data["serial"])


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up remotes for a manually configured gateway."""
    if discovery_info is None:
        return
    _create_remotes(hass.data[DOMAIN], async_add_entities, DOMAIN)


class MasterLinkRemote(RemoteEntity):
    """A command-only remote sharing the speaker's name and device."""

    _attr_should_poll = False

    def __init__(self, gateway, product, zone, serial, lock):
        self._gateway = gateway
        self._mln = product["MLN"]
        self._lock = lock
        self._attr_name = product["name"]
        self._attr_unique_id = f"{serial}-remote-{self._mln}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._mln)},
            name=product["name"],
            manufacturer="Bang & Olufsen",
            suggested_area=zone.get("name"),
        )

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        """Validate and transmit a sequence using this product's MLN."""
        if kwargs.get(ATTR_HOLD_SECS, 0):
            raise ServiceValidationError(
                "BEO4 hold_secs is unsupported; send repeat and release commands explicitly"
            )
        dest, secondary, link = _parse_options(kwargs.get(ATTR_DEVICE))
        commands = [command] if isinstance(command, str) else list(command)
        codes = []
        for name in commands:
            normalized = name.strip().upper()
            if normalized not in COMMANDS:
                raise ServiceValidationError(f"Unknown BEO4 command: {name!r}")
            codes.append(COMMANDS[normalized])
        repeats = kwargs.get(ATTR_NUM_REPEATS, 1)
        delay = kwargs.get(ATTR_DELAY_SECS, DEFAULT_DELAY_SECS)
        async with self._lock:
            remaining = len(codes) * repeats
            for _ in range(repeats):
                for code in codes:
                    if not self._gateway.connectedMLGW:
                        raise HomeAssistantError("MasterLink Gateway is disconnected")
                    await self.hass.async_add_executor_job(
                        self._gateway.mlgw_send_beo4_cmd,
                        self._mln, dest, code, secondary, link,
                    )
                    remaining -= 1
                    if remaining and delay:
                        await asyncio.sleep(delay)

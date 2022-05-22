"""Provides device triggers for mlgw integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.automation import (
    AutomationActionType,
    AutomationTriggerInfo,
)
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import entity_registry
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, MLGW_EVENT_MLGW_TELEGRAM

PAYLOAD_LIGHT_CONTROL_EVENT = "light_control_event"
PAYLOAD_ALL_STANDBY = "all_standby"
CONF_PAYLOAD_TYPE = "payload_type"
CONF_PAYLOAD_TYPES = [PAYLOAD_LIGHT_CONTROL_EVENT]
# CONF_PAYLOAD_TYPES = [PAYLOAD_ALL_STANDBY, PAYLOAD_LIGHT_CONTROL_EVENT]
TRIGGER_TYPES = ["LIGHT", "CONTROL"]
LIGHT_COMMAND = "subtype"
LIGHT_COMMANDS = [
    "Standby",
    "Sleep",
    "TV",
    "Radio",
    "V.Aux",
    "A.Aux",
    "Media",
    "V.Mem",
    "DVD",
    "Camera",
    "Text",
    "DTV",
    "PC",
    "Web",
    "Doorcam",
    "Photo",
    "USB2",
    "A.Mem",
    "CD",
    "N.Radio",
    "N.Music",
    "Server",
    "Spotify",
    "CD2 / Join",
    "AV",
    "P-IN-P",
    "Digit-0",
    "Digit-1",
    "Digit-2",
    "Digit-3",
    "Digit-4",
    "Digit-5",
    "Digit-6",
    "Digit-7",
    "Digit-8",
    "Digit-9",
    "Step Up",
    "Step Down",
    "Rewind",
    "Return",
    "Wind",
    "Go / Play",
    "Stop",
    "Yellow",
    "Green",
    "Blue",
    "Red",
    "Mute",
    "P.Mute",
    "Format",
    "Sound / Speaker",
    "Menu",
    "Volume Up",
    "Volume Down",
    "Cinema_On",
    "Cinema_Off",
    "Stand",
    "Clear",
    "Store",
    "Reset",
    "Back",
    "MOTS",
    "Goto",
    "Show Clock",
    "Eject",
    "Record",
    "Select",
    "Sound",
    "Exit",
    "Guide",
    "Info",
    "Select",
    "Cursor_Up",
    "Cursor_Down",
    "Cursor_Left",
    "Cursor_Right",
    "Light",
    "Command",
]

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        # vol.Required(CONF_ENTITY_ID): cv.entity_id,
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
        vol.Required(CONF_PAYLOAD_TYPE): vol.In(CONF_PAYLOAD_TYPES),
        vol.Optional(LIGHT_COMMAND): vol.In(LIGHT_COMMANDS),
    }
)


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """List device triggers for mlgw integration devices."""

    registry = await entity_registry.async_get_registry(hass)
    triggers = []

    if not entity_registry.async_entries_for_device(registry, device_id):
        base_trigger = {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
        }

        for triggertype in TRIGGER_TYPES:
            for light_command in LIGHT_COMMANDS:
                triggers.append(
                    {
                        **base_trigger,
                        CONF_PAYLOAD_TYPE: PAYLOAD_LIGHT_CONTROL_EVENT,
                        CONF_TYPE: triggertype,
                        LIGHT_COMMAND: light_command,
                    }
                )

    return triggers


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: AutomationActionType,
    automation_info: AutomationTriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a trigger."""

    event_data = {
        CONF_PAYLOAD_TYPE: PAYLOAD_LIGHT_CONTROL_EVENT,
        CONF_TYPE: config[CONF_TYPE],
        LIGHT_COMMAND: config[LIGHT_COMMAND],
    }
    event_config = {
        event_trigger.CONF_PLATFORM: "event",
        event_trigger.CONF_EVENT_DATA: event_data,
        event_trigger.CONF_EVENT_TYPE: MLGW_EVENT_MLGW_TELEGRAM,
    }

    event_config = event_trigger.TRIGGER_SCHEMA(event_config)
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, automation_info, platform_type="device"
    )

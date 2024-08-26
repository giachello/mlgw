"""Provides Device Actions for the integration.

Provides the following Device Actions:
Virtual Buttons
All Standby
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_TYPE
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
import homeassistant.helpers.config_validation as cv

from . import DOMAIN, SERVICE_ALL_STANDBY, SERVICE_VIRTUAL_BUTTON
from .const import ATTR_MLGW_BUTTON

ACTION_TYPES = {"all_standby", "virtual_button"}

ALL_STANDBY_SCHEMA = cv.DEVICE_ACTION_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(ACTION_TYPES),
        vol.Required(CONF_DEVICE_ID): str,
    }
)

MLGW_VIRTUAL_BUTTONS = range(1, 256)

VIRTUAL_BUTTON_SCHEMA = cv.DEVICE_ACTION_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(ACTION_TYPES),
        vol.Required(CONF_DEVICE_ID): str,
        vol.Required("code"): vol.In(MLGW_VIRTUAL_BUTTONS),
    }
)

ACTION_SCHEMA = vol.Any(ALL_STANDBY_SCHEMA, VIRTUAL_BUTTON_SCHEMA)


async def async_get_actions(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """List device actions for aketest_integration devices."""
    registry = er.async_get(hass)
    actions = []

    if not er.async_entries_for_device(registry, device_id):
        base_action = {
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            # CONF_ENTITY_ID: entry.entity_id,
        }

        for action_type in ACTION_TYPES:
            actions.append({**base_action, CONF_TYPE: action_type})

    return actions


async def async_call_action_from_config(
    hass: HomeAssistant, config: dict, variables: dict, context: Context | None
) -> None:
    """Execute a device action."""
    service_data = {}

    if config[CONF_TYPE] == "all_standby":
        service = SERVICE_ALL_STANDBY
    elif config[CONF_TYPE] == "virtual_button":
        service = SERVICE_VIRTUAL_BUTTON
        service_data[ATTR_MLGW_BUTTON] = config["code"]

    await hass.services.async_call(
        DOMAIN, service, service_data, blocking=True, context=context
    )


async def async_get_action_capabilities(hass, config):
    """List action capabilities."""
    action_type = config[CONF_TYPE]

    fields = {}

    if action_type == "virtual_button":
        fields[vol.Required("code")] = vol.In(MLGW_VIRTUAL_BUTTONS)

    return {"extra_fields": vol.Schema(fields)}

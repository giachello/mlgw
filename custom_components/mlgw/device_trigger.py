"""Provides device triggers for mlgw integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
import logging

from homeassistant.components.automation import (
    AutomationActionType,
    AutomationTriggerInfo,
)
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import state
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_ENTITY_ID,
    CONF_PLATFORM,
    CONF_TYPE,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_registry
from homeassistant.helpers.typing import ConfigType

from . import DOMAIN

# TODO specify your supported trigger types.
TRIGGER_TYPES = {"light_go", "light_stop"}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        # vol.Required(CONF_ENTITY_ID): cv.entity_id,
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
    }
)

_LOGGER = logging.getLogger(__name__)

# async def async_get_mlgw_device(hass, device_id):
#     """Get a mlgw device for the given device registry id."""
#     device_registry = await hass.helpers.device_registry.async_get_registry()
#     device = device_registry.async_get(device_id)
#     mlgw_gateway = hass.data[DOMAIN][registry_device.unique_id]
#     return mlgw_gateway.devices[ieee]


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """List device triggers for mlgw integration devices."""
    _LOGGER.debug("Get triggers: ")

    device_registry = await entity_registry.async_get_registry(hass)
    device = device_registry.async_get(device_id)
    triggers = []

    # TODO Read this comment and remove it.
    # This example shows how to iterate over the entities of this device
    # that match this integration. If your triggers instead rely on
    # events fired by devices without entities, do something like:
    # zha_device = await _async_get_zha_device(hass, device_id)
    # return zha_device.device_triggers

    # Add triggers for each entity that belongs to this integration
    # TODO add your own triggers.
    base_trigger = {
        CONF_PLATFORM: "device",
        CONF_DEVICE_ID: device_id,
        CONF_DOMAIN: DOMAIN,
    }
    triggers.append({**base_trigger, CONF_TYPE: "light_go"})
    triggers.append({**base_trigger, CONF_TYPE: "light_stop"})
    _LOGGER.debug("Triggers: %s", triggers)

    return triggers


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: AutomationActionType,
    automation_info: AutomationTriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a trigger."""
    # TODO Implement your own logic to attach triggers.
    # Use the existing state or event triggers from the automation integration.

    _LOGGER.debug("Attach trigger: %s", config[CONF_TYPE])
    if config[CONF_TYPE] == "light_go":
        to_state = STATE_ON
    else:
        to_state = STATE_OFF

    state_config = {
        state.CONF_PLATFORM: "state",
        CONF_ENTITY_ID: config[CONF_ENTITY_ID],
        state.CONF_TO: to_state,
    }
    state_config = state.TRIGGER_SCHEMA(state_config)
    return await state.async_attach_trigger(
        hass, state_config, action, automation_info, platform_type="device"
    )

"""Provides device actions for aketest_integration."""
from __future__ import annotations
from custom_components.mlgw.const import ATTR_MLGW_BUTTON

import voluptuous as vol
import logging

from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_ENTITY_ID,
    CONF_TYPE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry
import homeassistant.helpers.config_validation as cv

from . import DOMAIN, SERVICE_VIRTUAL_BUTTON

_LOGGER = logging.getLogger(__name__)

# TODO specify your supported action types.
ACTION_TYPES = {"all_standby", "virtual_button"}

ALL_STANDBY_SCHEMA = cv.DEVICE_ACTION_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(ACTION_TYPES),
        vol.Required(CONF_ENTITY_ID): cv.entity_domain(DOMAIN),
    }
)

VIRTUAL_BUTTON_SCHEMA = cv.DEVICE_ACTION_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(ACTION_TYPES),
        vol.Required(CONF_ENTITY_ID): cv.entity_domain(DOMAIN),
        vol.Required("virtual_button_no"): str,
    }
)

ACTION_SCHEMA = vol.Any(ALL_STANDBY_SCHEMA, VIRTUAL_BUTTON_SCHEMA)


async def async_get_actions(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """List device actions for aketest_integration devices."""
    registry = await entity_registry.async_get_registry(hass)
    actions = []

    # TODO Read this comment and remove it.
    # This example shows how to iterate over the entities of this device
    # that match this integration. If your actions instead rely on
    # calling services, do something like:
    # zha_device = await _async_get_zha_device(hass, device_id)
    # return zha_device.device_actions

    # Get all the integrations entities for this device
    for entry in entity_registry.async_entries_for_device(registry, device_id):
        _LOGGER.debug("Action: %s | %s", entry.entity_id, entry.domain)
        # if entry.domain != DOMAIN:
        #     continue

        # Add actions for each entity that belongs to this integration
        # TODO add your own actions.
        base_action = {
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_ENTITY_ID: entry.entity_id,
        }

        for action_type in ACTION_TYPES:
            actions.append({**base_action, CONF_TYPE: action_type})
        _LOGGER.debug("Actions: %s", actions)

    return actions


async def async_call_action_from_config(
    hass: HomeAssistant, config: dict, variables: dict, context: Context | None
) -> None:
    """Execute a device action."""
    service_data = {ATTR_ENTITY_ID: config[CONF_ENTITY_ID]}

    if config[CONF_TYPE] == "all_standby":
        service = SERVICE_TURN_OFF
    elif config[CONF_TYPE] == "virtual_button":
        service = SERVICE_VIRTUAL_BUTTON
        service_data[ATTR_MLGW_BUTTON] = config["virtual_button_no"]

    await hass.services.async_call(
        DOMAIN, service, service_data, blocking=True, context=context
    )

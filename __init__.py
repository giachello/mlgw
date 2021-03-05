"""The MasterLink Gateway integration."""
import asyncio
from .gateway import create_mlgw_gateway, create_mlgw_gateway_with_configuration_data
from .media_player import BeoSpeaker
import logging
import json

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_DEVICES,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, discovery

from .const import (
    DOMAIN,
    MLGW_GATEWAY,
    MLGW_GATEWAY_CONFIGURATION_DATA,
    MLGW_DEVICES,
    CONF_MLGW_DEFAULT_SOURCE,
    CONF_MLGW_AVAILABLE_SOURCES,
    MLGW_DEFAULT_SOURCE,
    MLGW_AVAILABLE_SOURCES,
    CONF_MLGW_DEVICE_NAME,
    CONF_MLGW_DEVICE_MLN,
    CONF_MLGW_DEVICE_ROOM,
    CONF_MLGW_DEVICE_MLID,
    CONF_MLGW_USE_MLLOG,
    BASE_URL,
    TIMEOUT,
    MLGW_CONFIG_JSON_PATH,
)


CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_HOST, default="192.168.1.10"): cv.string,
                vol.Required(CONF_PASSWORD, default="admin"): cv.string,
                vol.Optional(CONF_USERNAME, default="admin"): cv.string,
                vol.Optional(CONF_PORT, default=9000): cv.positive_int,
                vol.Optional(CONF_MLGW_USE_MLLOG, default=True): cv.boolean,
                vol.Optional(
                    CONF_MLGW_DEFAULT_SOURCE, default=MLGW_DEFAULT_SOURCE
                ): cv.string,
                vol.Optional(
                    CONF_MLGW_AVAILABLE_SOURCES, default=MLGW_AVAILABLE_SOURCES
                ): cv.ensure_list,
                vol.Required(CONF_DEVICES): vol.All(
                    cv.ensure_list,
                    [
                        {
                            vol.Required(CONF_MLGW_DEVICE_NAME): cv.string,
                            vol.Optional(CONF_MLGW_DEVICE_MLN): cv.positive_int,
                            vol.Optional(CONF_MLGW_DEVICE_ROOM): cv.positive_int,
                            vol.Optional(CONF_MLGW_DEVICE_MLID): cv.string,
                        }
                    ],
                ),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

_LOGGER = logging.getLogger(__name__)


# TODO List the platforms that you want to support.
# For your initial PR, limit it to 1 platform.
PLATFORMS = ["media_player"]

# This is the routine that is used when configuring from configuration.yaml
async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the MasterLink Gateway component."""
    hass.data.setdefault(DOMAIN, {})
    mlgw_config = config.get(DOMAIN, {})
    if not mlgw_config:
        return True

    host = mlgw_config.get(CONF_HOST)
    password = mlgw_config.get(CONF_PASSWORD)
    user = mlgw_config.get(CONF_USERNAME)
    port = mlgw_config.get(CONF_PORT)
    hass.data[DOMAIN][MLGW_DEVICES] = mlgw_config.get(CONF_DEVICES)
    default_source = mlgw_config.get(CONF_MLGW_DEFAULT_SOURCE)
    available_sources = mlgw_config.get(CONF_MLGW_AVAILABLE_SOURCES)
    if user == "admin":
        use_mllog = mlgw_config.get(CONF_MLGW_USE_MLLOG)
    else:
        use_mllog = False

    gateway = await create_mlgw_gateway(
        host, port, user, password, default_source, available_sources, use_mllog, hass
    )
    if not gateway:
        return False
    hass.data[DOMAIN][MLGW_GATEWAY] = gateway

    hass.async_create_task(
        discovery.async_load_platform(hass, "media_player", DOMAIN, {}, mlgw_config)
    )

    #    hass.async_create_task(
    #        hass.config_entries.flow.async_init(
    #            DOMAIN,
    #            context={"source": SOURCE_IMPORT},
    #            data=mlgw_config,
    #        )
    #    )
    return True


def get_mlgw_configuration_data(host: str, username: str, password: str):
    import requests
    from requests.auth import HTTPDigestAuth, HTTPBasicAuth

    # try with Digest Auth first
    response = requests.get(
        BASE_URL.format(host, MLGW_CONFIG_JSON_PATH),
        timeout=TIMEOUT,
        auth=HTTPDigestAuth(username, password),
    )
    # if unauthorized use fallback to Basic Auth
    if response.status_code == 401:
        response = requests.get(
            BASE_URL.format(host, MLGW_CONFIG_JSON_PATH),
            timeout=TIMEOUT,
            auth=HTTPBasicAuth(username, password),
        )
    if response.status_code != 200:
        return None

    return response.json()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up MasterLink Gateway from a config entry."""
    # TODO Store an API object for your platforms to access
    # hass.data[DOMAIN][entry.entry_id] = MyApi(...)

    host = entry.data.get(CONF_HOST)
    password = entry.data.get(CONF_PASSWORD)
    username = entry.data.get(CONF_USERNAME)
    use_mllog = entry.data.get(CONF_MLGW_USE_MLLOG)

    mlgw_configurationdata = await hass.async_add_executor_job(
        get_mlgw_configuration_data, host, username, password
    )

    if mlgw_configurationdata is None:
        return False

    gateway = await create_mlgw_gateway_with_configuration_data(
        host, username, password, use_mllog, mlgw_configurationdata, hass
    )
    if not gateway:
        return False
    hass.data[DOMAIN][MLGW_GATEWAY] = gateway
    hass.data[DOMAIN][MLGW_GATEWAY_CONFIGURATION_DATA] = mlgw_configurationdata

    for component in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, component)
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    _LOGGER.debug("Async unload entry")

    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )
    if unload_ok:
        gateway = hass.data[DOMAIN].pop(MLGW_GATEWAY)
        await gateway.terminate_async()

    return True

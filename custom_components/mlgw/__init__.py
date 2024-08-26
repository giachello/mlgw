"""The MasterLink Gateway integration."""

import asyncio
import logging

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
from requests.exceptions import RequestException
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady
from homeassistant.const import (
    CONF_DEVICES,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    discovery,
)
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.typing import ServiceDataType

from .const import (
    ATTR_MLGW_ACTION,
    ATTR_MLGW_BUTTON,
    BASE_URL,
    BEO4_CMDS,
    CONF_MLGW_AVAILABLE_SOURCES,
    CONF_MLGW_DEFAULT_SOURCE,
    CONF_MLGW_DEVICE_MLID,
    CONF_MLGW_DEVICE_MLN,
    CONF_MLGW_DEVICE_NAME,
    CONF_MLGW_DEVICE_ROOM,
    CONF_MLGW_USE_MLLOG,
    DOMAIN,
    MLGW_AVAILABLE_SOURCES,
    MLGW_CONFIG_JSON_PATH,
    MLGW_DEFAULT_SOURCE,
    MLGW_DEVICES,
    MLGW_GATEWAY,
    MLGW_GATEWAY_CONFIGURATION_DATA,
    TIMEOUT,
    reverse_ml_destselectordict,
    reverse_ml_selectedsourcedict,
    reverse_mlgw_virtualactiondict,
)
from .gateway import MasterLinkGateway, create_mlgw_gateway

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

SERVICE_ALL_STANDBY = "all_standby"
SERVICE_VIRTUAL_BUTTON = "virtual_button"

SERVICE_VIRTUAL_BUTTON_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_MLGW_BUTTON): cv.positive_int,
        vol.Optional(ATTR_MLGW_ACTION, default="PRESS"): cv.string,
    }
)

_LOGGER = logging.getLogger(__name__)


# TODO List the platforms that you want to support.
# For your initial PR, limit it to 1 platform.
PLATFORMS = ["media_player"]


def yaml_to_json_config(manual_devices, availabe_sources):
    """Convert the YAML configuration into the equivalent json config from the MLGW."""
    result = {}
    result["zones"] = []
    i = 1
    for device in manual_devices:
        if CONF_MLGW_DEVICE_MLN in device.keys():
            mln = device[CONF_MLGW_DEVICE_MLN]
        else:
            mln = i
        i = i + 1
        room = 0
        if CONF_MLGW_DEVICE_ROOM in device.keys():
            room = device[CONF_MLGW_DEVICE_ROOM]
        ml = None
        if CONF_MLGW_DEVICE_MLID in device.keys():
            ml = device[CONF_MLGW_DEVICE_MLID]
        _z = None
        r = 0
        _zl = [w for w in result["zones"] if w["number"] == room]
        if len(_zl) == 0:
            _z = {}
            _z["number"] = room
            result["zones"].append(_z)
            r = result["zones"].index(_z)
        else:
            r = result["zones"].index(_zl[0])

        if "products" not in result["zones"][r]:
            result["zones"][r]["products"] = []

        product = {}
        product["MLN"] = mln
        product["ML"] = ml
        product["name"] = device[CONF_MLGW_DEVICE_NAME]

        device_sources = []
        for _x in availabe_sources:
            _source = {}
            _source["name"] = _x
            _source["destination"] = reverse_ml_destselectordict.get("AUDIO SOURCE")
            _source["format"] = "F0"
            _source["secondary"] = 0
            _source["link"] = 0
            _source["statusID"] = reverse_ml_selectedsourcedict.get(_x)
            _source["selectID"] = BEO4_CMDS.get(_x)
            _source["selectCmds"] = []
            _source["selectCmds"].append({"cmd": BEO4_CMDS.get(_x), "format": "F0"})
            device_sources.append(_source)

        product["sources"] = device_sources
        result["zones"][r]["products"].append(product)
    return result


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the MasterLink Gateway from configuration.yaml."""

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

    mlgw_configurationdata = yaml_to_json_config(
        hass.data[DOMAIN][MLGW_DEVICES], available_sources
    )
    mlgw_configurationdata["port"] = port

    if mlgw_configurationdata is None:
        return False

    gateway = await create_mlgw_gateway(
        hass,
        host,
        user,
        password,
        mlgw_configurationdata,
        use_mllog,
        default_source=default_source,
        available_sources=available_sources,
    )
    if not gateway:
        return False

    # Only one gateway configuration supported here
    hass.data[DOMAIN][MLGW_GATEWAY] = gateway
    hass.data[DOMAIN][MLGW_GATEWAY_CONFIGURATION_DATA] = mlgw_configurationdata

    hass.async_create_task(
        discovery.async_load_platform(hass, "media_player", DOMAIN, {}, mlgw_config)
    )

    register_services(hass, gateway)

    return True


def get_mlgw_configuration_data(host: str, username: str, password: str):
    """Get the configuration data from the mlgw using the mlgwpservices.json endpoint."""

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


def register_services(hass: HomeAssistant, gateway: MasterLinkGateway):
    """Register the Virtual Button and All Standby services."""

    def virtual_button_press(service: ServiceDataType):
        if not gateway:
            return False
        act = reverse_mlgw_virtualactiondict.get(service.data[ATTR_MLGW_ACTION])
        if act is None:
            act = 0x01
        gateway.mlgw_send_virtual_btn_press(service.data[ATTR_MLGW_BUTTON], act)
        return True

    def send_all_standby(service: ServiceDataType):
        if not gateway:
            return False
        gateway.mlgw_send_all_standby()
        return True

    # Register the services
    hass.services.async_register(
        DOMAIN,
        SERVICE_VIRTUAL_BUTTON,
        virtual_button_press,
        schema=SERVICE_VIRTUAL_BUTTON_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        "all_standby",
        send_all_standby,
        schema=vol.Schema({}),
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up MasterLink Gateway from a config entry."""

    host = entry.data.get(CONF_HOST)
    password = entry.data.get(CONF_PASSWORD)
    username = entry.data.get(CONF_USERNAME)
    use_mllog = entry.data.get(CONF_MLGW_USE_MLLOG)

    try:
        mlgw_configurationdata = await hass.async_add_executor_job(
            get_mlgw_configuration_data, host, username, password
        )
    except RequestException as ex:
        # this will cause Home Assistant to retry setting up the integration later.
        raise ConfigEntryNotReady(f"Cannot connect to {host}, is it on?") from ex

    if mlgw_configurationdata is None:
        return False

    gateway = await create_mlgw_gateway(
        hass,
        host,
        username,
        password,
        mlgw_configurationdata,
        use_mllog,
        entry.entry_id,
    )
    if not gateway:
        return False

    hass.data[DOMAIN][entry.entry_id] = {}
    hass.data[DOMAIN][entry.entry_id][MLGW_GATEWAY] = gateway
    hass.data[DOMAIN][entry.entry_id][MLGW_GATEWAY_CONFIGURATION_DATA] = (
        mlgw_configurationdata
    )
    hass.data[DOMAIN][entry.entry_id]["serial"] = entry.unique_id
    _LOGGER.debug("Serial: %s", entry.unique_id)

    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.unique_id)},
        manufacturer="Bang & Olufsen",
        name=mlgw_configurationdata["project"],
        model="MasterLink Gateway",
        hw_version=mlgw_configurationdata["version"],
        config_entry_id=entry.entry_id,
        configuration_url=f"http://{host}",
    )
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(**device_info)

    #    for component in PLATFORMS:
    #        hass.async_create_task(
    #            hass.config_entries.async_forward_entry_setup(entry, component)
    #        )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    register_services(hass, gateway)

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
        hass.services.async_remove(DOMAIN, SERVICE_VIRTUAL_BUTTON)
        gateway = hass.data[DOMAIN][entry.entry_id].pop(MLGW_GATEWAY)
        await gateway.terminate_async()
    else:
        _LOGGER.warning("Error Unloading Entries")
        return False

    return True

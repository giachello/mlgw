"""Config flow for MasterLink Gateway integration."""
import logging
import voluptuous as vol
import requests
import socket
import xml.etree.ElementTree as ET
import httpx

from homeassistant import config_entries, core, exceptions
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from .const import (
    CONF_MLGW_USE_MLLOG,
    BASE_URL,
    MLGW_CONFIG_JSON_PATH,
    TIMEOUT,
)  # pylint:disable=unused-import

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Data schema for the configuration flows
USER_STEP_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_MLGW_USE_MLLOG): bool,
    }
)

ZEROCONF_STEP_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_MLGW_USE_MLLOG): bool,
    }
)

"""Validate the user input allows us to connect.

Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
"""
async def async_authenticate(_host: str,_user: str,_password: str) -> dict:
    # Test if we can authenticate with the host.
    data = None
    # try Digest Auth first (this is needed for the MLGW)
    async with httpx.AsyncClient() as client:
        response = await client.get(
            BASE_URL.format(_host, MLGW_CONFIG_JSON_PATH),
            timeout=TIMEOUT,
            auth=httpx.DigestAuth(_user, _password),
        )
    # try Basic Auth next (this is needed for the BLGW)
        if response.status_code == 401:
            response = await client.get(
                BASE_URL.format(_host, MLGW_CONFIG_JSON_PATH),
                timeout=TIMEOUT,
                auth=httpx.BasicAuth(_user, _password),
            )
            if response.status_code == 401:
                raise InvalidAuth()
        if response.status_code == 404:
            raise InvalidGateway()
        if response.status_code != 200:
            response.raise_for_status()

        data = response.json()

    return {"name": data["project"], "sn": data["sn"]}


    # Get serial number of mlgw

async def async_mlgw_get_xmpp_serial(_host: str) -> str:
    _LOGGER.debug("Open XMPP connect to MLGW")
    # open socket to masterlink gateway
    _socket: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _socket.settimeout(TIMEOUT)
    try:
        _socket.connect((_host, 5222))
    except Exception as exc:
        _LOGGER.error("Error opening XMPP connection to MLGW (%s): %s" % (_host, exc))
        _socket.close()
        return None
    # Request serial number to mlgw
    _telegram = (
        "<?xml version='1.0'?>"
        "<stream:stream to='products.bang-olufsen.com' version='1.0' "
        "xmlns='jabber:client' "
        "xmlns:stream='http://etherx.jabber.org/streams'>"
    )
    # Receive serial number string from mlgw
    sn = None
    try:
        _socket.sendall(_telegram.encode())
        _mlgwdata = (_socket.recv(1024)).decode("utf-8")
        _xml = ET.fromstring(_mlgwdata + "</stream:stream>")
        sn = _xml.attrib["from"].split("@")[0].split(".")[2]
        _LOGGER.debug("Closed XMPP connection to MLGW - s/n %s" % sn)
        _socket.close()
    except Exception as exc:
        _LOGGER.error("Closed XMPP connection - error receiving MLGW info from %s: %s" % (_host, exc))
        _socket.close()

    return sn


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MasterLink Gateway."""

    VERSION = 1
    # TODO pick one of the available connection classes in homeassistant/config_entries.py
#    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    def __init__(self):
        """Initialize."""
        self.host = None
        self.hostname = None

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        _LOGGER.debug("Async Step User Config Flow called")

        errors = {}

        if user_input is not None:
            try:
                user_input[CONF_HOST] = socket.gethostbyname(user_input[CONF_HOST])
                info = await async_authenticate(user_input[CONF_HOST],
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
            except (CannotConnect, httpx.ConnectError, httpx.ConnectTimeout) as exc:
                _LOGGER.error("Error opening connection to MLGW (%s): %s"
                    % (user_input[CONF_HOST], exc))
                errors["base"] = "cannot_connect"
            except (InvalidHost, socket.gaierror):
                _LOGGER.error("Invalid Host: %s" % user_input[CONF_HOST])
                errors["base"] = "invalid_host"
            except InvalidGateway:
                _LOGGER.error("Invalid Gateway 404: %s not found" % MLGW_CONFIG_JSON_PATH)
                errors["base"] = "invalid_gateway"
            except InvalidAuth:
                _LOGGER.error("Invalid authentication 401")
                errors["base"] = "invalid_auth"
            except Exception as exc:  # pylint: disable=broad-except
                _LOGGER.error("Unexpected exception: %s while requesting http://%s/%s"
                    % (exc, user_input[CONF_HOST], MLGW_CONFIG_JSON_PATH))
                errors["base"] = "unknown"
            if not errors:
                # Check if already configured
                await self.async_set_unique_id(info["sn"])
                self._abort_if_unique_id_configured()
                title = ("Masterlink Gateway '%s' s/n %s" % (info["name"], info["sn"]))
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=USER_STEP_DATA_SCHEMA, errors=errors
        )

    async def async_step_zeroconf(self, discovery_info):
        """Handle zeroconf discovery."""
        _LOGGER.debug("Async_Step_Zeroconf start")
        if discovery_info is None:
            return self.async_abort(reason="cannot_connect")
        # _LOGGER.debug("Async_Step_Zeroconf discovery info %s" % discovery_info)

        # if it's not a MLGW or BLGW device, then abort
        if not discovery_info.get("name") or not discovery_info["name"].startswith(
            "LGW",1
        ):
            return self.async_abort(reason="not_mlgw_device")

        # Hostname is format: mlgw.local.
        self.hostname = discovery_info["hostname"].rstrip(".")
        _LOGGER.debug("Async_Step_Zeroconf Hostname %s" % self.hostname)

        self.host = discovery_info[CONF_HOST]

        try:
        	sn = await async_mlgw_get_xmpp_serial(self.host)
        except Exception as exc:
        	_LOGGER.debug("Exception %s" % exc)
        	return self.async_abort(reason="cannot_connect")
        if sn is not None:
        	await self.async_set_unique_id(sn)
        	self._abort_if_unique_id_configured()

        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(self, user_input=None):
        """Handle a flow initiated by zeroconf."""
        _LOGGER.debug("zeroconf_confirm: %s" % user_input)

        errors = {}

        if user_input is not None:
            user_input[CONF_HOST] = self.host
            try:
                info = await async_authenticate(user_input[CONF_HOST],
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
            except (CannotConnect, httpx.ConnectError, httpx.ConnectTimeout) as exc:
                _LOGGER.error("Error opening connection to MLGW (%s): %s"
                    % (user_input[CONF_HOST], exc))
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                _LOGGER.error("Invalid authentication 401")
                errors["base"] = "invalid_auth"
            except Exception as exc:  # pylint: disable=broad-except
                _LOGGER.error("Unexpected exception: %s while requesting http://%s/%s"
                    % (exc, user_input[CONF_HOST], MLGW_CONFIG_JSON_PATH))
                errors["base"] = "unknown"
            if not errors:
                title=("Masterlink Gateway '%s' s/n %s" % (info["name"], info["sn"]))
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_HOST: self.host,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_MLGW_USE_MLLOG: user_input[CONF_MLGW_USE_MLLOG],
                    },
                )

        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=ZEROCONF_STEP_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "name": self.host,
            },
        )

class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate there is invalid auth."""


class InvalidGateway(exceptions.HomeAssistantError):
    """Error to indicate there is invalid Gateway."""


class InvalidHost(exceptions.HomeAssistantError):
    """Error to indicate that hostname/IP address is invalid."""


class DeviceAlreadyConfigured(exceptions.HomeAssistantError):
    """Error to indicate device is already configured."""

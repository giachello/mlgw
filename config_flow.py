"""Config flow for MasterLink Gateway integration."""
import logging
import ipaddress
import re
import voluptuous as vol
import requests
import socket
from requests.auth import HTTPDigestAuth, HTTPBasicAuth
from requests.exceptions import ConnectTimeout

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


def host_valid(host):
    """Return True if hostname or IP address is valid."""
    try:
        if ipaddress.ip_address(host).version == (4 or 6):
            return True
    except ValueError:
        disallowed = re.compile(r"[^a-zA-Z\d\-]")
        return all(x and not disallowed.search(x) for x in host.split("."))


class CheckPasswordMLGWHub:
    """Checks Password for the MLGW Hub and gets basic information. """

    def __init__(self, host):
        """Initialize."""
        self._host = host
        self._data = None

    def authenticate(self, user, password) -> bool:
        """Test if we can authenticate with the host."""
        # try Digest Auth first (this is needed for the MLGW)
        response = requests.get(
            BASE_URL.format(self._host, MLGW_CONFIG_JSON_PATH),
            timeout=TIMEOUT,
            auth=HTTPDigestAuth(user, password),
        )
        # try Basic Auth next (this is needed for the BLGW)
        if response.status_code == 401:
            response = requests.get(
                BASE_URL.format(self._host, MLGW_CONFIG_JSON_PATH),
                timeout=TIMEOUT,
                auth=HTTPBasicAuth(user, password),
            )

        if response.status_code == 401:
            _LOGGER.debug("Invalid authentication 401")
            raise InvalidAuth()

        if response.status_code != 200:
            return False

        self._data = response.json()
        return True

class CheckSerialNumberMLGWHub:
    """Checks Serial Number for the MLGW Hub. """

    def __init__(self, host):
        self._host = host
        self._socket = None
        self.buffersize = 1024
        self._connectedMLGW = False
        self._data = None

    ## Close connection to mlgw
    def mlgw_close_xmpp(self):
        if self._connectedMLGW:
            self._connectedMLGW = False
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
                self._socket.close()
            except:
                _LOGGER.warning("Error closing XMPP connection to MLGW")
                return
            _LOGGER.info("Closed XMPP connection to MLGW")

    ## Get serial number of mlgw
    def mlgw_get_xmapp_serial(self) -> bool:
        _LOGGER.info("Trying to open XMPP connect to MLGW")
        # open socket to masterlink gateway
        self._socket: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # operations fail if they cannot be completed within Timeout second
        self._socket.settimeout(TIMEOUT)
        try:
            self._socket.connect((self._host, 5222))
        except Exception as e:
            self._socket = None
            _LOGGER.error("Error opening XMPP connection to MLGW (%s): %s" % (self._host, e))
            self.mlgw_close_xmpp()
            return False
        self._connectedMLGW = True
        # Request serial number to mlgw
        self._telegram = "<?xml version='1.0'?>" \
                "<stream:stream to='products.bang-olufsen.com' version='1.0' " \
                "xmlns='jabber:client' " \
                "xmlns:stream='http://etherx.jabber.org/streams'>"
        self._socket.sendall(self._telegram.encode())
        ## Receive serial number string from mlgw
        try:
            self._mlgwdata = (self._socket.recv(self.buffersize)).decode("utf-8")
        except Exception as e:
            self._socket = None
            _LOGGER.error("Error receiving MLGW info from %s: %s" % (self._host, e))
            self.mlgw_close_xmpp()
            return False
        # Decoded string to get serial number
        if self._mlgwdata != 0x00:
            last_number = self._mlgwdata.find('@')
            self._data = self._mlgwdata[last_number-8:last_number]
            _LOGGER.info("mlgw: Serial number of MLGW is " + self._data)  # info
        else:  # Display -Missing Serial Numner-
            self._data = None
            _LOGGER.info("mlgw: Missing Serial number of MLGW ")  # info
        self.mlgw_close_xmpp()
        self._data = {"sn": self._data}
        return True


async def validate_input(hass: core.HomeAssistant, data):
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user,
    or from discovery_info with values provided by Zeroconf.
    """
    # TODO validate the data can be used to set up a connection.

    # If your PyPI package is not built with async, pass your methods
    # to the executor:
    # await hass.async_add_executor_job(
    #     your_validate_func, data["username"], data["password"]
    # )
    if CONF_USERNAME in data:
    	hub = CheckPasswordMLGWHub(data[CONF_HOST])
    	func = hub.authenticate
    	args = (data[CONF_USERNAME], data[CONF_PASSWORD])
    else:
    	hub = CheckSerialNumberMLGWHub(data[CONF_HOST])
    	func = hub.mlgw_get_xmapp_serial
    	args = ()

    await hass.async_add_executor_job(func, *args)

    # If you cannot connect throw CannotConnect
    # If the authentication is wrong throw InvalidAuth

    # Return info that you want to store in the config entry.
    if "project" in hub._data:
        return {"name": hub._data["project"], "sn": hub._data["sn"]}
    else:
        return hub._data



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
                if not host_valid(user_input[CONF_HOST]):
                    raise InvalidHost()

                info = await validate_input(self.hass, user_input)

                # Check if already configured
                await self.async_set_unique_id(info["sn"])
                self._abort_if_unique_id_configured()

                title = ("Masterlink Gateway '%s' s/n %s" % (info["name"], info["sn"]))
                return self.async_create_entry(title=title, data=user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except ConnectTimeout:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except InvalidHost:
                errors["base"] = "invalid_host"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=USER_STEP_DATA_SCHEMA, errors=errors
        )

    async def async_step_zeroconf(self, discovery_info):
        """Handle zeroconf discovery."""
        _LOGGER.debug("Async_Step_Zeroconf start")
        if discovery_info is None:
            return self.async_abort(reason="cannot_connect")
        _LOGGER.debug("Async_Step_Zeroconf discovery info %s" % discovery_info)

        # if it's not a MLGW or BLGW device, then abort
        if not discovery_info.get("name") or not discovery_info["name"].startswith(
            "LGW",1
        ):
            return self.async_abort(reason="not_mlgw_device")

        # Hostname is format: mlgw.local.
        self.hostname = discovery_info["hostname"].rstrip(".")
        _LOGGER.debug("Async_Step_Zeroconf Hostname %s" % self.hostname)

        self.host = discovery_info[CONF_HOST]
        info = await validate_input(self.hass, discovery_info)

         # Check if already configured
        await self.async_set_unique_id(info["sn"])
        self._abort_if_unique_id_configured()

        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(self, user_input=None):
        """Handle a flow initiated by zeroconf."""
        _LOGGER.debug("zeroconf_confirm: %s" % user_input)

        if user_input is not None:
            user_input[CONF_HOST] = self.host
            info = await validate_input(self.hass, user_input)
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
            description_placeholders={
                "name": self.host,
            },
        )

class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate there is invalid auth."""


class InvalidHost(exceptions.HomeAssistantError):
    """Error to indicate that hostname/IP address is invalid."""

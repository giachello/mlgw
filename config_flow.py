"""Config flow for MasterLink Gateway integration."""
import logging
import ipaddress
import re
import voluptuous as vol
import requests
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


async def validate_input(hass: core.HomeAssistant, data):
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    # TODO validate the data can be used to set up a connection.

    # If your PyPI package is not built with async, pass your methods
    # to the executor:
    # await hass.async_add_executor_job(
    #     your_validate_func, data["username"], data["password"]
    # )

    hub = CheckPasswordMLGWHub(data[CONF_HOST])

    #    if not await hub.authenticate(data[CONF_USERNAME],data[CONF_PASSWORD]):
    #        raise InvalidAuth
    await hass.async_add_executor_job(
        hub.authenticate, data[CONF_USERNAME], data[CONF_PASSWORD]
    )

    # If you cannot connect throw CannotConnect
    # If the authentication is wrong throw InvalidAuth

    # Return info that you want to store in the config entry.
    return {"name": hub._data["project"], "sn": hub._data["sn"]}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MasterLink Gateway."""

    VERSION = 1
    # TODO pick one of the available connection classes in homeassistant/config_entries.py
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    def __init__(self):
        """Initialize."""
        self.host = None

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        _LOGGER.debug("Async Step User Config Flow called")

        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=USER_STEP_DATA_SCHEMA
            )

        errors = {}

        try:
            if not host_valid(user_input[CONF_HOST]):
                raise InvalidHost()

            info = await validate_input(self.hass, user_input)
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
        else:
            return self.async_create_entry(
                title=("Masterlink Gateway '%s' s/n %s" % (info["name"], info["sn"])),
                data=user_input,
            )

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
        if not discovery_info.get("name"):
            return self.async_abort(reason="not_mlgw_device")

        if not (
            discovery_info["name"].startswith("MLGW")
            or discovery_info["name"].startswith("BLGW")
        ):
            return self.async_abort(reason="not_mlgw_device")

        # Hostname is format: mlgw.local.
        self.host = discovery_info["hostname"].rstrip(".")
        _LOGGER.debug("Async_Step_Zeroconf Hostname %s" % self.host)

        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(self, user_input=None):
        """Handle a flow initiated by zeroconf."""

        _LOGGER.debug("zeroconf_confirm: %s" % user_input)

        if user_input is None:
            return self.async_show_form(
                step_id="zeroconf_confirm",
                data_schema=ZEROCONF_STEP_DATA_SCHEMA,
                description_placeholders={
                    "name": self.host,
                },
            )

        errors = {}
        user_input[CONF_HOST] = self.host

        try:
            if not host_valid(user_input[CONF_HOST]):
                raise InvalidHost()

            info = await validate_input(self.hass, user_input)

        except Exception as e:
            _LOGGER.debug("zeroconf_confirm: Exception %s" % str(e))
            errors["base"] = "cannot_connect"

        await self.async_set_unique_id(info["sn"])

        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=("Masterlink Gateway '%s' s/n %s" % (info["name"], info["sn"])),
            data={
                CONF_HOST: self.host,
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_MLGW_USE_MLLOG: user_input[CONF_MLGW_USE_MLLOG],
            },
        )


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate there is invalid auth."""


class InvalidHost(exceptions.HomeAssistantError):
    """Error to indicate that hostname/IP address is invalid."""

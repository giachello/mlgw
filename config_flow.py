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

# TODO adjust the data schema to the data that you need
USER_STEP_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
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
    """Placeholder class to make tests pass.

    TODO Remove this placeholder class and replace with things from your PyPI package.
    """

    def __init__(self, host):
        """Initialize."""
        self._host = host

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

    # If you cannot connect:
    # throw CannotConnect
    # If the authentication is wrong:
    # InvalidAuth

    # Return info that you want to store in the config entry.
    return {"title": "Masterlink Gateway"}


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
            return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=USER_STEP_DATA_SCHEMA, errors=errors
        )


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate there is invalid auth."""


class InvalidHost(exceptions.HomeAssistantError):
    """Error to indicate that hostname/IP address is invalid."""

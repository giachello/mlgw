"""

Media Player platform for Master Link Gateway connected devices.

------------------------------------------------------------
Where the current sources get modified. There are 3 places:

Media player entity subscribes to
GOTO SOURCE
TRACK INFO
Media player entity Select Source

Gateway changes media player source
in _mlgw_thread (source status, if the source is not in standby and position>0)

Gateway keeps track of last selected source
in _ml_thread (GOTO SOURCE)
in send beo4 message (select source)

-------------------------------------------------------------
Where state (ON/OFF) gets modified:

Media Player
GOTO SOURCE
RELEASE
turn on / Select Source

Gateway (Pict and Snd status)



"""
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.core import Event, CALLBACK_TYPE
import logging
import voluptuous as vol
from homeassistant.helpers import config_validation as cv
import asyncio

from homeassistant.const import (
    STATE_OFF,
    STATE_ON,
    CONF_DEVICES,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_DEVICES,
    CONF_USERNAME,
)

from homeassistant.components.media_player import MediaPlayerEntity

from homeassistant.components.media_player.const import (
    SUPPORT_TURN_ON,
    SUPPORT_TURN_OFF,
    SUPPORT_SELECT_SOURCE,
    SUPPORT_VOLUME_STEP,
    SUPPORT_VOLUME_MUTE,
)

SUPPORT_BEO = (
    SUPPORT_TURN_ON
    | SUPPORT_TURN_OFF
    | SUPPORT_VOLUME_STEP
    | SUPPORT_SELECT_SOURCE
    | SUPPORT_VOLUME_MUTE
)

from .const import (
    DOMAIN,
    beo4_commanddict,
    ml_destselectordict,
    reverse_ml_destselectordict,
    ml_selectedsourcedict,
    BEO4_CMDS,
    MLGW_GATEWAY,
    MLGW_DEVICES,
    MLGW_GATEWAY_CONFIGURATION_DATA,
    CONF_MLGW_DEFAULT_SOURCE,
    CONF_MLGW_AVAILABLE_SOURCES,
    MLGW_DEFAULT_SOURCE,
    MLGW_AVAILABLE_SOURCES,
    CONF_MLGW_DEVICE_NAME,
    CONF_MLGW_DEVICE_MLN,
    CONF_MLGW_DEVICE_ROOM,
    CONF_MLGW_DEVICE_MLID,
    CONF_MLGW_USE_MLLOG,
    MLGW_EVENT_ML_TELEGRAM,
)

from .gateway import MasterLinkGateway

_LOGGER = logging.getLogger(__name__)

# Set up the Media_player devices. there are two ways, through the manual configuration in configuration.yaml and through a config flow that automatically reads the devices list from the mlgw.

# #########################################################################################
#  devices through automatic configuration


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
):
    hass.data.setdefault(DOMAIN, {})

    mlgw_configurationdata = hass.data[DOMAIN][MLGW_GATEWAY_CONFIGURATION_DATA]
    gateway: MasterLinkGateway = hass.data[DOMAIN][MLGW_GATEWAY]
    mp_devices = list()

    device_sequence = list()
    ml_listener_iteration: int = 0
    stop_listening: CALLBACK_TYPE = None

    def _message_listener(_event: Event):
        nonlocal ml_listener_iteration
        if (
            _event.data["from_device"] == "MLGW"
            and _event.data["payload_type"] == "MLGW_REMOTE_BEO4"
            and _event.data["payload"]["command"] == "<all>"
        ):
            _LOGGER.info(
                "ML LOG returned ML id %s for MLN %s"
                % (
                    _event.data["to_device"],
                    str(gateway._devices[device_sequence[ml_listener_iteration]]._mln),
                )
            )
            gateway._devices[device_sequence[ml_listener_iteration]].set_ml(
                _event.data["to_device"]
            )
            ml_listener_iteration = ml_listener_iteration + 1

    if gateway.connectedMLGW:

        # listen to ML messages to track down the actual ML id of the device
        if gateway._connectedML:
            stop_listening = gateway._hass.bus.async_listen(
                MLGW_EVENT_ML_TELEGRAM, _message_listener
            )

        for zone in mlgw_configurationdata["zones"]:
            for product in zone["products"]:
                device_sources = list()
                device_dest = list()
                for source in product["sources"]:
                    device_sources.append(ml_selectedsourcedict.get(source["statusID"]))
                    device_dest.append(source["destination"])
                beospeaker = BeoSpeaker(
                    product["MLN"],
                    product["name"],
                    zone["number"],
                    gateway,
                    device_sources,
                    device_dest,
                )
                mp_devices.append(beospeaker)
                # Send a dummy command to the device. If the ML_LOG system is operating, then a ML telegram
                # will be sent from the MLGW to the actual device, and that will include the ML device address
                # which is different from the MLN used by MLGW Prototcol. This allows us to reconnect the ML
                # traffic to a device in Home Assistant. It does not work for NL devices so don't send it if
                # there is a Serial Number attached to the device.
                if gateway._connectedML and product.get("sn") is None:
                    device_sequence.append(len(mp_devices) - 1)  # skip NL devices
                    gateway.mlgw_send_beo4_cmd(
                        beospeaker._mln,
                        reverse_ml_destselectordict.get("AUDIO SOURCE"),
                        BEO4_CMDS.get("<ALL>"),
                    )

        async_add_entities(mp_devices, True)
        gateway.set_devices(
            mp_devices
        )  # tell the gateway the list of devices connected to it.

        # wait for 10 seconds or until all the devices have reported back their ML address
        if gateway._connectedML:
            waiting_for = 0.0
            while ml_listener_iteration < len(gateway._devices) and waiting_for < 10:
                await asyncio.sleep(0.1)
                waiting_for = waiting_for + 0.1
            stop_listening()  # clean up the listener for the device codes.
            _LOGGER.info("got back the ml Ids")

    else:
        _LOGGER.error("MLGW Not connected while trying to add media_player devices")


# #########################################################################################
# devices through manual configuration


async def async_setup_platform(hass, config, add_devices, discovery_info=None):
    hass.data.setdefault(DOMAIN, {})

    manual_devices = hass.data[DOMAIN][MLGW_DEVICES]
    gateway: MasterLinkGateway = hass.data[DOMAIN][MLGW_GATEWAY]
    mp_devices = list()

    ml_listener_iteration: int = 0
    stop_listening: CALLBACK_TYPE = None

    def _message_listener(_event: Event):
        nonlocal ml_listener_iteration
        if (
            _event.data["from_device"] == "MLGW"
            and _event.data["payload_type"] == "MLGW_REMOTE_BEO4"
            and _event.data["payload"]["command"] == "<all>"
        ):
            _LOGGER.info(
                "ML LOG returned ML id %s for MLN %s"
                % (
                    _event.data["to_device"],
                    str(gateway._devices[ml_listener_iteration]._mln),
                )
            )
            gateway._devices[ml_listener_iteration].set_ml(_event.data["to_device"])
            ml_listener_iteration = ml_listener_iteration + 1

    if gateway.connectedMLGW:

        # listen to ML messages to track down the actual ML id of the device
        if gateway._connectedML:
            stop_listening = gateway._hass.bus.async_listen(
                MLGW_EVENT_ML_TELEGRAM, _message_listener
            )

        i = 1
        for device in manual_devices:
            if CONF_MLGW_DEVICE_MLN in device.keys():
                mln = device[CONF_MLGW_DEVICE_MLN]
            else:
                mln = i
            i = i + 1
            _LOGGER.info(
                "Adding device: %s at mln: %s"
                % (device[CONF_MLGW_DEVICE_NAME], str(mln))
            )
            room = None
            if CONF_MLGW_DEVICE_ROOM in device.keys():
                room = device[CONF_MLGW_DEVICE_ROOM]
            ml = None
            if CONF_MLGW_DEVICE_MLID in device.keys():
                ml = device[CONF_MLGW_DEVICE_MLID]

            device_dest = list()
            for _x in gateway.available_sources:
                device_dest.append(reverse_ml_destselectordict.get("AUDIO SOURCE"))

            beospeaker = BeoSpeaker(
                mln,
                device[CONF_MLGW_DEVICE_NAME],
                room,
                gateway,
                gateway.available_sources,
                device_dest,
            )
            beospeaker.set_ml(ml)
            mp_devices.append(beospeaker)
            if gateway._connectedML:
                gateway.mlgw_send_beo4_cmd(
                    beospeaker._mln,
                    reverse_ml_destselectordict.get("AUDIO SOURCE"),
                    BEO4_CMDS.get("<ALL>"),
                )

        add_devices(mp_devices)
        gateway.set_devices(
            mp_devices
        )  # tell the gateway the list of devices connected to it.

        # wait for 10 seconds or until all the devices have reported back their ML address
        if gateway._connectedML:
            waiting_for = 0.0
            while ml_listener_iteration < len(gateway._devices) and waiting_for < 10:
                await asyncio.sleep(0.1)
                waiting_for = waiting_for + 0.1
            stop_listening()  # clean up the listener for the device codes.
            _LOGGER.info("got back the ml Ids")

    else:
        _LOGGER.error("MLGW Not connected while trying to add media_player devices")


"""
BeoSpeaker represents a single MasterLink device on the Masterlink bus. E.g., a speaker like BeoSound 3500 or a Masterlink Master device like a receiver or TV (e.g, a Beosound 3000)
Because the Masterlink has only one active source across all the speakers, we maintain the source state in the Gateway class, which manages the relationship with the Masterlink Gateway. It's not very clean, but it works.
"""


class BeoSpeaker(MediaPlayerEntity):
    def __init__(
        self,
        mln,
        name,
        room,
        gateway: MasterLinkGateway,
        available_sources: list,
        dest: list,
    ):
        self._mln = mln
        self._ml = None
        self._name = name
        self._room = room
        self._gateway = gateway
        self._pwon = False
        self._source = self._gateway.default_source
        self._stop_listening = None
        self._available_sources = available_sources
        self._dest = dest

        # set up a listener for "RELEASE" and "GOTO_SOURCE" commands associated with this speaker to
        # adjust the state. "All Standby" command is managed directly in the MLGW listener in MasterlinkGateway

        def _beospeaker_message_listener(_event: Event):
            if self._ml is not None:
                if _event.data["from_device"] == self._ml:
                    if _event.data["payload_type"] == "RELEASE":
                        _LOGGER.info("ML LOG said: RELEASE id %s" % (self._ml))
                        self._pwon = False
                    elif _event.data["payload_type"] == "GOTO_SOURCE":
                        _LOGGER.info(
                            "ML LOG said: GOTO_SOURCE %s on device %s"
                            % (_event.data["payload"]["source"], self._ml)
                        )
                        # reflect that the device is on and store the requested source
                        self._pwon = True
                        self._source = _event.data["payload"]["source"]
                if _event.data["to_device"] == self._ml:
                    if (  # I'm being told to change source
                        _event.data["payload_type"] == "TRACK_INFO"
                        and _event.data["payload"]["subtype"] == "Change Source"
                    ):
                        self._source = _event.data["payload"]["source"]

        if self._gateway._connectedML:
            self._stop_listening = gateway._hass.bus.async_listen(
                MLGW_EVENT_ML_TELEGRAM, _beospeaker_message_listener
            )

    def __del__(self):
        if self._gateway._connectedML:
            self._stop_listening()

    @property
    def name(self):
        return self._name

    @property
    def friendly_name(self):
        return self._name.capwords(sep="_")

    @property
    def supported_features(self):
        # Flag media player features that are supported.
        return SUPPORT_BEO

    @property
    def supported_media_commands(self):
        """Flag of media commands that are supported."""
        return SUPPORT_BEO

    @property
    def source(self):
        # Name of the current input source. Because the source is common across all the speakers connected to the gateway, we just pass through the beolink.
        # this breaks when there are devices with local sources and we need to fix it.
        #        self._source = self._gateway.beolink_source
        return self._source

    @property
    def source_list(self):
        """List of available input sources."""
        return self._available_sources

    @property
    def state(self):
        """Return the state of the device."""
        if self._pwon:
            return STATE_ON
        else:
            return STATE_OFF

    def set_ml(self, ml: str):
        self._ml = ml

    def set_state(self, _state):
        # to be called by the gateway to set the state to off when there is an event on the ml bus that turns off the device
        if _state == STATE_ON:
            self._pwon = True
        elif _state == STATE_OFF:
            self._pwon = False

    def turn_on(self):
        # when turning on this speaker, use the last known source active on beolink
        # if there is no such source, then use the last source used on this speaker
        # if there is no such source, then use the first source in the available sources list.
        # if there is no source in that list, then do nothing
        if self._gateway.beolink_source is not None:
            self.select_source(self._gateway.beolink_source)
        elif self._source is not None:
            self.select_source(self._source)
        elif len(self._available_sources) > 0:
            self.select_source(self._available_sources[0])

    # An alternate is to turn on with volume up which for most devices, turns it on without changing source, but it does nothing on the BeoSound system.
    #        self._pwon = True
    #        self.volume_up()

    def turn_off(self):
        self._pwon = False
        destination = self._dest[self._available_sources.index(self._source)]
        self._gateway.mlgw_send_beo4_cmd(
            self._mln, destination, BEO4_CMDS.get("STANDBY")
        )

    def select_source(self, source):
        self._pwon = True
        self._source = source
        destination = self._dest[self._available_sources.index(source)]
        self._gateway.mlgw_send_beo4_cmd_select_source(
            self._mln, destination, self._source
        )

    def volume_up(self):
        destination = self._dest[self._available_sources.index(self._source)]
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            destination,
            BEO4_CMDS.get("VOLUME UP"),
        )

    def volume_down(self):
        destination = self._dest[self._available_sources.index(self._source)]
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            destination,
            BEO4_CMDS.get("VOLUME DOWN"),
        )

    def mute_volume(self, mute):
        destination = self._dest[self._available_sources.index(self._source)]
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            destination,
            BEO4_CMDS.get("MUTE"),
        )

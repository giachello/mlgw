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
    BEO4_CMDS,
    MLGW_GATEWAY,
    MLGW_DEVICES,
    CONF_MLGW_DEFAULT_SOURCE,
    CONF_MLGW_AVAILABLE_SOURCES,
    MLGW_DEFAULT_SOURCE,
    MLGW_AVAILABLE_SOURCES,
    CONF_MLGW_DEVICE_NAME,
    CONF_MLGW_DEVICE_MLN,
    CONF_MLGW_DEVICE_ROOM,
    CONF_MLGW_USE_MLLOG,
    MLGW_EVENT_ML_TELEGRAM,
)

from .gateway import MasterLinkGateway

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, add_devices, discovery_info=None):
    hass.data.setdefault(DOMAIN, {})

    devices = hass.data[DOMAIN][MLGW_DEVICES]
    gateway: MasterLinkGateway = hass.data[DOMAIN][MLGW_GATEWAY]
    mp_devices = list()

    ml_listener_iteration: int = 0
    stop_listening: CALLBACK_TYPE = None

    def _message_listener(_event: Event):
        nonlocal ml_listener_iteration
        if (
            _event.data["from_device"] == "MLGW"
            and _event.data["payload_type"] == "MLGW REMOTE BEO4"
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
        for device in devices:
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

            mp_devices.append(
                BeoSpeaker(
                    mln,
                    device[CONF_MLGW_DEVICE_NAME],
                    room,
                    gateway,
                )
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
    def __init__(self, mln, name, room, gateway: MasterLinkGateway):
        self._mln = mln
        self._ml = None
        self._name = name
        self._room = room
        self._gateway = gateway
        self._pwon = False
        self._source = self._gateway.beolink_source
        self._stop_listening = None

        # Send a dummy command to the device. If the ML_LOG system is operating, then a ML telegram
        # will be sent from the MLGW to the actual device, and that will include the ML device address
        # which is different from the MLN used by MLGW Prototcol. This obviously only works if the ML
        # listener is connected so, only do it if that's the case.
        if self._gateway._connectedML:
            self._gateway.mlgw_send_beo4_cmd(
                self._mln,
                reverse_ml_destselectordict.get("AUDIO SOURCE"),
                BEO4_CMDS.get("<ALL>"),
            )

        # set up a listener for "RELEASE" and "GOTO_SOURCE" commands associated with this speaker to
        # adjust the state. "All Standby" command is managed directly in the MLGW listener in MasterlinkGateway

        def _beospeaker_message_listener(_event: Event):
            if self._ml is not None:
                if _event.data["from_device"] == self._ml:
                    if _event.data["payload_type"] == "RELEASE":
                        _LOGGER.info("ML LOG said: RELEASE id %s" % (self._ml))
                        self._pwon = False
                    elif _event.data["payload_type"] == "GOTO_SOURCE":
                        _LOGGER.info("ML LOG said: GOTO_SOURCE id %s" % (self._ml))
                        self._pwon = True

        if self._gateway._connectedML:
            self._stop_listening = gateway._hass.bus.async_listen(
                MLGW_EVENT_ML_TELEGRAM, _beospeaker_message_listener
            )

    def __del__(self):
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
        self._source = self._gateway.beolink_source
        return self._source

    @property
    def source_list(self):
        """List of available input sources."""
        return self._gateway.available_sources

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
        self.select_source(self._gateway.beolink_source)

    # An alternate is to turn on with volume up which for most devices, turns it on without changing source, but it does nothing on the BeoSound system.
    #        self._pwon = True
    #        self.volume_up()

    def turn_off(self):
        self._pwon = False
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            reverse_ml_destselectordict.get("AUDIO SOURCE"),
            BEO4_CMDS.get("STANDBY"),
        )

    def select_source(self, source):
        self._pwon = True
        self._source = source
        self._gateway.mlgw_send_beo4_cmd_select_source(
            self._mln, reverse_ml_destselectordict.get("AUDIO SOURCE"), self._source
        )

    def volume_up(self):
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            reverse_ml_destselectordict.get("AUDIO SOURCE"),
            BEO4_CMDS.get("VOLUME UP"),
        )

    def volume_down(self):
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            reverse_ml_destselectordict.get("AUDIO SOURCE"),
            BEO4_CMDS.get("VOLUME DOWN"),
        )

    def mute_volume(self, mute):
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            reverse_ml_destselectordict.get("AUDIO SOURCE"),
            BEO4_CMDS.get("MUTE"),
        )

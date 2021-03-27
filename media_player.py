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
    SUPPORT_STOP,
    SUPPORT_TURN_ON,
    SUPPORT_TURN_OFF,
    SUPPORT_SELECT_SOURCE,
    SUPPORT_VOLUME_STEP,
    SUPPORT_VOLUME_MUTE,
    SUPPORT_PREVIOUS_TRACK,
    SUPPORT_NEXT_TRACK,
    SUPPORT_PLAY,
    SUPPORT_PAUSE,
    SUPPORT_SHUFFLE_SET,
    SUPPORT_REPEAT_SET,
)

SUPPORT_BEO = (
    SUPPORT_TURN_ON
    | SUPPORT_TURN_OFF
    | SUPPORT_VOLUME_STEP
    | SUPPORT_SELECT_SOURCE
    | SUPPORT_VOLUME_MUTE
    | SUPPORT_PREVIOUS_TRACK
    | SUPPORT_NEXT_TRACK
    | SUPPORT_STOP
    | SUPPORT_PLAY
    | SUPPORT_PAUSE
    | SUPPORT_SHUFFLE_SET
    | SUPPORT_REPEAT_SET
)

from .const import (
    DOMAIN,
    beo4_commanddict,
    ml_destselectordict,
    reverse_ml_destselectordict,
    reverse_ml_selectedsourcedict,
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
            and _event.data["payload"]["command"] == "Light Timeout"
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
                device_source_names = list()
                for source in product["sources"]:
                    device_source_names.append(source["name"])
                beospeaker = BeoSpeaker(
                    product["MLN"],
                    product["name"],
                    zone["number"],
                    gateway,
                    device_source_names,
                    product["sources"],
                )
                mp_devices.append(beospeaker)
                # Send a dummy command to the device. If the ML_LOG system is operating, then the MLGW will send a ML telegram
                # to the actual device, and that will include the ML device address
                # which is different from the MLN used by MLGW Prototcol. This allows us to reconnect the ML
                # traffic to a device in Home Assistant. It does not work for NL devices so don't send it if
                # there is a Serial Number attached to the device.
                if gateway._connectedML and product.get("sn") is None:
                    device_sequence.append(len(mp_devices) - 1)  # skip NL devices
                    gateway.mlgw_send_beo4_cmd(
                        beospeaker._mln,
                        reverse_ml_destselectordict.get("AUDIO SOURCE"),
                        BEO4_CMDS.get("LIGHT TIMEOUT"),
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
            and _event.data["payload"]["command"] == "Light Timeout"
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

            device_sources = list()
            for _x in gateway.available_sources:
                _source = dict()
                _source["name"] = _x
                _source["destination"] = reverse_ml_destselectordict.get("AUDIO SOURCE")
                _source["format"] = "F0"
                _source["secondary"] = 0
                _source["link"] = 0
                _source["statusID"] = reverse_ml_selectedsourcedict.get(_x)
                _source["selectID"] = BEO4_CMDS.get(_x)
                _source["selectCmds"] = list()
                _source["selectCmds"].append({"cmd": BEO4_CMDS.get(_x), "format": "F0"})
                device_sources.append(_source)

            beospeaker = BeoSpeaker(
                mln,
                device[CONF_MLGW_DEVICE_NAME],
                room,
                gateway,
                gateway.available_sources,
                device_sources,
            )
            beospeaker.set_ml(ml)
            mp_devices.append(beospeaker)
            if gateway._connectedML:
                gateway.mlgw_send_beo4_cmd(
                    beospeaker._mln,
                    reverse_ml_destselectordict.get("AUDIO SOURCE"),
                    BEO4_CMDS.get("LIGHT TIMEOUT"),
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


# #########################################################################################
# convert statusID into selectID (e.g., Radio 0x6f ==> 0x81)


def statusID_to_selectID(statusId):
    return BEO4_CMDS.get(ml_selectedsourcedict.get(statusId).upper())


# #########################################################################################

# BeoSpeaker represents a single MasterLink device on the Masterlink bus. E.g., a speaker like
# BeoSound 3500 or a Masterlink Master device like a receiver or TV (e.g, a Beosound 3000)
# Because the Masterlink has only one active source across all the speakers, the Gateway class
# maintains track of that source, and tells the relevant MLNs about changes if the user is
# only using the MLGW as communication mechanism.
# If ML Bus listening is active, then this class listens to TRACK_INFO and other commands that
# represent the source on the masterlink bus and changes accordingly.
#


class BeoSpeaker(MediaPlayerEntity):
    def __init__(
        self,
        mln,
        name,
        room,
        gateway: MasterLinkGateway,
        source_names: list,
        sources: list,
    ):
        self._mln = mln
        self._ml = None
        self._name = name
        self._room = room
        self._gateway = gateway
        self._pwon = False
        self._source = self._gateway.default_source
        self._stop_listening = None
        self._source_names = source_names
        self._sources = sources

        # information on the current track
        self.clear_media_info()

        # set up a listener for "RELEASE", "STATUS_INFO" and "GOTO_SOURCE" commands associated with this speaker to
        # adjust the state. "All Standby" command is managed directly in the MLGW listener in MasterlinkGateway

        def _beospeaker_message_listener(_event: Event):
            if self._ml is not None:
                if _event.data["from_device"] == self._ml:
                    if _event.data["payload_type"] == "RELEASE":
                        # I am telling the system I am turning off
                        _LOGGER.info("ML LOG said: RELEASE id %s" % (self._ml))
                        self._pwon = False

                        self.clear_media_info()
                    elif _event.data["payload_type"] == "GOTO_SOURCE":
                        # I am telling the system I want a source
                        _LOGGER.info(
                            "ML LOG said: GOTO_SOURCE %s on device %s"
                            % (_event.data["payload"]["source"], self._ml)
                        )
                    ):
                        # reflect that the device is on and store the requested source
                        self._pwon = True
                        self.clear_media_info()
                        self.set_source(_event.data["payload"]["sourceID"])
                    elif (
                        _event.data["payload_type"] == "STATUS_INFO"
                        and self._ml == "VIDEO_MASTER"
                    ):
                        # special case I am a Video Master and my source status info changes
                        if _event.data["to_device"] == "MLGW" or (
                            _event.data["channel_track"] > 0
                            and _event.data["DTV_off"] == 0x00
                        ):
                            self.clear_media_info()
                            self.set_source(_event.data["payload"]["sourceID"])
                        elif _event.data["DTV_off"] == 0x80:
                            self.set_state(STATE_OFF)

                if _event.data["to_device"] == self._ml:
                    if (  # I'm being told to change source
                        _event.data["payload_type"] == "TRACK_INFO"
                        and _event.data["payload"]["subtype"] == "Change Source"
                    ):
                        self.clear_media_info()
                        self.set_source(_event.data["payload"]["sourceID"])
                    elif _event.data["payload_type"] == "TRACK_INFO_LONG":
                        if _event.data["payload"]["channel_track"] > 0:
                            self._media_track = _event.data["payload"]["channel_track"]
                        else:
                            self._media_track = None

                # handle the extended source information and fill in some info for the UI
                if _event.data["to_device"] == "ALL_LINK_DEVICES":
                    if _event.data["payload_type"] == "EXTENDED_SOURCE_INFORMATION":
                        self.clear_media_info()
                        if _event.data["payload"]["info_type"] == 2:
                            self._media_album_name = _event.data["payload"][
                                "info_value"
                            ]
                        elif _event.data["payload"]["info_type"] == 3:
                            self._media_artist = _event.data["payload"]["info_value"]
                        elif _event.data["payload"]["info_type"] == 4:
                            self._media_title = _event.data["payload"]["info_value"]

        if self._gateway._connectedML:
            self._stop_listening = gateway._hass.bus.async_listen(
                MLGW_EVENT_ML_TELEGRAM, _beospeaker_message_listener
            )

    def __del__(self):
        if self._gateway._connectedML:
            self._stop_listening()

    def clear_media_info(self):
        self._media_track = None
        self._media_title = None
        self._media_artist = None
        self._media_album_name = None
        self._media_album_artist = None
        self._media_channel = None

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
        # Name of the current input source.
        return self._source

    @property
    def source_list(self):
        """List of available input sources."""
        return self._source_names

    @property
    def state(self):
        """Return the state of the device."""
        if self._pwon:
            return STATE_ON
        else:
            return STATE_OFF

    @property
    def media_track(self):
        """Track number of current playing media, music track only."""
        return self._media_track

    @property
    def media_title(self):
        """Title of current playing media."""
        return self._media_title

    @property
    def media_artist(self):
        """Artist of current playing media, music track only."""
        return self._media_artist

    @property
    def media_album_name(self):
        """Album name of current playing media, music track only."""
        return self._media_album_name

    @property
    def media_album_artist(self):
        """Album artist of current playing media, music track only."""
        return self._media_album_artist

    @property
    def media_channel(self):
        """Channel currently playing."""
        return self._media_channel

    def set_ml(self, ml: str):
        self._ml = ml

    def set_state(self, _state):
        # to be called by the gateway to set the state to off when there is an event on the ml bus that turns off the device
        if _state == STATE_ON:
            self._pwon = True
        elif _state == STATE_OFF:
            self._pwon = False
            self.clear_media_info()

    def set_source(self, source):
        # to be called by the gateway to set the source (the source is a statusID e.g., radio=0x6f)
        # find the source based on the source ID
        for _x in self._sources:
            if _x["statusID"] == source or _x["selectID"] == statusID_to_selectID(
                source
            ):
                self._source = _x["name"]
                return

        _LOGGER.debug(
            "BeoSpeaker: set_source %s unknown on device %s" % (source, self._name)
        )

    def turn_on(self):
        # when turning on this speaker, use the last known source active on beolink
        # if there is no such source, then use the last source used on this speaker
        # if there is no such source, then use the first source in the available sources list.
        # if there is no source in that list, then do nothing
        if self._gateway.beolink_source is not None:
            for _x in self._sources:
                if _x["statusID"] == reverse_ml_selectedsourcedict.get(
                    self._gateway.beolink_source
                ):
                    self.select_source(_x["name"])
        elif self._source is not None:
            self.select_source(self._source)
        elif len(self._source_names) > 0:
            self.select_source(self._source_names[0])
        _LOGGER.debug(
            "BeoSpeaker: turn on failed %s %s %s"
            % (self._gateway.beolink_source, self._source, self._source_names[0])
        )

    # An alternate is to turn on with volume up which for most devices, turns it on without changing source, but it does nothing on the BeoSound system.
    #        self._pwon = True
    #        self.volume_up()

    def turn_off(self):
        self._pwon = False
        self.clear_media_info()
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            reverse_ml_destselectordict.get("AUDIO SOURCE"),
            BEO4_CMDS.get("STANDBY"),
        )

    def select_source(self, source):
        # look up the full information record for the source
        try:
            _LOGGER.debug("BeoSpeaker: trying to select source: %s", source)
            source_info = self._sources[self._source_names.index(source)]

            self._pwon = True
            self._source = source

            # traditional sources (Beo4)
            if source_info["format"] == "F0":
                dest = source_info["destination"]
                cmd = source_info["selectCmds"][0]["cmd"]
                sec = source_info["secondary"]
                link = source_info["link"]
                if (
                    dest is not None
                    and cmd is not None
                    and sec is not None
                    and link is not None
                ):
                    self._gateway.mlgw_send_beo4_select_source(
                        self._mln, dest, cmd, sec, link
                    )
            elif source_info["format"] == "F20":  # Network Link / BeoOne sources
                unit = source_info["selectCmds"][0]["unit"]
                cmd = source_info["selectCmds"][0]["cmd"]
                network_bit = source_info["networkBit"]
                if unit is not None and cmd is not None and network_bit is not None:
                    self._gateway.mlgw_send_beoremoteone_select_source(
                        self._mln, cmd, unit, network_bit
                    )

        except ValueError:
            _LOGGER.debug("BeoSpeaker: source not known: %s", source)

    def volume_up(self):
        dest = self._sources[self._source_names.index(self._source)]["destination"]
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            dest,
            BEO4_CMDS.get("VOLUME UP"),
        )

    def volume_down(self):
        dest = self._sources[self._source_names.index(self._source)]["destination"]
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            dest,
            BEO4_CMDS.get("VOLUME DOWN"),
        )

    def mute_volume(self, mute):
        dest = self._sources[self._source_names.index(self._source)]["destination"]
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            dest,
            BEO4_CMDS.get("MUTE"),
        )

    def media_play(self):
        """Send play command."""
        dest = self._sources[self._source_names.index(self._source)]["destination"]
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            dest,
            BEO4_CMDS.get("GO / PLAY"),
        )

    def media_stop(self):
        """Send stop command."""
        dest = self._sources[self._source_names.index(self._source)]["destination"]
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            dest,
            BEO4_CMDS.get("STOP"),
        )

    def media_pause(self):
        """Send stop command."""
        dest = self._sources[self._source_names.index(self._source)]["destination"]
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            dest,
            BEO4_CMDS.get("STOP"),
        )

    def media_previous_track(self):
        """Send previous track command."""
        dest = self._sources[self._source_names.index(self._source)]["destination"]
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            dest,
            BEO4_CMDS.get("STEP DOWN"),
        )

    def media_next_track(self):
        """Send next track command."""
        dest = self._sources[self._source_names.index(self._source)]["destination"]
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            dest,
            BEO4_CMDS.get("STEP UP"),
        )

    def set_shuffle(self, shuffle):
        """Enable/disable shuffle mode."""
        dest = self._sources[self._source_names.index(self._source)]["destination"]
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            dest,
            BEO4_CMDS.get("SHIFT-1 / RANDOM"),
        )

    def set_repeat(self, repeat):
        """Set repeat mode."""
        dest = self._sources[self._source_names.index(self._source)]["destination"]
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            dest,
            BEO4_CMDS.get("SHIFT-3 / REPEAT"),
        )

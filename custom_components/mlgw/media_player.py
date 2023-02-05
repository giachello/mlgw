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
from homeassistant.helpers.entity import DeviceInfo
import asyncio

from homeassistant.const import (
    STATE_OFF,
    STATE_PLAYING,
    STATE_PAUSED,
)

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaType,
)


from .const import (
    DOMAIN,
    reverse_ml_destselectordict,
    reverse_ml_selectedsourcedict,
    ml_selectedsourcedict,
    ml_selectedsource_type_dict,
    BEO4_CMDS,
    MLGW_GATEWAY,
    MLGW_GATEWAY_CONFIGURATION_DATA,
    MLGW_EVENT_ML_TELEGRAM,
    ML_ID_TIMEOUT,
)

from .gateway import MasterLinkGateway

SUPPORT_BEO = (
    MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.NEXT_TRACK
)

_LOGGER = logging.getLogger(__name__)

# Set up the Media_player devices. there are two ways, through the manual configuration in configuration.yaml
# and through a config flow that automatically reads the devices list from the mlgw.

# #########################################################################################
#
async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
):
    """Add MLGW devices through Config Entry"""

    hass.data.setdefault(DOMAIN, {})

    mlgw_configurationdata = hass.data[DOMAIN][config_entry.entry_id][
        MLGW_GATEWAY_CONFIGURATION_DATA
    ]
    gateway: MasterLinkGateway = hass.data[DOMAIN][config_entry.entry_id][MLGW_GATEWAY]
    serial = hass.data[DOMAIN][config_entry.entry_id]["serial"]
    _LOGGER.debug("Serial (async_setup_entry): %s", serial)

    await async_create_devices(
        mlgw_configurationdata, gateway, async_add_entities, serial
    )


# #########################################################################################
#
async def async_setup_platform(hass, config, add_devices, discovery_info=None):
    """Add MLGW devices through manual configuration"""
    hass.data.setdefault(DOMAIN, {})

    mlgw_configurationdata = hass.data[DOMAIN][MLGW_GATEWAY_CONFIGURATION_DATA]
    gateway: MasterLinkGateway = hass.data[DOMAIN][MLGW_GATEWAY]

    await async_create_devices(mlgw_configurationdata, gateway, add_devices, DOMAIN)


# #########################################################################################
#
async def async_create_devices(
    mlgw_configurationdata, gateway, async_add_entities, serial=""
):
    """Read the configuration data from the gateway, and create the devices"""
    mp_devices = list()

    device_sequence = list()

    ml_listener_iteration: int = 0
    ml_devices_scanned: int = 0
    stop_listening: CALLBACK_TYPE = None

    def _message_listener(_event: Event):
        nonlocal ml_listener_iteration
        if (
            _event.data["from_device"] == "MLGW"
            and _event.data["payload_type"] == "MLGW_REMOTE_BEO4"
            and _event.data["payload"]["command"] == "Light Timeout"
        ):
            _LOGGER.info(
                "ML LOG returned ML id %s for MLN %s",
                _event.data["to_device"],
                str(gateway.devices[device_sequence[ml_listener_iteration]].mln),
            )
            gateway.devices[device_sequence[ml_listener_iteration]].set_ml(
                _event.data["to_device"]
            )
            ml_listener_iteration = ml_listener_iteration + 1

    if gateway.connectedMLGW:

        # listen to ML messages to track down the actual ML id of the device
        if gateway.use_mllog:
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
                    zone["name"],
                    gateway,
                    device_source_names,
                    product["sources"],
                    serial=serial,
                )
                mp_devices.append(beospeaker)
                # Send a dummy command to the device. If the ML_LOG system is operating, then the MLGW will send a ML telegram
                # to the actual device, and that will include the ML device address
                # which is different from the MLN used by MLGW Prototcol. This allows us to reconnect the ML
                # traffic to a device in Home Assistant. It does not work for NL devices so don't send it if
                # there is a Serial Number attached to the device.
                if gateway.connectedML and product.get("sn") is None:
                    device_sequence.append(len(mp_devices) - 1)  # skip NL devices
                    gateway.mlgw_send_beo4_cmd(
                        beospeaker.mln,
                        reverse_ml_destselectordict.get("AUDIO SOURCE"),
                        BEO4_CMDS.get("LIGHT TIMEOUT"),
                    )
                    ml_devices_scanned = ml_devices_scanned + 1

        async_add_entities(mp_devices)
        gateway.set_devices(
            mp_devices
        )  # tell the gateway the list of devices connected to it.

        # wait for 10 seconds or until all the devices have reported back their ML address
        if gateway.connectedML:
            waiting_for = 0.0
            while (
                ml_listener_iteration < ml_devices_scanned
                and waiting_for < ML_ID_TIMEOUT
            ):
                await asyncio.sleep(0.1)
                waiting_for = waiting_for + 0.1
            stop_listening()  # clean up the listener for the device codes.
            _LOGGER.info("Got back the ML IDs")

    else:
        _LOGGER.error("MLGW Not connected while trying to add media_player devices")


# #########################################################################################


def statusID_to_selectID(statusId):
    """Convert statusID into selectID (e.g., Radio 0x6f ==> 0x81)"""
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
    """BeoSpeaker is a Media Player that represents one MasterLink device (e.g., a speaker or a receiver or TV)."""

    def __init__(
        self,
        mln,
        name,
        room_number,
        room_name,
        gateway: MasterLinkGateway,
        source_names: list,
        sources: list,
        serial="",
    ):
        self._mln = mln
        self._ml = None
        self._name = name
        self._roomNumber = room_number
        self._roomName = room_name
        self._gateway = gateway
        self._pwon = False
        self._playing = False
        self._source = self._gateway.default_source
        self._stop_listening = None
        self._source_names = source_names
        self._sources = sources
        self._serial = serial
        self._unique_id = f"{self._serial}-media_player-{self._mln}"

        # information on the current track
        self.clear_media_info()

        # set up a listener for "RELEASE", "STATUS_INFO" and "GOTO_SOURCE" commands associated with this speaker to
        # adjust the state. "All Standby" command is managed directly in the MLGW listener in MasterlinkGateway

        def _beospeaker_message_listener(_event: Event):
            if self._ml is not None:
                # The message comes from me --------------------------------------------------
                if _event.data["from_device"] == self._ml:

                    # I am telling the system I am turning off
                    if _event.data["payload_type"] == "RELEASE":
                        _LOGGER.debug("ML: RELEASE id %s", self._ml)
                        self._pwon = False
                        self._playing = False
                        self.clear_media_info()

                    # I am telling the system I want a source
                    elif _event.data["payload_type"] == "GOTO_SOURCE":
                        _LOGGER.debug(
                            "ML: GOTO_SOURCE %s on device %s",
                            _event.data["payload"]["source"],
                            self._ml,
                        )
                        # reflect that the device is on and store the requested source
                        self._pwon = True
                        self._playing = True

                        self.clear_media_info()
                        self.set_source(_event.data["payload"]["sourceID"])
                        self.set_source_info(
                            _event.data["payload"]["sourceID"],
                            _event.data["payload"]["channel_track"],
                        )

                    # I am updating the Status of this source
                    elif _event.data["payload_type"] == "STATUS_INFO":
                        # special case: I am a Video Device and my source status info changes
                        # the weird logic tries to figure out multiple source devices.
                        if _event.data["to_device"] == "MLGW" or (
                            self._ml == "VIDEO_MASTER"
                            and _event.data["payload"]["channel_track"] > 0x00
                            and _event.data["payload"]["channel_track"] < 0xFFFF
                            and _event.data["payload"]["local_source"] == 0x00
                        ):
                            self.set_source(_event.data["payload"]["sourceID"])
                            if _event.data["payload"]["source"] != "DVD" or (
                                _event.data["payload"]["source"] == "DVD"
                                and _event.data["payload"]["local_source"] != 0x00
                            ):
                                self.set_source_info(
                                    _event.data["payload"]["sourceID"],
                                    _event.data["payload"]["channel_track"],
                                )
                        # If I am an Audio Master
                        if self._ml == "AUDIO_MASTER":
                            self.set_source(_event.data["payload"]["sourceID"])
                            self.set_source_info(
                                _event.data["payload"]["sourceID"],
                                _event.data["payload"]["channel_track"],
                            )

                    elif _event.data["payload_type"] == "VIDEO_TRACK_INFO":
                        if (
                            _event.data["payload"]["channel_track"] > 0x00
                            and _event.data["payload"]["channel_track"] < 0xFF
                        ):
                            self.set_source_info(
                                _event.data["payload"]["sourceID"],
                                _event.data["payload"]["channel_track"],
                            )

                # The message is directed to me -------------------------------------------------
                if _event.data["to_device"] == self._ml:
                    # I'm being told to change source
                    if (
                        _event.data["payload_type"] == "TRACK_INFO"
                        and _event.data["payload"]["subtype"] == "Change Source"
                    ):
                        self.clear_media_info()
                        self.set_source(_event.data["payload"]["sourceID"])
                    # I received a Track Information Long packet - which means I am on
                    elif _event.data["payload_type"] == "TRACK_INFO_LONG":
                        if (
                            _event.data["payload"]["channel_track"] > 0
                            and _event.data["payload"]["channel_track"] < 0xFF
                        ) or _event.data["payload"]["activity"] == "Playing":
                            self.set_source_info(
                                _event.data["payload"]["sourceID"],
                                _event.data["payload"]["channel_track"],
                            )

                # handle the extended source information and fill in some info for the UI
                if _event.data["from_device"] == "AUDIO_MASTER":
                    if _event.data["payload_type"] == "DISPLAY_SOURCE":
                        if self.source is not None:
                            _statusID = self._sources[
                                self._source_names.index(self.source)
                            ]["statusID"]
                            if _statusID in ml_selectedsource_type_dict["AUDIO"]:
                                self.clear_media_info()
                                self._media_content_type = MediaType.MUSIC
                    elif _event.data["payload_type"] == "EXTENDED_SOURCE_INFORMATION":
                        if self.source is not None:
                            _statusID = self._sources[
                                self._source_names.index(self.source)
                            ]["statusID"]
                            if (
                                _statusID != 0x97
                                and _statusID in ml_selectedsource_type_dict["AUDIO"]
                            ):
                                if (
                                    _event.data["orig_src"] == "RADIO"
                                    or _event.data["orig_src"] == "N.RADIO"
                                ):
                                    if _event.data["payload"]["info_type"] == 2:
                                        self._media_artist = _event.data["payload"][
                                            "info_value"
                                        ]
                                    elif _event.data["payload"]["info_type"] == 3:
                                        _country = _event.data["payload"]["info_value"]
                                        self._media_artist += f" / {_country}"
                                    elif _event.data["payload"]["info_type"] == 4:
                                        self._media_title = _event.data["payload"][
                                            "info_value"
                                        ]
                                elif (
                                    _event.data["orig_src"] == "A.MEM"
                                    or _event.data["orig_src"] == "N.MUSIC"
                                    or _event.data["orig_src"] == "CD"
                                ):
                                    if _event.data["payload"]["info_type"] == 2:
                                        self._media_album_name = _event.data["payload"][
                                            "info_value"
                                        ]
                                    elif _event.data["payload"]["info_type"] == 3:
                                        self._media_artist = _event.data["payload"][
                                            "info_value"
                                        ]
                                    elif _event.data["payload"]["info_type"] == 4:
                                        self._media_title = _event.data["payload"][
                                            "info_value"
                                        ]

                # setup STATE ON/OFF through "beo4 key" events on the ML bus
                if _event.data["to_device"] == "AUDIO_MASTER":
                    if _event.data["payload_type"] == "BEO4_KEY":
                        if self.source is not None:
                            _statusID = self._sources[
                                self._source_names.index(self.source)
                            ]["statusID"]
                            if _statusID == _event.data["payload"]["sourceID"]:
                                if _event.data["payload"]["command"] == "Go / Play":
                                    self._playing = True
                                elif _event.data["payload"]["command"] == "Stop":
                                    self._playing = False

        if self._gateway._use_mllog:
            self._stop_listening = gateway._hass.bus.async_listen(
                MLGW_EVENT_ML_TELEGRAM, _beospeaker_message_listener
            )

    def __del__(self):
        if self._gateway._connectedML:
            self._stop_listening()

    def clear_media_info(self):
        """Clear out the information about the current track/channel."""
        self._media_content_type = None
        self._media_track = None
        self._media_title = None
        self._media_artist = None
        self._media_album_name = None
        self._media_album_artist = None
        self._media_channel = None
        self._media_image_url = None

    def set_source_info(self, sourceID, channel_track=0):
        """fill in channel number, name and icon for the UI, if the source ID matches the current source"""
        if self._source is None:
            return
        _statusID = self._sources[self._source_names.index(self._source)]["statusID"]
        if _statusID == sourceID:
            if self._playing == False:
                self._playing = True

            if channel_track == self._media_channel:  # no cange
                return

            # set the media type
            if _statusID in ml_selectedsource_type_dict["VIDEO"]:
                self.clear_media_info()
                self._media_content_type = MediaType.VIDEO
            elif _statusID in ml_selectedsource_type_dict["AUDIO"]:
                self.clear_media_info()
                self._media_content_type = MediaType.MUSIC
            else:
                return

            # for channel based sources, set channel number and image
            if ml_selectedsourcedict[_statusID] in ["TV", "DTV", "RADIO", "N.RADIO"]:
                self._media_channel = channel_track
                if channel_track > 0x00:
                    _ch_name, _ch_icon = self.ch_number_to_name_and_icon(
                        self._source, self._media_channel
                    )
                    self._media_title = f"{self._media_channel} - {_ch_name or '?'}"
                    self._media_image_url = _ch_icon or ""
                else:
                    self._media_title = None
            elif ml_selectedsourcedict[_statusID] in ["DVD", "DVD2", "CD", "N.MUSIC"]:
                self._media_track = channel_track
                self._media_title = f"Track {self._media_track}"

            self.schedule_update_ha_state()

    def ch_number_to_name_and_icon(self, source, channel_track):
        """look up the caption corresponding to the command number of the favorites list"""
        try:
            source_info = self._sources[self._source_names.index(source)]
            if "channels" in source_info:  # check if the source has favorites
                for _c in source_info["channels"]:
                    # the channel number is expressed a sequence of digits interspersed by delay commands and ended by a select code.

                    ch = ""
                    for _x in _c["selectSEQ"]:
                        if type(_x) == int and (int(_x) >= 0 and int(_x) <= 9):
                            ch += str(_x)  # assembly of the channel number
                    ch_number = int(ch)
                    if ch_number == channel_track:
                        return (_c["name"], _c["icon"])

            _LOGGER.debug("BeoSpeaker: %s does not have Favourites", source)

        except ValueError:
            _LOGGER.debug("BeoSpeaker: source not known: %s", source)

        return (None, None)

    @property
    def name(self):
        return self._name

    @property
    def ml(self):
        return self._ml

    @property
    def mln(self):
        return self._mln

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, self._mln)},
            name=self._name,
            manufacturer="Bang & Olufsen",
            via_device=(DOMAIN, self._serial),
            suggested_area=self._roomName,
        )

    @property
    def friendly_name(self):
        """Friendly Name of the device."""
        return self._name.capwords(sep="_")

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        support = SUPPORT_BEO
        if self.source is not None:
            _statusID = self._sources[self._source_names.index(self.source)]["statusID"]
            if (
                _statusID in ml_selectedsource_type_dict["AUDIO_PAUSABLE"]
                or _statusID in ml_selectedsource_type_dict["VIDEO_PAUSABLE"]
            ):
                support = (
                    support
                    | MediaPlayerEntityFeature.STOP
                    | MediaPlayerEntityFeature.PLAY
                    | MediaPlayerEntityFeature.PAUSE
                    | MediaPlayerEntityFeature.SHUFFLE_SET
                    | MediaPlayerEntityFeature.REPEAT_SET
                )
        return support

    @property
    def source(self):
        """Name of the current input source."""
        return self._source

    @property
    def source_list(self):
        """List of available input sources."""
        return self._source_names

    @property
    def state(self):
        """Return the state of the device."""
        if self._pwon:
            if self._playing:
                return STATE_PLAYING
            else:
                return STATE_PAUSED
        else:
            return STATE_OFF

    @property
    def media_content_type(self):
        """Content type of current playing media."""
        return self._media_content_type

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
    def media_image_url(self):
        """Image url of current playing media."""
        return self._media_image_url

    @property
    def media_channel(self):
        """Channel currently playing."""
        return self._media_channel

    def set_ml(self, ml: str):
        self._ml = ml

    def set_state(self, _state):
        # to be called by the gateway to set the state to off when there is an event on the ml bus that turns off the device
        if _state == STATE_PLAYING:
            self._pwon = True
            self._playing = True
        elif _state == STATE_OFF:
            self._pwon = False
            self._playing = False
            self.clear_media_info()

    def set_source(self, source):
        """to be called by the gateway to set the source (the source is a statusID e.g., radio=0x6f)"""
        # find the source based on the source ID
        for _x in self._sources:
            if _x["statusID"] == source or _x["selectID"] == statusID_to_selectID(
                source
            ):
                self._source = _x["name"]
                self.schedule_update_ha_state()
                return

        _LOGGER.debug(
            "BeoSpeaker: set_source %s unknown on device %s", source, self._name
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
                    return

        if self._source is not None:
            self.select_source(self._source)
            return

        if len(self._source_names) > 0:
            self.select_source(self._source_names[0])
        _LOGGER.debug(
            "BeoSpeaker: turn on failed %s %s %s"
            % (self._gateway.beolink_source, self._source, self._source_names[0])
        )

    def turn_off(self):
        self._pwon = False
        self._playing = False
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
            self._playing = True
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
        self._pwon = True
        self._playing = True

    def media_stop(self):
        """Send stop command."""
        dest = self._sources[self._source_names.index(self._source)]["destination"]
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            dest,
            BEO4_CMDS.get("STOP"),
        )
        self._pwon = True
        self._playing = False

    def media_pause(self):
        """Send stop command."""
        dest = self._sources[self._source_names.index(self._source)]["destination"]
        self._gateway.mlgw_send_beo4_cmd(
            self._mln,
            dest,
            BEO4_CMDS.get("STOP"),
        )
        self._pwon = True
        self._playing = False

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

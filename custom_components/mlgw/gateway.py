"""
The gateway to interact with a Bang & Olufsen MasterLink Gateway or BeoLink Gateway.
"""
import asyncio
from datetime import datetime
import logging

import telnetlib
import socket
import threading
import time

from homeassistant.core import callback, HomeAssistant
from homeassistant.const import (
    STATE_OFF,
    STATE_PLAYING,
    EVENT_HOMEASSISTANT_STOP,
)

from .const import *

_LOGGER = logging.getLogger(__name__)


class MasterLinkGateway:
    """Masterlink gateway to interact with a MasterLink Gateway http://mlgw.bang-olufsen.dk/source/documents/mlgw_2.24b/MlgwProto0240.pdf ."""

    def __init__(
        self,
        host,
        port,
        user,
        password,
        mlgw_configurationdata,
        use_mllog,
        default_source,
        available_sources,
        hass,
        config_entry_id=None,
    ):
        """Initialize the MLGW gateway."""
        # for both connections
        self._host = host
        self._user = user
        self._password = password
        # for the ML (Telnet CLI) connection
        self._use_mllog = use_mllog
        self._connectedML = False
        self._tn = None
        # for the MLGW (Port 9000) connection
        self._port = port
        self._socket = None
        self.buffersize = 1024
        self._connectedMLGW = False
        self.stopped = threading.Event()
        self.brokensocket = threading.Event()
        self.stopped.clear()
        self.brokensocket.clear()

        # to manage the sources and devices
        self._default_source = default_source
        self._beolink_source = default_source
        self._available_sources = available_sources
        self._devices = None
        self._hass: HomeAssistant = hass
        self._config_entry_id = config_entry_id
        self._serial = None
        self._mlgw_configurationdata = mlgw_configurationdata

    @property
    def connectedMLGW(self):
        """True if the MLGW is connected."""
        return self._connectedMLGW

    @property
    def connectedML(self):
        """True if the ML CLI is connected."""
        return self._connectedML

    @property
    def devices(self):
        """Return a list of BeoSpeaker devices."""
        return self._devices

    @property
    def use_mllog(self):
        """True if we are trying to use the ML CLI function."""
        return self._use_mllog

    @property
    def beolink_source(self):
        """Returns the last known active source on the MasterLink bus, or None if there isn't one."""
        return self._beolink_source

    @property
    def default_source(self):
        """Returns the default source for the gateway (E.g., "CD"), or None if there is no default source."""
        return self._default_source

    @property
    def available_sources(self):
        """Returns a list of the available sources on the gateway (only used for manual configuration)."""
        return self._available_sources

    def set_devices(self, devices):
        """Set the list of devices configured on the gateway."""
        self._devices = devices

    async def terminate_async(self):
        """Terminate the gateway connections. Sets a flag that the listen threads look
        at to determine when to quit"""
        self.stopped.set()

    async def async_ml_connect(self):
        """Async version of the mlgw connect function"""
        loop = asyncio.get_event_loop()
        # start mlgw_connect(self) in a separate thread, suspend
        # the current coroutine, and resume when it's done
        await loop.run_in_executor(None, self.ml_connect, self)

    def ml_connect(self):
        """Connect the undocumented MasterLink stream."""
        _LOGGER.debug("Attempt to connect to ML CLI: %s", self._host)
        self._connectedML = False

        try:
            self._tn = telnetlib.Telnet(self._host)

            line = self._tn.read_until(b"login: ", 3)
            if line[-7:] != b"login: ":
                _LOGGER.debug("Unexpected login prompt: %s", line)
                raise ConnectionError
            # put some small pauses to see if we can avoid occasional login problems
            time.sleep(0.1)
            self._tn.write(self._password.encode("ascii") + b"\n")
            time.sleep(0.1)

            # Try to read until we hit the command prompt.
            # BLGW has a "BLGW >" prompt. MLGW has a "MLGW >" prompt
            attempts = 0
            max_attempts = 3
            while attempts < max_attempts:
                line = self._tn.read_until(
                    b"LGW >", 2
                )  # the third line should be the prompt
                attempts = attempts + 1
                if line[-5:] == b"LGW >":
                    break
                time.sleep(0.5)

            if line[-5:] != b"LGW >":
                _LOGGER.debug("Unexpected CLI prompt: %s", line)
                raise ConnectionError

            # Enter the undocumented Masterlink Logging function
            self._tn.write(b"_MLLOG ONLINE\r\n")

            self._connectedML = True
            _LOGGER.debug("Connected to ML CLI: %s", self._host)

            return True

        except EOFError as exc:
            _LOGGER.warning("Error opening ML CLI connection to: %s", exc)
            raise

        except ConnectionError as exc:
            _LOGGER.warning("Failed to connect to ML CLI: %s", exc)
            raise

    def ml_close(self):
        """Close the connection to the MasterLink stream."""
        if self._connectedML:
            self._connectedML = False
            try:
                self._tn.close()
                self._tn = None
            except OSError:
                _LOGGER.error("Error closing ML CLI")
            _LOGGER.debug("Closed connection to ML CLI")

    # This is the thread function to manage the ML CLI connection
    def ml_thread(self):
        """The thread that manages the connection with the MLGW API"""
        connect_retries = 0
        max_connect_retries = 10
        retry_delay = 60
        while connect_retries < max_connect_retries and not self.stopped.isSet():
            try:
                # if not connected, then connect
                if not self._connectedML:
                    self.ml_connect()
            except (ConnectionError, OSError):
                # wait for 1 minute, max 10 times
                time.sleep(retry_delay)
                connect_retries = connect_retries + 1
                continue
            try:
                connect_retries = 0  # if connect was successful, reset the attempts
                self.ml_listen()
                self.ml_close()
            except (ConnectionResetError, OSError, EOFError):
                self.ml_close()
                # wait for 1 minute, max 10 times
                time.sleep(retry_delay)
                connect_retries = connect_retries + 1
                continue
            except KeyboardInterrupt:
                break
        _LOGGER.warning("Shutting down ML CLI thread")

    def ml_listen(self):
        """Receive notification about incoming event from the ML connection."""
        _recvtimeout = 5  # timeout recv every 5 sec
        _lastping = 0  # how many seconds ago was the last ping.

        input_bytes = ""
        while not self.stopped.isSet():
            try:  # nonblocking read from the connection
                input_bytes = input_bytes + self._tn.read_until(
                    b"\n", _recvtimeout
                ).decode("ascii")

            except EOFError:
                _LOGGER.error("ML CLI Thread: EOF Error")
                self.ml_close()
                raise

            if input_bytes.find("\n") > 0:  # if there is a full line

                line = input_bytes[0 : input_bytes.find("\n")]
                input_bytes = input_bytes[input_bytes.find("\n") + 1 :]

                items = line.split()
                try:
                    date_time_obj = datetime.strptime(items[0], "%Y%m%d-%H:%M:%S:%f:")
                    telegram = bytearray()

                    for x in range(1, len(items)):
                        telegram.append(int(items[x][:-1], base=16))

                    encoded_telegram = decode_ml_to_dict(telegram)
                    encoded_telegram["timestamp"] = date_time_obj.isoformat()
                    encoded_telegram["bytes"] = "".join(
                        "{:02x}".format(x) for x in telegram
                    )

                    # try to find the mln of the from_device and to_device
                    if self._devices is not None:
                        for x in self._devices:
                            if x._ml == encoded_telegram["from_device"]:
                                encoded_telegram["from_mln"] = x._mln
                                encoded_telegram["from_name"] = x.name
                                encoded_telegram["from_entity_id"] = x.entity_id
                            if x._ml == encoded_telegram["to_device"]:
                                encoded_telegram["to_mln"] = x._mln
                                encoded_telegram["to_name"] = x.name
                                encoded_telegram["to_entity_id"] = x.entity_id
                    # if a GOTO Source telegram is received, set the beolink source to it
                    # this only tracks the primary beolink source, doesn't track local sources
                    if encoded_telegram["payload_type"] == "GOTO_SOURCE":
                        self._beolink_source = encoded_telegram["payload"]["source"]

                    _LOGGER.info("ML: %s", encoded_telegram)

                    self._hass.add_job(
                        self._notify_incoming_ML_telegram, encoded_telegram
                    )
                except ValueError:
                    continue
                except IndexError:
                    _LOGGER.error("ML CLI Thread: error parsing telegram: %s", line)
                    continue
            else:  # else sleep a bit and then continue reading
                #                time.sleep(0.5)
                _lastping = _lastping + _recvtimeout
                # Ping the gateway to test the connection every 10 minutes
                if _lastping >= 600:
                    _LOGGER.debug("Sent NUL ping to ML")
                    self._tn.write(bytes([0]))
                    _lastping = 0
                continue

    @callback
    def _notify_incoming_ML_telegram(self, telegram):  # pylint: disable=invalid-name
        """Notify hass when an incoming ML message is received."""
        self._hass.bus.async_fire(MLGW_EVENT_ML_TELEGRAM, telegram)

    @callback
    def _notify_incoming_MLGW_telegram(self, telegram):  # pylint: disable=invalid-name
        """Notify hass when an incoming ML message is received."""
        self._hass.bus.async_fire(MLGW_EVENT_MLGW_TELEGRAM, telegram)

    async def async_mlgw_connect(self):
        """Async version of the mlgw connect function"""
        loop = asyncio.get_event_loop()
        # start mlgw_connect(self) in a separate thread, suspend
        # the current coroutine, and resume when it's done
        await loop.run_in_executor(None, self.mlgw_connect, self)

    def mlgw_connect(self):
        """Open tcp connection to the mlgw API."""
        _LOGGER.debug("Trying to connect to MLGW API")
        self._connectedMLGW = False

        # open socket to masterlink gateway
        try:
            self._socket: socket.socket = socket.socket(
                socket.AF_INET, socket.SOCK_STREAM
            )
            self.brokensocket.clear()
            self._socket.connect((self._host, self._port))
        except OSError as ex:
            self._socket = None
            _LOGGER.error("Error connecting to MLGW API %s: %s", self._host, ex)
            raise

        self._connectedMLGW = True
        _LOGGER.debug(
            "MLGW API connection successful to %s port: %s",
            self._host,
            str(self._port),
        )

    def mlgw_close(self):
        """Close connection to mlgw"""
        if self._connectedMLGW:
            self._connectedMLGW = False
            if self._socket is not None:
                try:
                    self._socket.shutdown(socket.SHUT_RDWR)
                    self._socket.close()
                    self._socket = None
                except OSError:
                    _LOGGER.error("Error closing connection to MLGW API")
                    return
                _LOGGER.debug("Closed connection to MLGW API")

    def mlgw_login(self):
        """Login to the gateway using username and password."""
        _LOGGER.debug("MLGW: Trying to login")
        if self._connectedMLGW:
            wrkstr = self._user + chr(0x00) + self._password
            payload = bytearray()
            for c in wrkstr:
                payload.append(ord(c))
            self.mlgw_send(0x30, payload)  # login Request

    def mlgw_ping(self):
        """Send a ping to the gateway (used for keepalive)."""
        self.mlgw_send(0x36, "")

    def mlgw_send(self, msg_type, payload):
        """Send a message to the gateway."""
        if self._connectedMLGW:
            _telegram = bytearray()
            _telegram.append(0x01)  # byte[0] SOH
            _telegram.append(msg_type)  # byte[1] msg_type
            _telegram.append(len(payload))  # byte[2] Length
            _telegram.append(0x00)  # byte[3] Spare
            for p in payload:
                _telegram.append(p)
            try:
                self._socket.sendall(_telegram)
            except (OSError, BrokenPipeError):
                self.brokensocket.set()
                _LOGGER.warning("MLGW: socket broken pipe")
                return

            _LOGGER.debug(
                "MLGW: >send %s: %s",
                _getpayloadtypestr(msg_type),
                _getpayloadstr(_telegram),
            )

    ## Send Beo4 command to mlgw
    def mlgw_send_beo4_cmd(self, mln, dest, cmd, sec_source=0x00, link=0x00):
        """Send BEO4 command."""
        _payload = bytearray()
        _payload.append(mln)  # byte[0] MLN
        _payload.append(dest)  # byte[1] Dest-Sel (0x00, 0x01, 0x05, 0x0f)
        _payload.append(cmd)  # byte[2] Beo4 Command
        _payload.append(sec_source)  # byte[3] Sec-Source
        _payload.append(link)  # byte[4] Link
        self.mlgw_send(0x01, _payload)

    ## Send BeoRemote One command to mlgw
    def mlgw_send_beoremoteone_cmd(self, mln, cmd, network_bit: bool):
        """Send BEO Remote One command."""
        _payload = bytearray()
        _payload.append(mln)  # byte[0] MLN
        _payload.append(cmd)  # byte[1] Beo4 Command
        _payload.append(0x00)  # byte[2] AV (needs to be 0)
        _payload.append(network_bit)  # byte[3] Network_bit
        self.mlgw_send(0x06, _payload)

    ## Send BeoRemote One Source Select to mlgw
    def mlgw_send_beoremoteone_select_source(self, mln, cmd, unit, network_bit: bool):
        """Send BEO Remote One Select command."""
        _payload = bytearray()
        _payload.append(mln)  # byte[0] MLN
        _payload.append(cmd)  # byte[1] Beo4 Command
        _payload.append(unit)  # byte[2] Unit
        _payload.append(0x00)  # byte[3] AV (needs to be 0)
        _payload.append(network_bit)  # byte[4] Network_bit
        self.mlgw_send(0x07, _payload)

    def mlgw_send_beo4_select_source(self, mln, dest, source, sec_source, link):
        """Send Beo4 commmand and store the source name.
        Should change to use a source ID."""
        self._beolink_source = _dictsanitize(beo4_commanddict, source).upper()
        self.mlgw_send_beo4_cmd(mln, dest, source, sec_source, link)

    def mlgw_send_virtual_btn_press(self, btn, act=0x01):
        """Send a virtual button press."""
        self.mlgw_send(0x20, [btn, act])

    def mlgw_send_all_standby(self):
        """Send an "All Standby" command (turns off the entire B&O System)."""
        self.mlgw_send_beo4_cmd(1, 0x0F, 0x0C)

    def mlgw_get_serial(self):
        """Send the get serial command to the mlgw and store it in the self._serial property."""
        if self._connectedMLGW:
            # Request serial number
            self.mlgw_send(MLGW_PL.get("REQUEST SERIAL NUMBER"), "")
            (_, self._serial) = self.mlgw_receive()
            _LOGGER.info("MLGW: Serial number is %s", self._serial)  # info
        return

    def mlgw_thread(self):
        """The thread that manages the connection with the MLGW API"""
        connect_retries = 0
        max_connect_retries = 10
        retry_delay = 60
        while connect_retries < max_connect_retries and not self.stopped.isSet():
            try:
                # if not connected, then connect
                if not self._connectedMLGW:
                    self.mlgw_connect()
                self.mlgw_ping()  # force a ping so that the MLGW will request authentication
            except OSError:
                # wait for 1 minute, max 10 times
                time.sleep(retry_delay)
                connect_retries = connect_retries + 1
                continue
            try:
                connect_retries = 0  # if connect was successful, reset the attempts
                self._mlgw_listen()
                self.mlgw_close()
            except (ConnectionResetError, OSError):
                self.mlgw_close()
                # wait for 1 minute, max 10 times
                time.sleep(retry_delay)
                connect_retries = connect_retries + 1
                continue
            except KeyboardInterrupt:
                break

        # after 10 attempts, or if HA asked to stop it, stop the thread
        self.mlgw_close()
        _LOGGER.warning("Shutting down MLGW API thread")

    def _mlgw_listen(self):
        """Listen and manage the MLGW connection"""
        _recvtimeout = 5  # timeout recv every 5 sec
        _lastping = 0  # how many seconds ago was the last ping.
        self._socket.settimeout(_recvtimeout)

        while not (self.stopped.isSet() or self.brokensocket.isSet()):
            response = None
            try:
                response = self._socket.recv(self.buffersize)
            except KeyboardInterrupt:
                _LOGGER.warning("MLGW: keyboard interrupt in listen thread")
                raise
            except socket.timeout:
                _lastping = _lastping + _recvtimeout
                # Ping the gateway to test the connection every 10 minutes
                if _lastping >= 600:
                    self.mlgw_ping()
                    _lastping = 0
                continue
            except (ConnectionResetError, OSError):
                _LOGGER.warning("MLGW: socket connection reset")
                raise

            if response is not None and response != b"":
                # Decode response. Response[0] is SOH, or 0x01
                msg_byte = response[1]
                msg_type = _getpayloadtypestr(msg_byte)
                msg_payload = _getpayloadstr(response)

                _LOGGER.debug("MLGW: Msg type: %s: %s", msg_type, msg_payload)

                if msg_byte == 0x02:  # Source status
                    sourceMLN = response[4]
                    beolink_source = _getselectedsourcestr(response[5]).upper()
                    sourceMediumPosition = _hexword(response[6], response[7])
                    sourcePosition = _hexword(response[8], response[9])
                    sourceActivity = _getdictstr(mlgw_sourceactivitydict, response[10])
                    pictureFormat = _getdictstr(ml_pictureformatdict, response[11])
                    decoded = dict()
                    decoded["payload_type"] = "source_status"
                    decoded["source_mln"] = sourceMLN
                    decoded["source"] = beolink_source
                    decoded["source_medium_position"] = sourceMediumPosition
                    decoded["source_position"] = sourcePosition
                    decoded["source_activity"] = sourceActivity
                    decoded["picture_format"] = pictureFormat
                    self._hass.add_job(self._notify_incoming_MLGW_telegram, decoded)
                    # remember the new source
                    if sourceActivity not in ("Standby", "Unknown"):
                        self._beolink_source = beolink_source
                    # change the source of the MLN
                    # reporting the change
                    # not sure this works in all situations
                    sourcePositionInt = response[8] * 256 + response[9]
                    if (
                        sourceActivity != "Standby"
                        and sourceActivity != "Unknown"
                        and sourcePositionInt > 0
                        and self._devices is not None
                    ):
                        for x in self._devices:
                            if x._mln == sourceMLN:
                                x.set_source(response[5])

                elif msg_byte == 0x03:  # Picture and Sound status
                    decoded = dict()
                    decoded["payload_type"] = "pict_sound_status"
                    sourceMLN = response[4]
                    decoded["source_mln"] = sourceMLN
                    decoded["sound_status"] = _getdictstr(
                        mlgw_soundstatusdict, response[5]
                    )
                    decoded["speaker_mode"] = _getdictstr(
                        mlgw_speakermodedict, response[6]
                    )
                    decoded["volume"] = int(response[7])
                    decoded["screen1_mute"] = _getdictstr(
                        mlgw_screenmutedict, response[8]
                    )
                    decoded["screen1_active"] = _getdictstr(
                        mlgw_screenactivedict, response[9]
                    )
                    decoded["screen2_mute"] = _getdictstr(
                        mlgw_screenmutedict, response[10]
                    )
                    decoded["screen2_active"] = _getdictstr(
                        mlgw_screenactivedict, response[11]
                    )
                    decoded["cinema_mode"] = _getdictstr(
                        mlgw_cinemamodedict, response[12]
                    )
                    decoded["stereo_mode"] = _getdictstr(
                        mlgw_stereoindicatordict, response[13]
                    )
                    self._hass.add_job(self._notify_incoming_MLGW_telegram, decoded)
                    # if the device picture status is on, then turn on the state in the media_player
                    if self._devices is not None and (
                        response[9] == 0x01 or response[11] == 0x01
                    ):
                        for x in self._devices:
                            if x._mln == sourceMLN:
                                x.set_state(STATE_PLAYING)

                elif msg_byte == 0x04:  # Light / Control command
                    lcroomnumber = response[4]
                    lcroom = _getroomstr(lcroomnumber)
                    if self._mlgw_configurationdata:
                        for zone in self._mlgw_configurationdata["zones"]:
                            if zone["number"] == lcroomnumber:
                                if "name" in zone:
                                    lcroom = zone["name"]
                                break
                    lctype = _getdictstr(mlgw_lctypedict, response[5])
                    lccommand = _getbeo4commandstr(response[6])
                    decoded = dict()
                    decoded["payload_type"] = "light_control_event"
                    decoded["room"] = lcroom
                    decoded["type"] = lctype
                    decoded["command"] = lccommand
                    self._hass.add_job(self._notify_incoming_MLGW_telegram, decoded)

                elif msg_byte == 0x05:  # All Standby
                    if self._devices is not None:
                        # set all connected devices state to off
                        for i in self._devices:
                            i.set_state(STATE_OFF)
                    decoded = dict()
                    decoded["payload_type"] = "all_standby"
                    self._hass.add_job(self._notify_incoming_MLGW_telegram, decoded)

                elif msg_byte == 0x20:  # Virtual Button event
                    virtual_btn = response[4]
                    if len(response) < 5:
                        virtual_action = _getvirtualactionstr(0x01)
                    else:
                        virtual_action = _getvirtualactionstr(response[5])
                    _LOGGER.debug(
                        "MLGW: Virtual button: %s %s", virtual_btn, virtual_action
                    )
                    decoded = dict()
                    decoded["payload_type"] = "virtual_button"
                    decoded["button"] = virtual_btn
                    decoded["action"] = virtual_action
                    self._hass.add_job(self._notify_incoming_MLGW_telegram, decoded)

                elif msg_byte == 0x31:  # Login Status
                    if msg_payload == "FAIL":
                        _LOGGER.debug(
                            "MLGW: MLGW protocol Password required to %s", self._host
                        )
                        self.mlgw_login()
                    elif msg_payload == "OK":
                        _LOGGER.debug(
                            "MLGW: MLGW protocol Login successful to %s", self._host
                        )
                        self.mlgw_get_serial()

                # elif msg_byte == 0x37:  # Pong (Ping response)
                #     _LOGGER.debug("mlgw: pong")

                elif msg_byte == 0x38:  # Configuration changed notification
                    _LOGGER.info("MLGW: configuration changed, reloading component")
                    service_data = {"entry_id": self._config_entry_id}
                    self._hass.services.call(
                        "homeassistant", "reload_config_entry", service_data, False
                    )

    def mlgw_receive(self):
        """Receive message from MLGW.
        Returns a tuple: (payload type, payload)."""
        if self._connectedMLGW:
            try:
                _mlgwdata = self._socket.recv(self.buffersize)
            except socket.timeout:
                pass
            except KeyboardInterrupt:
                _LOGGER.error("MLGW: KeyboardInterrupt, terminating")
                self.mlgw_close()

            _payloadstr = _getpayloadstr(_mlgwdata)
            if _mlgwdata[0] != 0x01:
                _LOGGER.error("MLGW: Received telegram with SOH byte <> 0x01")
            if _mlgwdata[3] != 0x00:
                _LOGGER.error("MLGW: Received telegram with spare byte <> 0x00")
            _LOGGER.debug(
                "MLGW: <recv: %s: %s",
                _getpayloadtypestr(_mlgwdata[1]),
                str(_payloadstr),
            )
            return (_mlgwdata[1], str(_payloadstr))


# ########################################################################################
# ##### Create the gateway instance and set up listners to destroy it if needed


async def create_mlgw_gateway(
    hass: HomeAssistant,
    host,
    user,
    password,
    mlgw_configurationdata,
    use_mllog,
    config_entry_id=None,
    default_source=None,
    available_sources=None,
):
    """Create the mlgw gateway.
    Hass: Home Assistant instance
    Host / User / Password: is the login information
    mlgw_configurationdata: the configuration information taken from the mlgw_pservices.json API
    use_mllog: True: use the undocumented ML functionality
    config_entry_id: the configuration entry ID of this gateway so it can be reloaded if a config change notification is received from the MLGW.
    default_soruce, available_sources: the static list of sources from configuration yaml
    """
    gateway = MasterLinkGateway(
        host,
        mlgw_configurationdata["port"],
        user,
        password,
        mlgw_configurationdata,
        use_mllog,
        default_source,
        available_sources,
        hass,
        config_entry_id,
    )

    # Start the threads to connect the two endpoints
    threading.Thread(target=gateway.mlgw_thread).start()

    if use_mllog is True:
        threading.Thread(target=gateway.ml_thread).start()

    def _stop_listener(_event):
        gateway.stopped.set()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop_listener)

    async def wait_to_connect():
        # Wait until gateway is connected on both MLGW and ML (if we are using it)
        while (gateway.connectedMLGW is False) or (
            gateway.connectedML is False and gateway.use_mllog is True
        ):
            await asyncio.sleep(1)

    # wait to connect at most 20 seconds
    try:
        await asyncio.wait_for(wait_to_connect(), timeout=20)
    except TimeoutError:
        print("MLGW: timeout connecting with the MLGW!")
        return None

    return gateway


# ########################################################################################
# ##### Utility functions


def _hexbyte(byte):
    resultstr = hex(byte)
    if byte < 16:
        resultstr = resultstr[:2] + "0" + resultstr[2]
    return resultstr


def _hexword(byte1, byte2):
    resultstr = _hexbyte(byte2)
    resultstr = _hexbyte(byte1) + resultstr[2:]
    return resultstr


def _dictsanitize(d, s):
    result = d.get(s)
    if result is None:
        result = "UNKNOWN (type=" + _hexbyte(s) + ")"
    return str(result)


# ########################################################################################


def decode_device(d):
    """Decode the Device ID."""
    if d == 0xC0:
        return "VIDEO_MASTER"
    if d == 0xC1:
        return "AUDIO_MASTER"
    if d == 0xC2:
        return "SOURCE_CENTER"  # also known as 'SLAVE_DEVICE' in older documentation
    if d == 0x81:
        return "ALL_AUDIO_LINK_DEVICES"
    if d == 0x82:
        return "ALL_VIDEO_LINK_DEVICES"
    if d == 0x83:
        return "ALL_LINK_DEVICES"
    if d == 0x80:
        return "ALL"
    if d == 0xF0:
        return "MLGW"
    else:
        return hex(d)


# ########################################################################################
# ##### Decode Masterlink Protocol packet to a serializable dict


def decode_ml_to_dict(telegram) -> dict:
    """Convert a binary ML packet into a dict representation of the message.
    telegram: the binary package"""
    decoded = dict()
    decoded["from_device"] = decode_device(telegram[1])
    decoded["to_device"] = decode_device(telegram[0])
    decoded["type"] = _dictsanitize(ml_telegram_type_dict, telegram[3])
    decoded["src_dest"] = _dictsanitize(ml_selectedsourcedict, telegram[4])
    decoded["orig_src"] = _dictsanitize(ml_selectedsourcedict, telegram[5])
    decoded["payload_type"] = _dictsanitize(ml_command_type_dict, telegram[7])
    decoded["payload_len"] = telegram[8]
    decoded["payload"] = dict()

    # source status info
    # TTFF__TYDSOS__PTLLPS SR____LS______SLSHTR__ACSTPI________________________TRTR______
    if telegram[7] == 0x87:
        decoded["payload"]["source"] = _dictsanitize(
            ml_selectedsourcedict, telegram[10]
        )
        decoded["payload"]["sourceID"] = telegram[10]
        decoded["payload"]["local_source"] = telegram[13]
        decoded["payload"]["source_medium"] = _hexword(telegram[18], telegram[17])
        decoded["payload"]["channel_track"] = (
            telegram[19] if telegram[8] < 27 else (telegram[36] * 256 + telegram[37])
        )
        decoded["payload"]["activity"] = _dictsanitize(ml_state_dict, telegram[21])
        decoded["payload"]["source_type"] = telegram[22]
        decoded["payload"]["picture_identifier"] = _dictsanitize(
            ml_pictureformatdict, telegram[23]
        )

    # display source information
    if telegram[7] == 0x06:
        _s = ""
        for i in range(0, telegram[8] - 5):
            _s = _s + chr(telegram[i + 15])
        decoded["payload"]["display_source"] = _s.rstrip()
    # extended source information
    if telegram[7] == 0x0B:
        decoded["payload"]["info_type"] = telegram[10]
        _s = ""
        for i in range(0, telegram[8] - 14):
            _s = _s + chr(telegram[i + 24])
        decoded["payload"]["info_value"] = _s
    # beo4 command
    if telegram[7] == 0x0D:
        decoded["payload"]["source"] = _dictsanitize(
            ml_selectedsourcedict, telegram[10]
        )
        decoded["payload"]["sourceID"] = telegram[10]
        decoded["payload"]["command"] = _dictsanitize(beo4_commanddict, telegram[11])
    # audio track info long
    if telegram[7] == 0x82:
        decoded["payload"]["source"] = _dictsanitize(
            ml_selectedsourcedict, telegram[11]
        )
        decoded["payload"]["sourceID"] = telegram[11]
        decoded["payload"]["channel_track"] = telegram[12]
        decoded["payload"]["activity"] = _dictsanitize(ml_state_dict, telegram[13])
    # video track info
    if telegram[7] == 0x94:
        decoded["payload"]["source"] = _dictsanitize(
            ml_selectedsourcedict, telegram[13]
        )
        decoded["payload"]["sourceID"] = telegram[13]
        decoded["payload"]["channel_track"] = telegram[11] * 256 + telegram[12]
        decoded["payload"]["activity"] = _dictsanitize(ml_state_dict, telegram[14])
    # track change info
    if telegram[7] == 0x44:
        if telegram[9] == 0x07:
            decoded["payload"]["subtype"] = "Change Source"
            decoded["payload"]["prev_source"] = _dictsanitize(
                ml_selectedsourcedict, telegram[11]
            )
            decoded["payload"]["prev_sourceID"] = telegram[11]
            decoded["payload"]["source"] = _dictsanitize(
                ml_selectedsourcedict, telegram[22]
            )
            decoded["payload"]["sourceID"] = telegram[22]
        elif telegram[9] == 0x05:
            decoded["payload"]["subtype"] = "Current Source"
            decoded["payload"]["source"] = _dictsanitize(
                ml_selectedsourcedict, telegram[11]
            )
            decoded["payload"]["sourceID"] = telegram[11]
        else:
            decoded["payload"]["subtype"] = "Undefined"
    # goto source
    if telegram[7] == 0x45:
        decoded["payload"]["source"] = _dictsanitize(
            ml_selectedsourcedict, telegram[11]
        )
        decoded["payload"]["sourceID"] = telegram[11]
        decoded["payload"]["channel_track"] = telegram[12]
    # remote request
    if telegram[7] == 0x20:
        decoded["payload"]["command"] = _dictsanitize(beo4_commanddict, telegram[14])
        decoded["payload"]["dest_selector"] = _dictsanitize(
            ml_destselectordict, telegram[11]
        )
    # request_key
    if telegram[7] == 0x5C:
        if telegram[9] == 0x01:
            decoded["payload"]["subtype"] = "Request Key"
        elif telegram[9] == 0x02:
            decoded["payload"]["subtype"] = "Transfter Key"
        elif telegram[9] == 0x04:
            decoded["payload"]["subtype"] = "Key Received"
        elif telegram[9] == 0x05:
            decoded["payload"]["subtype"] = "Timeout"
        else:
            decoded["payload"]["subtype"] = "Undefined"
    # request distributed audio source
    if telegram[7] == 0x08:
        if telegram[9] == 0x01:
            decoded["payload"]["subtype"] = "Request Source"
        elif telegram[9] == 0x04:
            decoded["payload"]["subtype"] = "No Source"
        elif telegram[9] == 0x06:
            decoded["payload"]["subtype"] = "Source Active"
            decoded["payload"]["source"] = _dictsanitize(
                ml_selectedsourcedict, telegram[13]
            )
            decoded["payload"]["sourceID"] = telegram[13]
        else:
            decoded["payload"]["subtype"] = "Undefined"
    # request local audio source
    if telegram[7] == 0x30:
        if telegram[9] == 0x02:
            decoded["payload"]["subtype"] = "Request Source"
        elif telegram[9] == 0x04:
            decoded["payload"]["subtype"] = "No Source"
        elif telegram[9] == 0x06:
            decoded["payload"]["subtype"] = "Source Active"
            decoded["payload"]["source"] = _dictsanitize(
                ml_selectedsourcedict, telegram[11]
            )
            decoded["payload"]["sourceID"] = telegram[11]
        else:
            decoded["payload"]["subtype"] = "Undefined"
    return decoded


# ########################################################################################
# ##### Decode MLGW Protocol packet to readable string

## Get decoded string for mlgw packet's payload type
#
def _getpayloadtypestr(payloadtype):
    result = mlgw_payloadtypedict.get(payloadtype)
    if result is None:
        result = "UNKNOWN (type=" + _hexbyte(payloadtype) + ")"
    return str(result)


def _getroomstr(room):
    result = "Room=" + str(room)
    return result


def _getmlnstr(mln):
    result = "MLN=" + str(mln)
    return result


def _getbeo4commandstr(command):
    result = beo4_commanddict.get(command)
    if result is None:
        result = "Cmd=" + _hexbyte(command)
    return result


def _getvirtualactionstr(action):
    result = mlgw_virtualactiondict.get(action)
    if result is None:
        result = "Action=" + _hexbyte(action)
    return result


def _getselectedsourcestr(source):
    result = ml_selectedsourcedict.get(source)
    if result is None:
        result = "Src=" + _hexbyte(source)
    return result


def _getspeakermodestr(source):
    result = mlgw_speakermodedict.get(source)
    if result is None:
        result = "mode=" + _hexbyte(source)
    return result


def _getdictstr(mydict, mykey):
    result = mydict.get(mykey)
    if result is None:
        result = _hexbyte(mykey)
    return result


## Get decoded string for a mlgw packet
#
#   The raw message (mlgw packet) is handed to this function.
#   The result of this function is a human readable string, describing the content
#   of the mlgw packet
#
#  @param message   raw mlgw telegram
#  @returns         telegram as a human readable string
#
def _getpayloadstr(message):
    if message[2] == 0:  # payload length is 0
        resultstr = "[No payload]"
    elif message[1] == 0x01:  # Beo4 Command
        resultstr = _getmlnstr(message[4])
        resultstr = resultstr + " " + _hexbyte(message[5])
        resultstr = resultstr + " " + _getbeo4commandstr(message[6])

    elif message[1] == 0x02:  # Source Status
        resultstr = _getmlnstr(message[4])
        resultstr = resultstr + " " + _getselectedsourcestr(message[5])
        resultstr = resultstr + " " + _hexword(message[6], message[7])
        resultstr = resultstr + " " + _hexword(message[8], message[9])
        resultstr = resultstr + " " + _getdictstr(mlgw_sourceactivitydict, message[10])
        resultstr = resultstr + " " + _getdictstr(ml_pictureformatdict, message[11])

    elif message[1] == 0x03:  # Picture and Sound Status
        resultstr = _getmlnstr(message[4])
        if message[5] != 0x00:
            resultstr = resultstr + " " + _getdictstr(mlgw_soundstatusdict, message[5])
        resultstr = resultstr + " " + _getdictstr(mlgw_speakermodedict, message[6])
        resultstr = resultstr + " Vol=" + str(message[7])
        resultstr = resultstr + " Scrn1:" + _getdictstr(mlgw_screenmutedict, message[8])
        resultstr = resultstr + ", " + _getdictstr(mlgw_screenactivedict, message[9])
        resultstr = (
            resultstr + " Scrn2:" + _getdictstr(mlgw_screenmutedict, message[10])
        )
        resultstr = resultstr + ", " + _getdictstr(mlgw_screenactivedict, message[11])
        resultstr = resultstr + " " + _getdictstr(mlgw_cinemamodedict, message[12])
        resultstr = resultstr + " " + _getdictstr(mlgw_stereoindicatordict, message[13])

    elif message[1] == 0x04:  # Light and Control command
        resultstr = (
            _getroomstr(message[4])
            + " "
            + _getdictstr(mlgw_lctypedict, message[5])
            + " "
            + _getbeo4commandstr(message[6])
        )

    elif message[1] == 0x30:  # Login request
        wrk = message[4 : 4 + message[2]]
        for i in range(0, message[2]):
            if wrk[i] == 0:
                wrk[i] = 0x7F
        wrk = wrk.decode("utf-8")
        resultstr = wrk.split(chr(0x7F))[0] + " / " + wrk.split(chr(0x7F))[1]

    elif message[1] == 0x31:  # Login status
        resultstr = _getdictstr(mlgw_loginstatusdict, message[4])

    elif message[1] == 0x3A:  # Serial Number
        resultstr = message[4 : 4 + message[2]].decode("utf-8")

    else:  # Display raw payload
        resultstr = ""
        for i in range(0, message[2]):
            if i > 0:
                resultstr = resultstr + " "
            resultstr = resultstr + _hexbyte(message[4 + i])
    return resultstr

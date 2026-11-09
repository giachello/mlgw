"""Verify remote routing against the installed Home Assistant entity API."""

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

# Load the platform without integration __init__ and its unrelated dependencies.
ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_mlgw_remote_tests"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT / "custom_components" / "mlgw")]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(
    f"{PACKAGE}.remote", ROOT / "custom_components" / "mlgw" / "remote.py"
)
remote = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = remote
spec.loader.exec_module(remote)


class RemoteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gateway = SimpleNamespace(connectedMLGW=True, mlgw_send_beo4_cmd=Mock())
        self.product = {"MLN": 7, "name": "Beo3500"}
        self.zone = {"name": "Living room", "products": [self.product]}
        self.lock = asyncio.Lock()
        self.entity = remote.MasterLinkRemote(
            self.gateway, self.product, self.zone, "serial", self.lock
        )

        async def executor(function, *args):
            function(*args)

        self.entity.hass = SimpleNamespace(async_add_executor_job=executor)

    async def test_identity_and_no_state(self):
        self.assertEqual(self.entity.name, "Beo3500")
        self.assertEqual(self.entity.unique_id, "serial-remote-7")
        self.assertEqual(self.entity.device_info["identifiers"], {("mlgw", 7)})
        self.assertIsNone(self.entity.is_on)
        self.assertFalse(self.entity.should_poll)
        self.assertEqual(self.entity.supported_features, 0)

    async def test_every_command_and_default_arguments(self):
        for name, code in remote.COMMANDS.items():
            await self.entity.async_send_command([f" {name.lower()} "], delay_secs=0)
            self.gateway.mlgw_send_beo4_cmd.assert_called_with(7, 1, code, 0, 0)
        self.assertEqual(remote.COMMANDS["SELECT"], 0x13)
        self.assertEqual(remote.COMMANDS["MEDIUM SELECT"], 0x3F)
        self.assertEqual(remote.COMMANDS["0"], 0)
        self.assertEqual(remote.COMMANDS["SHIFT-PLAY"], 0x22)

    async def test_destinations_secondary_and_link(self):
        for code, name in remote.ml_destselectordict.items():
            await self.entity.async_send_command("Stop", device=f"destination={name}")
            self.gateway.mlgw_send_beo4_cmd.assert_called_with(7, code, 0x36, 0, 0)
        await self.entity.async_send_command(
            ["V.Mem"], device="destination=v.tape/v.mem;secondary=true;link=1"
        )
        self.gateway.mlgw_send_beo4_cmd.assert_called_with(7, 5, 0x85, 1, 1)

    async def test_invalid_calls_send_nothing(self):
        for options in ("destination=bad", "secondary=true", "link=yes",
                        "link=1;link=0", "bad=1", "audio", ""):
            with self.assertRaises(ServiceValidationError):
                await self.entity.async_send_command(["Radio"], device=options)
        for commands in (["Radio", "bad"], ["<all>"]):
            with self.assertRaises(ServiceValidationError):
                await self.entity.async_send_command(commands)
        with self.assertRaises(ServiceValidationError):
            await self.entity.async_send_command(["Radio"], hold_secs=1)
        self.gateway.mlgw_send_beo4_cmd.assert_not_called()

    async def test_repeats_delays_and_zero_repeats(self):
        with patch.object(remote.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await self.entity.async_send_command(["0", "1"], num_repeats=2, delay_secs=0.2)
        self.assertEqual(
            [call.args[2] for call in self.gateway.mlgw_send_beo4_cmd.call_args_list],
            [0, 1, 0, 1],
        )
        self.assertEqual(sleep.await_count, 3)
        self.gateway.mlgw_send_beo4_cmd.reset_mock()
        await self.entity.async_send_command(["Radio"], num_repeats=0)
        self.gateway.mlgw_send_beo4_cmd.assert_not_called()

    async def test_disconnected(self):
        self.gateway.connectedMLGW = False
        with self.assertRaises(HomeAssistantError):
            await self.entity.async_send_command(["Radio"])
        self.gateway.mlgw_send_beo4_cmd.assert_not_called()

    async def test_concurrent_sequences_share_lock(self):
        other = remote.MasterLinkRemote(
            self.gateway, {"MLN": 8, "name": "Other"}, self.zone, "serial", self.lock
        )
        other.hass = self.entity.hass
        await asyncio.gather(
            self.entity.async_send_command(["0", "1"], delay_secs=0.001),
            other.async_send_command(["2", "3"], delay_secs=0),
        )
        self.assertEqual(
            [(call.args[0], call.args[2]) for call in self.gateway.mlgw_send_beo4_cmd.call_args_list],
            [(7, 0), (7, 1), (8, 2), (8, 3)],
        )

    async def test_entry_and_yaml_setup_without_media_players(self):
        data = {
            remote.MLGW_GATEWAY: self.gateway,
            remote.MLGW_GATEWAY_CONFIGURATION_DATA: {"zones": [self.zone]},
            "serial": "serial",
        }
        hass = SimpleNamespace(data={"mlgw": {"entry": data}})
        add = Mock()
        await remote.async_setup_entry(hass, SimpleNamespace(entry_id="entry"), add)
        self.assertEqual(len(add.call_args.args[0]), 1)
        self.assertEqual(add.call_args.args[0][0].name, "Beo3500")
        first_lock = add.call_args.args[0][0]._lock
        await remote.async_setup_entry(hass, SimpleNamespace(entry_id="entry"), add)
        self.assertIs(add.call_args.args[0][0]._lock, first_lock)
        hass.data["mlgw"] = data
        await remote.async_setup_platform(hass, {}, add, {})
        self.assertEqual(add.call_args.args[0][0].unique_id, "mlgw-remote-7")
        add.reset_mock()
        await remote.async_setup_platform(hass, {}, add)
        add.assert_not_called()


if __name__ == "__main__":
    unittest.main()

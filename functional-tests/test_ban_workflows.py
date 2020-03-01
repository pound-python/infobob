import re
import contextlib

import pytest
import pytest_twisted as pytest_tw
from twisted import logger

import clients
import utils
from config import PROJECT_CHAN, GENERICS, INFOTEST, WEBUI_PORT


LOG = logger.Logger()



@contextlib.asynccontextmanager
async def clean_channel(channel: clients.ChannelController):
    for extantBan in await channel.retrieveBans():
        channel.unsetBan(extantBan.mask)
    assert (await channel.retrieveBans()) == []
    yield
    for extantBan in await channel.retrieveBans():
        channel.unsetBan(extantBan.mask)
    await utils.sleep(1)
    assert (await channel.retrieveBans()) == []


@pytest_tw.ensureDeferred
async def test_ban_discovery(monitor, start_infobob, chanop):
    async with clean_channel(chanop.channel(PROJECT_CHAN)):
        mask = 'naughty!user@client.example.org'
        chanop.channel(PROJECT_CHAN).setBan(mask)
        webui = await start_infobob()
        await utils.sleep(2)
        recorded = await webui.getCurrentBans(PROJECT_CHAN)
        thatBan = next((ban for ban in recorded if ban['mask'] == mask), None)
        assert thatBan is not None, f'{mask} not found in {bans!r}'
        assert thatBan['reason'].startswith('ban pulled from banlist')
        assert thatBan['setBy'] == chanop.nickname

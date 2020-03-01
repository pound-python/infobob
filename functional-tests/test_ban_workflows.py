import re

import pytest
import pytest_twisted as pytest_tw
from twisted import logger

import utils
from config import PROJECT_CHAN, GENERICS, INFOTEST, WEBUI_PORT


LOG = logger.Logger()


@pytest_tw.ensureDeferred
async def test_ban_discovery(monitor, start_infobob, chanop):
    project = chanop.channel(PROJECT_CHAN)
    mask = 'naughty!user@client.example.org'
    project.setBan(mask)
    try:
        webui = await start_infobob()
        await utils.sleep(2)
        recorded = await webui.getCurrentBans(PROJECT_CHAN)
        thatBan = next((ban for ban in recorded if ban['mask'] == mask), None)
        assert thatBan is not None, f'{mask} not found in {bans!r}'
        assert thatBan['reason'].startswith('ban pulled from banlist')
        assert thatBan['setBy'] == chanop.nickname
    finally:
        project.unsetBan(mask)
        await utils.sleep(0.1)

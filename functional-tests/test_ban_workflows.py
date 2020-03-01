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
    """
    Infobob discovers bans that it did not see happen, by populating
    its database when it joins a channel.
    """
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


@pytest_tw.ensureDeferred
async def test_record_ban_unset(monitor, start_infobob, chanop):
    """
    Infobob notices when an operator unsets a ban.

    It will notify the chanop in a PM when the ban was set and by
    whom, and update its database to record the ban as unset.
    """
    project = chanop.channel(PROJECT_CHAN)
    async with clean_channel(project):
        mask = 'naughty!user@client.example.org'
        project.setBan(mask)
        webui = await start_infobob()
        await utils.sleep(2)
        project.unsetBan(mask)
        await utils.sleep(2)

        messages = chanop.getPrivateMessages(sender=INFOTEST.nickname)
        assert len(messages) == 1
        [pm] = messages
        pattern = ' '.join([
            re.escape(rf'fyi: {chanop.nickname} set'),
            r'"\+b naughty\S+"',
            re.escape(f'on {PROJECT_CHAN}'),
            '.*',
        ])
        # TODO: Also verify "when the ban was set" information in message.
        assert re.fullmatch(pattern, pm.text)

        recorded = await webui.getExpiredBans(PROJECT_CHAN)
        thatBan = next((ban for ban in recorded if ban['mask'] == mask), None)
        assert (
            thatBan['setBy']
            == thatBan['unset']['unsetBy']
            == chanop.nickname
        )

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
        # Given the bot is not in the channel,
        # And a new ban is set on the channel,
        mask = 'naughty!user@client.example.org'
        chanop.channel(PROJECT_CHAN).setBan(mask)
        # When the bot joins the channel,
        webui = await start_infobob()
        await utils.sleep(2)
        # Then the ban shows as active in the webui,
        recorded = await webui.getCurrentBans(PROJECT_CHAN)
        thatBan = next((ban for ban in recorded if ban['mask'] == mask), None)
        assert thatBan is not None, f'{mask} not found in {recorded!r}'
        # And the reason says the ban was pulled from the channel,
        assert thatBan['reason'].startswith('ban pulled from banlist')
        # ...and who it was set by.
        assert thatBan['setBy'] == chanop.nickname


@pytest_tw.ensureDeferred
async def test_record_ban_unset(monitor, start_infobob, chanop):
    """
    Infobob notices when an operator unsets a ban.

    It will notify the chanop in a PM when the ban was set and by
    whom, and update its database to record the ban as unset.
    """
    # Given a chanop is in the channel,
    project = chanop.channel(PROJECT_CHAN)
    async with clean_channel(project):
        # And a ban is set on the channel,
        mask = 'naughty!user@client.example.org'
        project.setBan(mask)
        webui = await start_infobob()
        await utils.sleep(2)
        # When the chanop unsets the ban,
        project.unsetBan(mask)
        await utils.sleep(2)

        # Then the bot notifies the chanop in a PM,
        messages = chanop.getPrivateMessages(sender=INFOTEST.nickname)
        assert len(messages) == 1
        [pm] = messages
        # TODO: And the message says when the ban was set,
        # And the message says by whom the ban was set,
        pattern = ' '.join([
            re.escape(rf'fyi: {chanop.nickname} set'),
            r'"\+b naughty\S+"',
            re.escape(f'on {PROJECT_CHAN}'),
            '.*',
        ])
        assert re.fullmatch(pattern, pm.text)

        # And the ban will show as expired in the webui.
        recorded = await webui.getExpiredBans(PROJECT_CHAN)
        thatBan = next((ban for ban in recorded if ban['mask'] == mask), None)
        assert (
            thatBan['setBy']
            == thatBan['unset']['unsetBy']
            == chanop.nickname
        )

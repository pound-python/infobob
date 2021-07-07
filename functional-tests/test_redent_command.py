import re

import pytest
import pytest_twisted as pytest_tw
import utils
from config import PROJECT_CHAN, GENERICS, INFOTEST


class IncompletelyImplemented(NotImplementedError):
    pass


@pytest.mark.xfail(raises=IncompletelyImplemented, strict=True)
@pytest_tw.ensureDeferred
async def test_redent(monitor, start_infobob, joinfake):
    """
    Infobob reformats oneliner code with indentation when requested.
    """
    # Given two users are in the channel,
    sender, target = GENERICS[:2]
    await start_infobob()
    senderCtrl = await joinfake(sender, [PROJECT_CHAN])
    targetCtrl = await joinfake(target, [PROJECT_CHAN])
    # When user A tells the bot to redent some code for user B,
    code = 'try: dostuff();; except ItBroke as e: failAnnoyingly()'
    message = f'{INFOTEST.nickname}, redent {target.nickname} {code}'
    senderCtrl.channel(PROJECT_CHAN).say(message)
    await utils.sleep(3)
    # Then the bot parses the code, uploads it to a pastebin,
    # And the bot mentions user B with a link containing the code,
    chan = monitor.channel(PROJECT_CHAN)
    botmessages = chan.getMessages(sender=INFOTEST.nickname)
    assert len(botmessages) == 1
    [msg] = botmessages
    assert msg.sender == INFOTEST.nickname
    message_pattern = re.escape(target.nickname) + r', (https?://\S+)\s*'
    assert re.fullmatch(message_pattern, msg.text)
    # TODO: And the code is reformatted with indentation.
    raise IncompletelyImplemented

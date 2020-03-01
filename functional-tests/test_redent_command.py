import re

import pytest
import pytest_twisted as pytest_tw
from twisted import logger

import utils
from config import PROJECT_CHAN, GENERICS, INFOTEST


LOG = logger.Logger()

class IncompletelyImplemented(NotImplementedError):
    pass


@pytest.mark.xfail(raises=IncompletelyImplemented, strict=True)
@pytest_tw.ensureDeferred
async def test_redent(monitor, start_infobob, joinfake):
    sender, target = GENERICS[:2]
    await start_infobob()
    LOG.info('bot join complete')
    senderCtrl = await joinfake(sender, [PROJECT_CHAN])
    LOG.info('sender {nick} join complete', nick=sender.nickname)
    targetCtrl = await joinfake(target, [PROJECT_CHAN])
    LOG.info('target {nick} join complete', nick=target.nickname)
    code = 'try: dostuff();; except ItBroke as e: failAnnoyingly()'
    message = f'{INFOTEST.nickname}, redent {target.nickname} {code}'
    senderCtrl.channel(PROJECT_CHAN).say(message)
    LOG.info('sender said {message}', message=message)
    await utils.sleep(3)
    LOG.info('sleep complete')
    chan = monitor.channel(PROJECT_CHAN)
    botmessages = chan.getMessages(sender=INFOTEST.nickname)
    assert len(botmessages) == 1
    [msg] = botmessages
    assert msg.sender == INFOTEST.nickname
    message_pattern = re.escape(target.nickname) + r', (https?://\S+)\s*'
    assert re.fullmatch(message_pattern, msg.text)
    # TODO: Check if paste content matches
    raise IncompletelyImplemented

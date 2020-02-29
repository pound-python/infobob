import pytest
import pytest_twisted as pytest_tw

from config import PROJECT_CHAN, GENERICS, INFOTEST


@pytest.mark.skip(reason='message monitor not yet implemented')
@pytest_tw.ensureDeferred
async def test_redent(monitor, start_infobob, joinfake):
    sender, target = GENERICS[:2]
    await start_infobob()
    senderCtrl = await joinfake(sender, [PROJECT_CHAN])
    targetCtrl = await joinfake(target, [PROJECT_CHAN])
    code = 'try: dostuff();; except ItBroke as e: failAnnoyingly()'
    message = f'{INFOTEST.nickname}, redent {target.nickname} {code}'
    senderCtrl.say(PROJECT_CHAN, message)

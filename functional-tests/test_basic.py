import pytest_twisted as pytest_tw

import utils
from config import ALL_CHANS, INFOTEST


@pytest_tw.inlineCallbacks
def test_infobob_basic(monitor, start_infobob):
    yield start_infobob()
    yield utils.sleep(3)
    for channelName in ALL_CHANS:
        chan = monitor.getChannelState(channelName)
        assert INFOTEST.nickname in chan

import pytest_twisted as pytest_tw
from twisted.internet import task

from config import ALL_CHANS


@pytest_tw.inlineCallbacks
def test_infobob_basic(monitor, start_infobob):
    from twisted.internet import reactor

    channelsconf = {cname: {'have_ops': True} for cname in ALL_CHANS}
    yield start_infobob(channelsconf=channelsconf, autojoin=ALL_CHANS)
    yield task.deferLater(reactor, 10, lambda: None)

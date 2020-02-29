import pytest_twisted as pytest_tw
from twisted.internet import task


@pytest_tw.inlineCallbacks
def test_infobob_basic(monitor, start_infobob):
    from twisted.internet import reactor

    yield start_infobob()
    yield task.deferLater(reactor, 10, lambda: None)

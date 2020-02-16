import os
import json
import pathlib
import sqlite3
import time
import sys

import pytest
import pytest_twisted as pytest_tw
from twisted.internet import protocol
from twisted.internet import defer
from twisted.internet import task
from twisted.internet import endpoints
from twisted import logger

from config import (
    # User credentials
    MONITOR,
    CHANOP,
    # IRC server stuff
    ALL_CHANS,
    IRCD_HOST,
    IRCD_PORT,
)
import clients
from runner import InfobobRunner


@pytest_tw.inlineCallbacks
def test_infobob_basic(start_infobob):
    from twisted.internet import reactor

    # This part could be used to test ComposedIRCController and friends...
    endpoint = endpoints.TCP4ClientEndpoint(
        reactor, IRCD_HOST, IRCD_PORT, timeout=5)
    monitor = yield clients.joinFakeUser(
        endpoint, MONITOR.nickname, MONITOR.password,
        autojoin=ALL_CHANS)

    channelsconf = {cname: {'have_ops': True} for cname in ALL_CHANS}
    start_infobob(channelsconf=channelsconf, autojoin=ALL_CHANS)
    yield task.deferLater(reactor, 10, lambda: None)

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
from twisted.web import xmlrpc
from twisted import logger

from config import (
    SCHEMA_PATH,
    INFOBOB_PYTHON,
    WEBUI_PORT,
    INFOTEST,
    MONITOR,
    CHANOP,
    GENERICS,
    IRCD_HOST,
    IRCD_PORT,
    SERVICES_XMLRPC_URL,
    buildConfig,
)
import clients
from runner import InfobobRunner


@pytest.fixture(scope='session', autouse=True)
def fixture_start_logging():
    """
    Start up twisted.logger machinery.
    """
    observers = [logger.textFileLogObserver(sys.stderr)]
    logger.globalLogBeginner.beginLoggingTo(
        observers, redirectStandardIO=False)


@pytest.fixture(scope='session', autouse=True)
def fixture_registrations():
    from twisted.internet import reactor
    proxy = xmlrpc.Proxy(
        SERVICES_XMLRPC_URL.encode('ascii'),
        connectTimeout=5.0,
        reactor=reactor,
    )
    users = [INFOTEST, MONITOR, CHANOP, *GENERICS]
    dfd = defer.succeed(None)
    for creds in users:
        dfd.addCallback(
            lambda _: checkRegistered(proxy, creds.nickname, creds.password)
        )
    return pytest_tw.blockon(dfd)


def checkRegistered(proxy, nickname, password):
    # https://github.com/atheme/atheme/blob/v7.2.9/doc/XMLRPC
    def ebExplain(failure):
        failure.trap(xmlrpc.Fault)
        fault = failure.value
        raise AthemeLoginFailed(
            f'While logging in as {nickname}, got {fault}'
        ) from fault

    loginDfd = proxy.callRemote('atheme.login', nickname, password)
    return loginDfd.addCallback(lambda _: None).addErrback(ebExplain)


class AthemeLoginFailed(Exception):
    pass


@pytest.fixture(name='start_infobob')
def fixture_start_infobob(tmp_path):
    infobob_twistd = str(INFOBOB_PYTHON.parent.joinpath('twistd'))
    called = False
    spawned = None
    fixlog = logger.Logger(namespace=f'{__name__}.fixture_start_infobob')

    def start_infobob(channelsconf, autojoin):
        nonlocal called
        nonlocal spawned
        if called:
            raise RuntimeError('already called')
        called = True
        conf = buildConfig(channelsconf, autojoin)
        bot = InfobobRunner(
            python=INFOBOB_PYTHON,
            server=IRCD_HOST,
            server_port=IRCD_PORT,
            working_dir=tmp_path,
            conf=conf,
        )
        from twisted.internet import reactor
        spawned = bot.spawn(reactor)

    yield start_infobob

    if spawned is not None:
        def cbStop(botproto):
            if botproto.transport.pid is not None:
                botproto.transport.signalProcess('INT')
            return botproto.ended

        def ebLogAndRaise(f):
            fixlog.failure('Ugh, i dunno', f)
            return f

        return pytest_tw.blockon(
            spawned.addCallback(cbStop).addErrback(ebLogAndRaise)
        )


@pytest_tw.inlineCallbacks
def test_infobob_basic(start_infobob):
    from twisted.internet import reactor

    # This part could be used to test ComposedIRCController and friends...
    endpoint = endpoints.TCP4ClientEndpoint(
        reactor, IRCD_HOST, IRCD_PORT, timeout=5)
    monitor = yield clients.joinFakeUser(
        endpoint, MONITOR.nickname, MONITOR.password,
        autojoin=['#project', '##offtopic'])
    assert '#project' in monitor._proto.state.channels._channels
    assert '##offtopic' in monitor._proto.state.channels._channels

    start_infobob(channelsconf={
        '#project': {'have_ops': True},
        '##offtopic': {'have_ops': True},
    }, autojoin=['#project', '##offtopic'])
    yield task.deferLater(reactor, 10, lambda: None)

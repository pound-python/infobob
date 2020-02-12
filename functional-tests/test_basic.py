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
from twisted.internet.error import ProcessDone
from twisted.web import xmlrpc
from twisted import logger

import clients
from config import (
    SCHEMA_PATH,
    INFOBOB_PYTHON,
    WEBUI_PORT,
    INFOTEST,
    MONITOR,
    IRCD_HOST,
    IRCD_PORT,
    SERVICES_XMLRPC_URL,
)


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
    users = [INFOTEST, MONITOR]
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
    ended = None

    def start_infobob(channelsconf, autojoin):
        nonlocal called
        nonlocal spawned
        nonlocal ended
        if called:
            raise RuntimeError('already called')
        called = True
        confpath = tmp_path.joinpath('infobob.conf.json')
        dbpath = tmp_path.joinpath('infobob.db')
        conf = {
            'irc': {
                'server': IRCD_HOST,
                'port': IRCD_PORT,
                'ssl': False,
                'nickname': INFOTEST.nickname,
                'password': INFOTEST.password,
                'nickserv_pw': None,
                'autojoin': autojoin,
            },
            'channels': {
                'defaults': {
                    'commands': [
                        ['allow', 'all'],
                    ],
                },
                **channelsconf,
            },
            'database': {'sqlite': {'db_file': dbpath.name}},
            'web': {
                'port': WEBUI_PORT,
                'url': f'http://localhost:{WEBUI_PORT}',
            },
            'misc': {'manhole': {'socket': None}},
        }
        conn = sqlite3.connect(str(dbpath))
        with conn:
            conn.executescript(SCHEMA_PATH.read_text())
        conn.close()
        confpath.write_text(json.dumps(conf))
        from twisted.internet import reactor
        proto = InfobobProcessProtocol()
        args = [infobob_twistd, '-n', 'infobob', confpath.name]
        childFDs = {
            0: open(os.devnull, 'rb').fileno(),
            #1: open(os.devnull, 'wb').fileno(),
            1: 'r',
            #2: open(os.devnull, 'wb').fileno(),
            2: 'r',
        }
        ended = proto.ended
        spawned = defer.execute(
            reactor.spawnProcess,
            proto,
            infobob_twistd,
            args=args,
            env=None,
            path=str(tmp_path),
            childFDs=childFDs,
        )

    yield start_infobob

    if spawned is not None:
        def cbStop(procTransport):
            if procTransport.pid is not None:
                procTransport.signalProcess('INT')
            return ended
        return pytest_tw.blockon(spawned.addCallback(cbStop))


class InfobobProcessProtocol(protocol.ProcessProtocol):
    log = logger.Logger()

    def __init__(self):
        self.ended = defer.Deferred()

    def connectionMade(self):
        self.log.info('Infobob started')
        self.transport.closeStdin()

    def outReceived(self, data: bytes):
        self.log.info("stdout: " + data.decode('utf-8').rstrip('\r\n'))

    def errReceived(self, data: bytes):
        self.log.info("stderr: " + data.decode('utf-8').rstrip('\r\n'))

    def processEnded(self, status):
        if status.check(ProcessDone) is None:
            self.log.warn('Infobob exited: {status}', status=status)
            self.ended.errback(status)
        else:
            self.log.info('Infobob exited cleanly')
            self.ended.callback(None)


@pytest_tw.inlineCallbacks
def test_infobob_basic(start_infobob):
    from twisted.internet import reactor

    # This part could be used to test ComposedIRCController and friends...
    endpoint = endpoints.TCP4ClientEndpoint(
        reactor, IRCD_HOST, IRCD_PORT, timeout=5)
    monitor = yield clients.ComposedIRCController.connect(
        endpoint, MONITOR.nickname, MONITOR.password)
    yield defer.gatherResults([
        monitor.joinChannel('#project'),
        monitor.joinChannel('##offtopic'),
    ])
    assert '#project' in monitor._proto.state.channels._channels
    assert '##offtopic' in monitor._proto.state.channels._channels

    start_infobob(channelsconf={
        '#project': {'have_ops': True},
        '##offtopic': {'have_ops': True},
    }, autojoin=['#project', '##offtopic'])
    yield task.deferLater(reactor, 300, lambda: None)

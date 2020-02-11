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
from twisted import logger

import clients
from config import (
    INFOBOB_PYTHON,
    INFOTEST,
    MONITOR,
    SCHEMA_PATH,
    IRCD_HOST,
    IRCD_PORT,
    WEBUI_PORT,
)


@pytest.fixture(scope='session', autouse=True)
def fixture_start_logging():
    """
    Start up twisted.logger machinery.
    """
    observers = [logger.textFileLogObserver(sys.stderr)]
    logger.globalLogBeginner.beginLoggingTo(
        observers, redirectStandardIO=False)


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

    monitor = clients.ComposedIRCClientFactory(
        MONITOR.nickname, MONITOR.password)
    endpoint = endpoints.TCP4ClientEndpoint(
        reactor, IRCD_HOST, IRCD_PORT, timeout=5)
    yield endpoint.connect(monitor).addCallback(lambda p: p.signOnComplete)
    yield defer.gatherResults([
        monitor.joinChannel('#project'),
        monitor.joinChannel('##offtopic'),
    ])

    start_infobob(channelsconf={
        '#project': {'have_ops': True},
        '##offtopic': {'have_ops': True},
    }, autojoin=['#project', '##offtopic'])
    yield task.deferLater(reactor, 300, lambda: None)

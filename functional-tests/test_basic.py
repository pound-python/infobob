import os
import json
import pathlib
import sqlite3
import time

import pytest
import pytest_twisted as pytest_tw
from twisted.internet import protocol
from twisted.internet import defer
from twisted.internet import task
from twisted.internet import endpoints
from twisted.internet.error import ProcessDone

import clients


HERE = pathlib.Path(__name__).parent.resolve()
SCHEMA = HERE.parent.joinpath('db.schema')

SERVER = 'localhost'
SERVER_PORT = 6667
INFOBOB_NICK = 'infotest'
INFOBOB_PASS = 'infotestpass'
INFOBOB_WEB_PORT = 8888


@pytest.fixture(name='start_infobob')
def fixture_start_infobob(tmp_path):
    infobob_python = pathlib.Path(os.environ['INFOBOB_PYTHON']).resolve()
    infobob_twistd = str(infobob_python.parent.joinpath('twistd'))
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
                'server': SERVER,
                'port': SERVER_PORT,
                'ssl': False,
                'nickname': INFOBOB_NICK,
                'password': INFOBOB_PASS,
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
            'web': {'port': INFOBOB_WEB_PORT},
            'misc': {'manhole': {'socket': None}},
        }
        conn = sqlite3.connect(str(dbpath))
        with conn:
            conn.executescript(SCHEMA.read_text())
        conn.close()
        confpath.write_text(json.dumps(conf))
        from twisted.internet import reactor
        proto = InfobobProcessProtocol()
        args = [infobob_twistd, '-n', 'infobob', confpath.name]
        print('running', args)
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
    def __init__(self):
        self.ended = defer.Deferred()

    def connectionMade(self):
        print('Infobob started')
        self.transport.closeStdin()

    def outReceived(self, data: bytes):
        print('Got chunk of stdout:', data.decode('utf-8').rstrip('\r\n'))

    def errReceived(self, data: bytes):
        print('Got chunk of stderr:', data.decode('utf-8').rstrip('\r\n'))

    def processEnded(self, status):
        print('Infobob exited', status.value)
        if status.check(ProcessDone) is None:
            self.ended.errback(status)
        else:
            self.ended.callback(None)


@pytest_tw.inlineCallbacks
def test_infobob_basic(start_infobob):
    from twisted.internet import reactor

    monitor = clients.ComposedIRCClientFactory('monitor')
    endpoint = endpoints.TCP4ClientEndpoint(
        reactor, SERVER, SERVER_PORT, timeout=5)
    yield endpoint.connect(monitor).addCallback(lambda p: p.signOnComplete)
    yield defer.gatherResults([
        monitor.joinChannel('#project'),
        monitor.joinChannel('##offtopic'),
    ])

    start_infobob(channelsconf={}, autojoin=['#project', '##offtopic'])
    yield task.deferLater(reactor, 5, lambda: None)

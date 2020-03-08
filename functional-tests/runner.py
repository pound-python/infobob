import contextlib
import json
import os
import pathlib
import sqlite3
import urllib.parse
from typing import Sequence, Tuple, Optional

import attr
import hyperlink
import treq
from twisted.internet import defer
from twisted.internet import protocol
from twisted.internet.error import ProcessDone
from twisted.web.http_headers import Headers
from twisted import logger

from config import SCHEMA_PATH



JAN_15_1970 = '1970-01-15T12:00:00.000000Z'


def _getReactor():
    from twisted.internet import reactor
    return reactor


@attr.s
class InfobobRunner:
    python: pathlib.Path = attr.ib()
    server: str = attr.ib()
    server_port: int = attr.ib()
    working_dir: pathlib.Path = attr.ib()
    conf: dict = attr.ib()
    _reactor = attr.ib(factory=_getReactor)
    _procproto: Optional[defer.Deferred] = attr.ib(
        init=False, repr=False, default=None)
    _log = logger.Logger()

    def spawn(self) -> defer.Deferred:
        if self._procproto is not None:
            raise RuntimeError('process protocol not clear')
        self._ensure_db()
        confpath = self._write_conf()
        executable, args = self._build_args(confpath)
        childFDs = {
            0: open(os.devnull, 'rb').fileno(),
            1: 'r',
            2: 'r',
        }
        procproto = InfobobProcessProtocol()
        dfd = defer.execute(
            self._reactor.spawnProcess,
            procproto,
            executable,
            args=args,
            env=None,
            path=str(self.working_dir),
            childFDs=childFDs,
        )

        def cbUpdateStateReturnSelf(_):
            self._procproto = procproto
            return self

        return dfd.addCallback(cbUpdateStateReturnSelf)

    def respawn(self) -> defer.Deferred:
        if self._procproto is None:
            raise RuntimeError('No process protocol')
        dfd = self.stop()
        dfd.addCallback(lambda _: self.spawn())

    def stop(self) -> defer.Deferred:
        if self._procproto is None:
            return defer.succeed(None)
        if self._procproto.transport.pid is not None:
            self._procproto.transport.signalProcess('INT')
        dfd = self._procproto.ended

        def cbNullifyProcProto(passthrough):
            self._procproto = None
            return passthrough

        dfd.addBoth(cbNullifyProcProto)

        def ebLogAndRaise(f):
            self._log.failure('Error in process', f)
            return f

        dfd.addErrback(ebLogAndRaise)
        return dfd

    def webui(self) -> 'InfobobWebUIClient':
        port = self.conf['web']['port']
        uiclient = InfobobWebUIClient.new('localhost', port)
        return uiclient

    def database(self) -> 'InfobobDBClient':
        db = self.conf.get('database', {}).get('sqlite', {}).get('db_file')
        if db is None:
            dbpath = self.working_dir.joinpath('infobob.db')
            self.conf\
                .setdefault('database', {})\
                .setdefault('sqlite', {})['db_file'] = str(dbpath)
        else:
            dbpath = pathlib.Path(db)
        return InfobobDBClient(dbpath)

    def _ensure_db(self):
        self.database().init()

    def _write_conf(self) -> pathlib.Path:
        confpath = self.working_dir.joinpath('infobob.conf.json')
        confpath.write_text(json.dumps(self.conf))
        return confpath

    def _build_args(
            self, confpath: pathlib.Path) -> Tuple[str, Sequence[str]]:
        twistd = str(self.python.parent.joinpath('twistd'))
        args = [twistd, '-n', 'infobob', str(confpath)]
        return twistd, args


@attr.s
class InfobobDBClient:
    dbpath: os.PathLike = attr.ib()

    def init(self) -> None:
        if not self.dbpath.exists():
            conn = self._connect()
            with contextlib.closing(conn):
                with conn:
                    conn.executescript(SCHEMA_PATH.read_text())

    def _connect(self):
        conn = sqlite3.connect(str(self.dbpath))
        return conn

    def getBanAuths(self):
        conn = self._connect()
        with contextlib.closing(conn):
            rows = conn.execute('SELECT ban, code FROM ban_authorizations')
            return [(str(banid), banauth) for (banid, banauth) in rows]

    def dumpBanRows(self):
        conn = self._connect()
        with contextlib.closing(conn):
            for row in conn.execute('SELECT * FROM bans'):
                print(row)


@attr.s
class InfobobWebUIClient:
    root: hyperlink.URL = attr.ib()
    _client = attr.ib()

    @classmethod
    def new(cls, host: str, port: int):
        root = hyperlink.URL(scheme='http', host=host, port=port)
        return cls(root=root, client=treq)

    def _get(self, url):
        headers = Headers()
        headers.addRawHeader('Accept', 'application/json')
        return self._client.get(str(url), headers=headers)

    def _post(self, url, data):
        headers = Headers()
        headers.addRawHeader('Accept', 'application/json')
        headers.addRawHeader(
            'Content-Type', 'application/x-www-form-urlencoded')
        payload = urllib.parse.urlencode(data).encode('ascii')
        return self._client.post(str(url), headers=headers, data=payload)

    async def getCurrentBans(self, channelName: str):
        chanBans = await self._bansFromChannel(('bans',), channelName)
        return chanBans

    async def getExpiredBans(self, channelName: str):
        chanBans = await self._bansFromChannel(('bans', 'expired'), channelName)
        return chanBans

    async def getAllBans(self, channelName: str):
        chanBans = await self._bansFromChannel(('bans', 'all'), channelName)
        return chanBans

    async def _bansFromChannel(self, endpoint: Sequence[str], channelName: str):
        url = self.root.child(*endpoint)
        resp = await self._get(url)
        assert resp.code == 200
        byChannel = await resp.json()
        return byChannel.get(channelName, [])

    async def setBanExpired(self, banId: str, authToken: str):
        url = self.root.child('bans', 'edit', banId, authToken)
        resp = await self._post(url, data=dict(expire_at=JAN_15_1970))
        assert resp.code == 200
        body = await resp.json()
        return body


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

    def processEnded(self, reason):
        if reason.check(ProcessDone) is None:
            self.log.warn('Infobob exited: {reason}', reason=reason)
            self.ended.errback(reason)
        else:
            self.log.info('Infobob exited cleanly')
            self.ended.callback(None)

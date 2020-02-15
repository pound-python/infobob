import os
import pathlib
import sqlite3
import json
from typing import Sequence, Tuple

from twisted.internet import defer
from twisted.internet import protocol
from twisted import logger
import attr

from config import SCHEMA_PATH


@attr.s
class InfobobRunner:
    python: pathlib.Path = attr.ib()
    server: str = attr.ib()
    server_port: int = attr.ib()
    working_dir: pathlib.Path = attr.ib()
    conf: dict = attr.ib()

    def spawn(self, reactor) -> defer.Deferred:
        self._ensure_db()
        confpath = self._write_conf()
        executable, args = self._build_args(confpath)
        childFDs = {
            0: open(os.devnull, 'rb').fileno(),
            1: 'r',
            2: 'r',
        }
        proto = InfobobProcessProtocol()
        spawned = defer.execute(
            reactor.spawnProcess,
            proto,
            executable,
            args=args,
            env=None,
            path=str(self.working_dir),
            childFDs=childFDs,
        )
        return spawned.addCallback(lambda _: proto)

    def _ensure_db(self):
        db = self.conf.get('database', {}).get('sqlite', {}).get('db_file')
        if db is None:
            dbpath = self.working_dir.joinpath('infobob.db')
            self.conf\
                .setdefault('database', {})\
                .setdefault('sqlite', {})['db_file'] = str(dbpath)
        else:
            dbpath = pathlib.Path(db)
        if not dbpath.exists():
            conn = sqlite3.connect(str(dbpath))
            with conn:
                conn.executescript(SCHEMA_PATH.read_text())
            conn.close()

    def _write_conf(self) -> pathlib.Path:
        confpath = self.working_dir.joinpath('infobob.conf.json')
        confpath.write_text(json.dumps(self.conf))
        return confpath

    def _build_args(
            self, confpath: pathlib.Path) -> Tuple[str, Sequence[str]]:
        twistd = str(self.python.parent.joinpath('twistd'))
        args = [twistd, '-n', 'infobob', str(confpath)]
        return twistd, args


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

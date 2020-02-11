import os
import pathlib
from typing import TypeVar, Callable

import attr


HERE = pathlib.Path(__name__).parent.resolve()

SCHEMA_PATH = HERE.parent.joinpath('db.schema')


@attr.s
class IRCCredentials:
    nickname: str = attr.ib()
    password: str = attr.ib()


# Static credentials, representing nickserv-registered accounts.
# Don't change these unless you're prepared to do a lot of work to
# make the dockerized ircd and services match.
INFOTEST = IRCCredentials('infotest', 'infotestpass')
MONITOR = IRCCredentials('monitor', 'monitorpass')


def _passthrough(o: str) -> str:
    return o

_T = TypeVar('_T')


def _maybeEnv(
    suffix: str,
    default: _T,
    normalize: Callable[[str], _T] = _passthrough,
) -> _T:
    key = f'INFOBOB_FUNCTEST_{suffix}'
    fromEnv = os.environ.get(key)
    if fromEnv is None:
        return default
    return normalize(fromEnv)


# Required env var
INFOBOB_PYTHON = pathlib.Path(os.environ['INFOBOB_PYTHON']).resolve()

# Configurable defaults, change by setting as enviroment variables prefixed
# with `INFOBOB_FUNCTEST_<varname>`.
IRCD_HOST = _maybeEnv('IRCD_HOST', 'localhost')
IRCD_PORT = _maybeEnv('IRCD_PORT', 6667, int)
WEBUI_PORT = _maybeEnv('WEBUI_PORT', 8888, int)

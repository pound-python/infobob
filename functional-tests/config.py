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
CHANOP = IRCCredentials('chanop', 'chanoppass')
GENERICS = tuple(IRCCredentials(*cred) for cred in [
    ('agonzales', 'agonzalespass'),
    ('amcdowell', 'amcdowellpass'),
    ('bbutler', 'bbutlerpass'),
    ('cody67', 'cody67pass'),
    ('daniel18', 'daniel18pass'),
    ('imcdaniel', 'imcdanielpass'),
    ('james74', 'james74pass'),
    ('kevin69', 'kevin69pass'),
    ('marissa05', 'marissa05pass'),
    ('mary19', 'mary19pass'),
    ('michael81', 'michael81pass'),
    ('paul51', 'paul51pass'),
    ('paula67', 'paula67pass'),
    ('pward', 'pwardpass'),
    ('rmiller', 'rmillerpass'),
    ('tateroger', 'taterogerpass'),
    ('tinasmith', 'tinasmithpass'),
    ('tking', 'tkingpass'),
    ('wendybell', 'wendybellpass'),
    ('zchase', 'zchasepass')
])

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
WEBUI_PORT = _maybeEnv('WEBUI_PORT', 8888, int)

IRCD_HOST = _maybeEnv('IRCD_HOST', 'localhost')
IRCD_PORT = _maybeEnv('IRCD_PORT', 6667, int)
SERVICES_XMLRPC_URL = _maybeEnv(
    'SERVICES_XMLRPC_URL', 'http://localhost:8080/xmlrpc'
)


def buildConfig(channelsconf, autojoin, dbpath=None):
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
        'web': {
            'port': WEBUI_PORT,
            'url': f'http://localhost:{WEBUI_PORT}',
        },
        'misc': {'manhole': {'socket': None}},
    }
    if dbpath:
        conf['database'] = {'sqlite': {'db_file': str(dbpath)}}
    return conf

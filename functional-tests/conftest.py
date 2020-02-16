import sys

import pytest
import pytest_twisted as pytest_tw
from twisted import logger

from config import (
    INFOBOB_PYTHON,
    buildConfig,
    IRCD_HOST,
    IRCD_PORT,
)
import clients
from runner import InfobobRunner


@pytest.fixture(scope='session', autouse=True)
def fixture_start_logging():
    """
    Start up twisted.logger machinery.
    """
    stderrObserver = logger.textFileLogObserver(sys.stderr)
    levelPredicate = logger.LogLevelFilterPredicate(
        defaultLogLevel=logger.LogLevel.info)
    filterer = logger.FilteringLogObserver(stderrObserver, [levelPredicate])
    observers = [filterer]
    logger.globalLogBeginner.beginLoggingTo(
        observers, redirectStandardIO=False)


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

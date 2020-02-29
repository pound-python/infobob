import sys

import pytest
import pytest_twisted as pytest_tw
from twisted.internet import endpoints
from twisted.internet import defer
from twisted import logger

from config import (
    INFOBOB_PYTHON,
    buildConfig,
    IRCD_HOST,
    IRCD_PORT,
    MONITOR,
    ALL_CHANS,
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

    def start_infobob(channelsconf, autojoin) -> defer.Deferred:
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

        running = defer.Deferred()
        callLater = reactor.callLater  # pylint: disable=no-member

        def cbNotifyTest(value):
            callLater(0, running.callback, None)
            return value

        def ebNotifyTest(failure):
            callLater(0, running.errback, failure)
            return failure

        spawned = bot.spawn(reactor).addCallbacks(cbNotifyTest, ebNotifyTest)
        return running

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


@pytest.fixture(name='ircd_endpoint')
def fixture_ircd_endpoint():
    from twisted.internet import reactor

    ircd_endpoint = endpoints.TCP4ClientEndpoint(
        reactor, IRCD_HOST, IRCD_PORT, timeout=5)
    return ircd_endpoint


@pytest_tw.async_yield_fixture(name='monitor')
async def fixture_monitor(ircd_endpoint):
    monitor = await clients.joinFakeUser(
        ircd_endpoint,
        MONITOR.nickname,
        MONITOR.password,
        autojoin=ALL_CHANS)
    yield monitor
    await monitor.disconnect()

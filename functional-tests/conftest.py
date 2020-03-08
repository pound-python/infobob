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
    CHANOP,
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
    called = False
    controller = None

    def start_infobob(channelsconf=None, autojoin=None) -> defer.Deferred:
        nonlocal called
        nonlocal controller

        if channelsconf is None:
            channelsconf = {cname: {'have_ops': True} for cname in ALL_CHANS}
        if autojoin is None:
            autojoin = ALL_CHANS
        if called:
            raise RuntimeError('already called')
        called = True
        conf = buildConfig(channelsconf, autojoin)
        controller = InfobobRunner(
            python=INFOBOB_PYTHON,
            server=IRCD_HOST,
            server_port=IRCD_PORT,
            working_dir=tmp_path,
            conf=conf,
        )

        return controller.spawn()

    yield start_infobob

    if controller is not None:
        return pytest_tw.blockon(controller.stop())


@pytest.fixture(name='ircd_endpoint')
def fixture_ircd_endpoint():
    from twisted.internet import reactor

    ircd_endpoint = endpoints.TCP4ClientEndpoint(
        reactor, IRCD_HOST, IRCD_PORT, timeout=5)
    return ircd_endpoint


# XXX: This is broken in pytest_twisted, see
# https://github.com/pytest-dev/pytest-twisted/pull/90
# @pytest_tw.async_yield_fixture(name='joinfake')
# async def fixture_joinfake(ircd_endpoint):
#     controllers = []
#     async def joinfake(creds, autojoin=ALL_CHANS):
#         controller = await clients.joinFakeUser(
#             ircd_endpoint,
#             creds.nickname,
#             creds.password,
#             autojoin=autojoin,
#         )
#         controllers.append(controller)
#         return controller
#
#     yield joinfake
#     await defer.gatherResults([
#         controller.disconnect() for controller in controllers
#     ])
@pytest.fixture(name='joinfake')
def fixture_joinfake(ircd_endpoint):
    controllers = []

    @defer.inlineCallbacks
    def joinfake(creds, autojoin=ALL_CHANS):
        controller = yield defer.ensureDeferred(clients.joinFakeUser(
            ircd_endpoint,
            creds.nickname,
            creds.password,
            autojoin=autojoin,
        ))
        controllers.append(controller)
        return controller

    yield joinfake
    if controllers:
        pytest_tw.blockon(defer.gatherResults([
            controller.disconnect() for controller in controllers
        ]))


@pytest_tw.async_fixture(name='monitor')
async def fixture_monitor(joinfake):
    monitor = await joinfake(MONITOR, autojoin=ALL_CHANS)
    return monitor


@pytest_tw.async_fixture(name='chanop')
async def fixture_chanop(joinfake):
    chanop = await joinfake(CHANOP, autojoin=ALL_CHANS)
    for channelName in ALL_CHANS:
        await chanop.channel(channelName).becomeOperator()
    return chanop

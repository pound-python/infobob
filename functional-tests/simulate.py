import os
import json
import pathlib
import tempfile
import time
import sys
import functools
import random
from typing import Sequence, Tuple

from twisted.internet import defer
from twisted.internet import task
from twisted.internet import endpoints
from twisted.internet.error import ProcessDone
from twisted.web import xmlrpc
from twisted import logger
import attr

from config import (
    SCHEMA_PATH,
    INFOBOB_PYTHON,
    WEBUI_PORT,
    INFOTEST,
    MONITOR,
    GENERICS,
    IRCD_HOST,
    IRCD_PORT,
    SERVICES_XMLRPC_URL,
    buildConfig,
)
import clients
import runner


LOG = logger.Logger()


def main(reactor, infobob_working_dir: pathlib.Path):
    observers = [logger.textFileLogObserver(sys.stderr)]
    logger.globalLogBeginner.beginLoggingTo(
        observers, redirectStandardIO=False)
    with open('phrases.txt') as fp:
        phrases = [line.strip() for line in fp]
    conf = buildConfig(
        channelsconf={
            '#project': {'have_ops': True},
            '##offtopic': {'have_ops': True},
        },
        autojoin=['#project', '##offtopic'],
    )
    bot = runner.InfobobRunner(
        python=INFOBOB_PYTHON,
        server=IRCD_HOST,
        server_port=IRCD_PORT,
        working_dir=infobob_working_dir,
        conf=conf,
    )
    endpoint = endpoints.TCP4ClientEndpoint(
        reactor, IRCD_HOST, IRCD_PORT, timeout=5)
    creds = [MONITOR, *GENERICS[:10]]
    taskRunners = [
        functools.partial(
            runChatter, endpoint, reactor,
            cred.nickname, cred.password,
            '##offtopic', phrases,
        )
        for cred in creds
    ]
    return run(reactor, bot=bot, taskRunners=taskRunners)


@defer.inlineCallbacks
def run(reactor, *, bot, taskRunners):
    LOG.info('Starting infobob')
    botproto = yield bot.spawn(reactor)
    try:
        yield task.deferLater(reactor, 5)
        if botproto.transport.pid is None:
            LOG.error('infobob quit')
            return
        yield defer.gatherResults([run() for run in taskRunners])
    except Exception:
        LOG.failure('Unhandled exception in simulate.run')
    finally:
        if botproto.transport.pid is not None:
            botproto.transport.signalProcess('INT')
        yield botproto.ended


def runChatter(endpoint, reactor, nickname, password, channel, phrases):
    dfd = joinFakeUser(endpoint, nickname, password, [channel])
    dfd.addCallback(chat, reactor, channel, phrases)
    return dfd


@defer.inlineCallbacks
def joinFakeUser(
    endpoint,
    nickname: str,
    password: str,
    autojoin: Sequence[str] = (),
) -> clients.ComposedIRCController:
    controller = yield clients.ComposedIRCController.connect(
        endpoint, nickname, password)
    if autojoin:
        yield defer.gatherResults([
            controller.joinChannel(chan) for chan in autojoin
        ])
    return controller


@defer.inlineCallbacks
def chat(controller: clients.ComposedIRCController, reactor, channel, phrases):
    while True:
        initdelay = random.randint(5, 20)
        yield task.deferLater(reactor, initdelay)
        for _ in range(random.randint(30, 180)):
            msgdelay = random.randint(4, 50)
            yield task.deferLater(reactor, msgdelay)
            message = random.choice(phrases)
            controller.say(channel, message)
        burstdelay = random.randint(30, 180)
        yield task.deferLater(reactor, burstdelay)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as tdir:
        infobob_working_dir = pathlib.Path(tdir)
        task.react(main, (infobob_working_dir,))

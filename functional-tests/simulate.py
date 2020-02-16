#!/usr/bin/env python3
import os
import json
import pathlib
import tempfile
import time
import sys
import random
import argparse
from functools import partial
from typing import Sequence

from twisted.internet import defer
from twisted.internet import task
from twisted.internet import endpoints
from twisted.internet.error import ProcessDone
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


def main(reactor, infobob_working_dir: pathlib.Path, phrases: Sequence[str]):
    observers = [logger.textFileLogObserver(sys.stderr)]
    logger.globalLogBeginner.beginLoggingTo(
        observers, redirectStandardIO=False)
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
        partial(
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
    dfd = clients.joinFakeUser(endpoint, nickname, password, [channel])
    dfd.addCallback(chat, reactor, channel, phrases)
    return dfd


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
    args = sys.argv[1:]
    if args:
        with open(args[0]) as fp:
            phrases = [line.strip() for line in fp]
    else:
        phrases = [
            "You're using coconuts!",
            "Where did you get the coconuts?",
            "Found them?  In Mercea.  The coconut's tropical!",
            "This new learning amazes me, Sir Bedevere.",
            "Explain again how sheep's bladders may be employed "
                "to prevent earthquakes.",
            "Oh, let me go and have a bit of peril?",
        ]
    with tempfile.TemporaryDirectory() as tdir:
        infobob_working_dir = pathlib.Path(tdir)
        task.react(main, (infobob_working_dir, phrases))

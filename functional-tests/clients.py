from __future__ import annotations
import enum
from typing import Mapping

from twisted.words.protocols import irc
from twisted.internet.protocol import Factory
from twisted.internet import defer
from twisted.logger import Logger
import zope.interface as zi
import attr


class ActionsWrangler:
    def __init__(self, name: str):
        self.name = name
        self._inflight = {}

    # Really key can be any hashable thing, not just a str
    def begin(self, key: str) -> defer.Deferred:
        if key in self._inflight:
            raise ValueError(f'{self.name} for {key!r} already in flight')
        dfd = self._inflight[key] = defer.Deferred()
        return dfd

    def complete(self, key: str) -> None:
        dfd = self._inflight.pop(key, None)
        if dfd is None:
            raise ValueError(f'No {self.name} in flight for {key!r}')
        dfd.callback(key)

    def __repr__(self):
        return (
            f'<{type(self).__name__}(name={self.name}),'
            f' {len(self._inflight)} outstanding>'
        )


@attr.s
class Actions:
    myJoins = attr.ib(factory=lambda: ActionsWrangler('myJoins'))
    userJoins = attr.ib(factory=lambda: ActionsWrangler('userJoins'))


numeric_addendum = dict(
    RPL_WHOISACCOUNT='330',
    RPL_QUIETLIST='728',
    RPL_ENDOFQUIETLIST='729',
)
for name, numeric in numeric_addendum.items():
    irc.numeric_to_symbolic[numeric] = name
    irc.symbolic_to_numeric[name] = numeric


class ComposedIRCClient(irc.IRCClient):
    """
    Goal: provide separations of concerns by dispatching events to
    other objects, instead of stuffing even more in the already-bloated
    IRCClient.
    """
    log = Logger()

    def __init__(self, nickname: str):
        self.nickname = nickname
        self.signOnComplete = defer.Deferred()

    def signedOn(self):
        self.signOnComplete.callback(None)

    def joined(self, channel: str) -> None:
        self.factory.actions.myJoins.complete(channel)


class ComposedIRCClientFactory(Factory):
    def __init__(self, nickname: str):
        self.nickname = nickname
        self.actions = None

    def startFactory(self):
        self.actions = Actions()

    def buildProtocol(self, addr):
        proto = ComposedIRCClient(self.nickname)
        proto.factory = self
        self.p = proto
        return proto

    def joinChannel(self, channel: str):
        self.p.join(channel)
        return self.actions.myJoins.begin(channel)

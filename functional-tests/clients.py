from __future__ import annotations
import enum
from typing import MutableMapping

from twisted.words.protocols import irc
from twisted.internet.protocol import Factory
from twisted.internet import endpoints
from twisted.internet import defer
from twisted import logger
import zope.interface as zi
import attr


def _add_numerics():
    numeric_addendum = dict(
        RPL_WHOISACCOUNT='330',
        RPL_QUIETLIST='728',
        RPL_ENDOFQUIETLIST='729',
    )
    for name, numeric in numeric_addendum.items():
        irc.numeric_to_symbolic[numeric] = name
        irc.symbolic_to_numeric[name] = numeric

_add_numerics()


@attr.s
class Channel:
    name: str = attr.ib()
    # nick -> User
    _members: MutableMapping[str, User] = attr.ib(factory=dict)
    # TODO: Maybe add modes? Bans?

    _log = logger.Logger()

    def userJoined(self, user: User):
        if user.nickname in self._members:
            self._log.warn(f'User {user!r} already in channel {self.name}')
        self.members[user.nickname] = user

    def userLeft(self, user: Union[User, str]):
        nick = user if isinstance(user, str) else user.nickname
        if nick not in self._members:
            self._log.warn(f'User {user!r} not in channel {self.name}')
            return
        del self._members[nick]


@attr.s
class ChannelCollection:
    _channels: MutableMapping[str, Channel] = attr.ib(factory=dict)

    _log = logger.Logger()

    def joined(self, channelName: str):
        if channelName in self._channels:
            self._log.warn('Channel {channelName!r} already registered')
            return
        c = Channel(channelName)
        self._channels[c.name] = c


@attr.s
class User:
    nickname: str = attr.ib()
    username: str = attr.ib()
    hostname: str = attr.ib()


class _ActionsWrangler:
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
class _Actions:
    myJoins = attr.ib(factory=lambda: _ActionsWrangler('myJoins'))
    # XXX: Semantics yet unclear.
    #userJoins = attr.ib(factory=lambda: _ActionsWrangler('userJoins'))


@attr.s
class ComposedIRCController:
    # TODO: Change methods to coroutines? Deferred isn't generic (is that
    #       even possible to have?), Awaitable[ComposedIRCController]
    #       is a nicer type hint, and overall async/await is nicer.
    _proto: _ComposedIRCClient = attr.ib()
    _actions: _Actions = attr.ib()

    @classmethod
    @defer.inlineCallbacks
    def connect(cls, endpoint, nickname: str, password: str) -> defer.Deferred:
        proto = yield endpoints.connectProtocol(
            endpoint, _ComposedIRCClient(nickname, password))
        yield proto.signOnComplete
        return cls(proto=proto, actions=proto.state.actions)

    def joinChannel(self, channelName: str) -> defer.Deferred:
        self._proto.join(channelName)
        return self._actions.myJoins.begin(channelName)


@attr.s
class _IRCClientState:
    actions: _Actions = attr.ib(factory=_Actions)
    channels: ChannelCollection = attr.ib(factory=ChannelCollection)


class _ComposedIRCClient(irc.IRCClient):
    """
    Goal: provide separations of concerns by dispatching events to
    other objects, instead of stuffing even more in the already-bloated
    IRCClient.
    """
    _log = logger.Logger()

    def __init__(self, nickname: str, password: str):
        self.nickname = nickname
        self.password = password
        self.state = _IRCClientState()
        self.signOnComplete = defer.Deferred()

    def signedOn(self):
        self.signOnComplete.callback(None)

    def joined(self, channel: str) -> None:
        self._log.info('Joined channel {channel!r}', channel=channel)
        self.state.channels.joined(channel)
        self.state.actions.myJoins.complete(channel)

    def irc_unknown(self, command, prefix, params):
        self._log.warn(
            "received command we aren't prepared to handle: {cmd} {pfx} {pms}",
            cmd=command, pfx=prefix, pms=params,
        )

from __future__ import annotations
import enum
from typing import MutableMapping, MutableSet, Callable, Sequence

from twisted.words.protocols import irc
from twisted.internet.protocol import Factory
from twisted.internet import endpoints
from twisted.internet import defer
from twisted import logger
import zope.interface as zi
import attr


def _add_numerics() -> None:
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
class ChannelCollection:
    # channel name -> users
    _channels: MutableMapping[str, MutableSet[str]] = attr.ib(factory=dict)

    _log = logger.Logger()

    def add(self, channelName: str) -> None:
        assert channelName not in self._channels, \
            f'channel {channelName} already exists'
        self._channels[channelName] = set()

    def remove(self, channelName: str) -> None:
        del self._channels[channelName]

    def userJoined(self, nickname: str, channelName: str) -> None:
        channel = self._channels[channelName]
        assert nickname not in channel, \
            f'{nickname} already in {channelName}'
        channel.add(nickname)

    def userLeft(self, nickname: str, channelName: str) -> None:
        self._channels[channelName].remove(nickname)

    def userQuit(self, nickname) -> None:
        found = False
        for members in self._channels.values():
            if nickname in members:
                found = True
                members.remove(nickname)
        assert found, f'{nickname} not found in any channels'


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

    def error(self, key: str, err: Exception) -> None:
        dfd = self._inflight.pop(key, None)
        if dfd is None:
            raise ValueError(f'No {self.name} in flight for {key!r}')
        dfd.errback(err)

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


class FailedToJoin(Exception):
    pass


def _joinErrorMethod(
    errorName: str
) -> Callable[[_ComposedIRCClient, str, Sequence[str]], None]:
    assert errorName.upper() == errorName and errorName.startswith('ERR_')
    methodName = f'irc_{errorName}'

    def method(self, prefix: str, params: Sequence[str]) -> None:
        channel, *rest = params
        self._log.warn(
            'Failed to join {channel!r}: {code} {params}',
            channel=channel, code=errorName, params=rest,
        )
        err = FailedToJoin(errorName, channel, rest)
        self.state.actions.myJoins.error(channel, err)

    method.__name__ == methodName
    # TODO: Uh, what about __qualname__?
    return method


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

    def signedOn(self) -> None:
        self.signOnComplete.callback(None)

    def joined(self, channel: str) -> None:
        self._log.info('Joined channel {channel}', channel=channel)
        self.state.channels.add(channel)
        self.state.actions.myJoins.complete(channel)

    def left(self, channel: str) -> None:
        self._log.info('I left {channel}', channel=channel)
        self.state.channels.remove(channel)

    def kickedFrom(self, channel: str, kicker: str, message: str) -> None:
        self._log.info(
            'I was kicked from {channel} by {kicker}: {message}',
            channel=channel, kicker=kicker, message=message,
        )
        self.state.channels.remove(channel)

    #def userJoined(self, user: str, channel: str) -> None:
    #def userLeft(self, user: str, channel: str) -> None:
    #def userQuit(self, user: str, message: str) -> None:
    #def userKicked(
    #    self, kickee: str, channel: str, kicker: str, message: str
    #) -> None:
    #def userRenamed(self, oldname: str, newname: str) -> None:

    ### Low-level protocol events
    def irc_unknown(self, command, prefix, params):
        self._log.warn(
            "received command we aren't prepared to handle: {cmd} {pfx} {pms}",
            cmd=command, pfx=prefix, pms=params,
        )

    ### JOIN replies:
    # The server itself replies with a JOIN, this is handled by twisted:
    # it calls either `joined` or `userJoined`, depending.
    # RPL_TOPIC is handled by twisted: it calls `topicUpdated`.

    # These error replies aren't sent for anything but JOIN:
    irc_ERR_BADCHANNELKEY = _joinErrorMethod('ERR_BADCHANNELKEY')
    irc_ERR_BANNEDFROMCHAN = _joinErrorMethod('ERR_BANNEDFROMCHAN')
    irc_ERR_CHANNELISFULL = _joinErrorMethod('ERR_CHANNELISFULL')
    irc_ERR_INVITEONLYCHAN = _joinErrorMethod('ERR_INVITEONLYCHAN')
    irc_ERR_TOOMANYCHANNELS = _joinErrorMethod('ERR_TOOMANYCHANNELS')

    # XXX: Maybe try to handle these more ambiguous ones?
    # There doesn't appear to be a nice way to correlate one to its cause,
    # without some IRCv3 stuff, but we really shouldn't receive any of them
    # unless we do something wrong. If we store recently-sent commands, we
    # could maybe guess a little easier, but it's almost certainly not worth
    # the effort.
    # ERR_BADCHANMASK - to: JOIN or KICK
    # ERR_NOSUCHCHANNEL - to: JOIN, PART, or KICK
    # ERR_TOOMANYTARGETS - to: JOIN or PRIVMSG
    # ERR_UNAVAILRESOURCE - to: JOIN or NICK
    # XXX: This one could just be fatal, maybe.
    # ERR_NEEDMOREPARAMS - to: numerous commands

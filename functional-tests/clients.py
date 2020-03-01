from __future__ import annotations
import datetime
import collections
import contextlib
from typing import (
    Awaitable,
    Callable,
    Deque,
    MutableMapping,
    MutableSet,
    Optional,
    Sequence,
    Union,
)

from twisted.words.protocols import irc
from twisted.internet import endpoints
from twisted.internet import defer
from twisted import logger
import attr

import utils


async def joinFakeUser(
    endpoint,
    nickname: str,
    password: str,
    autojoin: Sequence[str] = (),
) -> Awaitable[ComposedIRCController]:
    controller = await ComposedIRCController.connect(
        endpoint, nickname, password)
    if autojoin:
        # NickServ might not have had time to give us +i, give it a moment.
        await utils.sleep(0.5)
        await defer.gatherResults([
            controller.joinChannel(chan) for chan in autojoin
        ])
    return controller


@attr.s
class ComposedIRCController:
    # TODO: Change methods to coroutines? Deferred isn't generic (is that
    #       even possible to have?), Awaitable[ComposedIRCController]
    #       is a nicer type hint, and overall async/await is nicer.
    _proto: _ComposedIRCClient = attr.ib()
    _actions: _Actions = attr.ib()

    @classmethod
    @defer.inlineCallbacks
    def connect(
        cls,
        endpoint,
        nickname: str,
        password: str,
        signOnTimeout: int = 1,
    ) -> defer.Deferred:
        from twisted.internet import reactor
        proto = yield endpoints.connectProtocol(
            endpoint, _ComposedIRCClient(nickname, password)
        ).addTimeout(signOnTimeout, reactor)
        ctrl = cls(proto=proto, actions=proto.state.actions)
        yield proto.signOnComplete.addTimeout(signOnTimeout, reactor)
        return ctrl

    @property
    def nickname(self):
        return self._proto.nickname

    def disconnect(self) -> defer.Deferred:
        self._proto.transport.loseConnection()
        return self._proto.disconnected

    def channel(self, channelName: str) -> ChannelController:
        chanstate = self._proto.state.channels.get(channelName)
        return ChannelController(
            name=channelName, proto=self._proto, state=chanstate)

    def joinChannel(self, channelName: str, timeout: int= 1) -> defer.Deferred:
        from twisted.internet import reactor
        self._proto.join(channelName)
        dfd = self._actions.myJoins.begin(channelName)
        return dfd.addTimeout(timeout, reactor)

    def say(self, channelName: str, message: str):
        self._proto.say(channelName, message)

    def getPrivateMessages(
        self,
        sender: Optional[str] = None,
    ) -> Sequence[Message]:
        messages = self._proto.state.getPrivateMessages()
        if sender is None:
            return messages
        return [msg for msg in messages if sender == msg.sender]


@attr.s
class ChannelController:
    name: str = attr.ib()
    _proto: _ComposedIRCClient = attr.ib()
    _state: _ChannelState = attr.ib()

    def say(self, message: str):
        self._proto.say(self.name, message)

    def msg(self, nickname: str, message: str):
        self._proto.msg(nickname, message)

    def becomeOperator(self, timeout: int = 1) -> defer.Deferred:
        if self._isOpped:
            return defer.succeed(None)
        from twisted.internet import reactor
        self.msg('ChanServ', f'op {self.name}')
        dfd = self._state.opAttempts.begin(self.name)
        return dfd.addTimeout(timeout, reactor)

    def retrieveBans(self, timeout: int = 1) -> defer.Deferred:
        from twisted.internet import reactor
        self._proto.mode(self.name, True, 'b')
        dfd = self._state.banlistReceives.begin(self.name)
        def cbGetBans(_):
            return self._state.getCurrentBans()
        return dfd.addTimeout(timeout, reactor).addCallback(cbGetBans)

    def setBan(self, mask: str) -> None:
        if not self._isOpped:
            raise NotAnOperator
        self._proto.mode(self.name, True, 'b', mask=mask)

    def unsetBan(self, mask: str) -> None:
        if not self._isOpped:
            raise NotAnOperator
        self._proto.mode(self.name, False, 'b', mask=mask)

    @property
    def _isOpped(self) -> bool:
        return self._proto.nickname in self._state.operators

    def getOperators(self) -> Set[str]:
        return frozenset(self._state.operators)

    def getMembers(self) -> Set[str]:
        return self._state.getMembers()

    def getCurrentBans(self) -> Sequence[Ban]:
        return self._state.getCurrentBans()

    def getUnsetBans(self) -> Sequence[Ban]:
        return self._state.getUnsetBans()

    def getMessages(
        self,
        sender: Optional[str] = None,
        when: Optional[Union[int, datetime.datetime]] = None,
    ) -> Sequence[Message]:
        """
        Get messages still in the queue, optionally filtered by
        sender or age.

        Note: only a limited number of messages are stored.

        If ``sender`` is provided, only messages from that nickname
        will be returned.

        If ``when`` is provided, only younger messages will be
        returned. ``when`` can be either:
        -   a naive :class:`datetime.datetime` instance in UTC, the
            earliest time, or
        -   an integer, the maximum age in seconds relative to when
            this method is called.
        """
        if isinstance(when, int):
            when = _now() - datetime.timedelta(seconds=when)

        messages = self._state.getMessages()
        if sender is not None:
            messages = [msg for msg in messages if msg.sender == sender]
        if when is not None:
            messages = [msg for msg in messages if msg.when >= when]

        return messages


class NotAnOperator(Exception):
    pass


@attr.s
class _ChannelCollection:
    _channels: MutableMapping[str, _ChannelState] = attr.ib(factory=dict)

    _log = logger.Logger()

    def add(self, channelName: str) -> None:
        assert channelName not in self._channels, \
            f'channel {channelName} already exists'
        self._channels[channelName] = _ChannelState(name=channelName)

    def remove(self, channelName: str) -> None:
        del self._channels[channelName]

    def get(self, channelName: str) -> _ChannelState:
        return self._channels[channelName]

    def userRenamed(self, oldnick: str, newnick: str) -> None:
        for chan in self._channelsWithUser(oldnick):
            chan.removeNick(oldnick)
            chan.addNick(newnick)

    def userQuit(self, nickname: str) -> None:
        for chan in self._channelsWithUser(nickname):
            chan.removeNick(nickname)

    def _channelsWithUser(self, nickname: str) -> Sequence[_ChannelState]:
        return [chan for chan in self._channels.values() if nickname in chan]


_MAX_MESSAGES = 50


@attr.s
class _ChannelState:
    # TODO: Need to eventually have some concept of "events" to cover
    #       joins, parts, quits, kicks, bans (set and unset), and
    #       nick changes.
    name: str = attr.ib()
    opAttempts: _ActionsWrangler = attr.ib(init=False)
    banlistReceives: _ActionsWrangler = attr.ib(init=False)
    operators: Set[str] = attr.ib(init=False, factory=set)
    _messages: Deque[Message] = attr.ib(
        init=False,
        repr=False,
        factory=lambda: collections.deque([], _MAX_MESSAGES),
    )
    _members: MutableSet[str] = attr.ib(init=False, repr=False, factory=set)
    # mask -> Ban
    _bans: MutableMapping[str, Ban] = attr.ib(
        init=False, repr=False, factory=dict)
    _unsetbans: MutableMapping[str, Ban] = attr.ib(
        init=False, repr=False, factory=dict)

    def __attrs_post_init__(self):
        self.opAttempts = _ActionsWrangler(f'opAttempt {self.name}')
        self.banlistReceives = _ActionsWrangler(f'banlistReceive {self.name}')

    def __contains__(self, nickname: str) -> None:
        return nickname in self._members

    def getMembers(self) -> Set[str]:
        return frozenset(self._members)

    def addNick(self, nickname: str) -> None:
        self._members.add(nickname)

    def removeNick(self, nickname: str) -> None:
        with contextlib.suppress(KeyError):
            self._members.remove(nickname)

    def addBan(self, mask: str, setter: str) -> None:
        ban = Ban(mask=mask, setBy=setter)
        self._bans[ban.mask] = ban

    def removeBan(self, mask: str, unsetter: str) -> None:
        ban = self._bans.pop(mask, None)
        if ban is None:
            return
        ban = attr.evolve(ban, unsetBy=unsetter)
        self._unsetbans[ban.mask] = ban

    def getCurrentBans(self) -> Sequence[Ban]:
        return list(self._bans.values())

    def getUnsetBans(self) -> Sequence[Ban]:
        return list(self._unsetbans.values())

    def addMessage(self, nickname: str, message: str) -> None:
        self.addNick(nickname)
        msg = Message.now(sender=nickname, text=message)
        self._messages.append(msg)

    def getMessages(self) -> Sequence[Message]:
        return list(self._messages)


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


@attr.s
class Message:
    sender: str = attr.ib()
    text: str = attr.ib()
    when: datetime.datetime = attr.ib()

    @classmethod
    def now(cls, *, sender: str, text: str) -> Message:
        return cls(sender=sender, text=text, when=_now())


@attr.s
class Ban:
    mask: str = attr.ib()
    setBy: str = attr.ib()
    unsetBy: Optional[str] = attr.ib(default=None)


class _ActionsWrangler:
    def __init__(self, name: str):
        self.name = name
        self._inflight = {}

    # Really key can be any hashable thing, not just a str
    def begin(self, key: str) -> defer.Deferred:
        if key in self._inflight:
            raise _ActionAlreadyInFlight.build(self.name, key)
        dfd = self._inflight[key] = defer.Deferred()
        return dfd

    def complete(self, key: str) -> None:
        dfd = self._inflight.pop(key, None)
        if dfd is None:
            raise _NoActionInFlight.build(self.name, key)
        dfd.callback(key)

    def error(self, key: str, err: Exception) -> None:
        dfd = self._inflight.pop(key, None)
        if dfd is None:
            raise _NoActionInFlight.build(self.name, key)
        dfd.errback(err)

    def __repr__(self):
        return (
            f'<{type(self).__name__}(name={self.name}),'
            f' {len(self._inflight)} outstanding>'
        )


class _ActionAlreadyInFlight(Exception):
    @classmethod
    def build(cls, name, key):
        return cls(f'{name} for {key!r} already in flight')


class _NoActionInFlight(Exception):
    @classmethod
    def build(cls, name, key):
        return cls(f'No {name} in flight for {key!r}')


@attr.s
class _Actions:
    myJoins = attr.ib(factory=lambda: _ActionsWrangler('myJoins'))
    # XXX: Semantics yet unclear.
    #userJoins = attr.ib(factory=lambda: _ActionsWrangler('userJoins'))


@attr.s
class _IRCClientState:
    actions: _Actions = attr.ib(factory=_Actions)
    channels: _ChannelCollection = attr.ib(factory=_ChannelCollection)
    _privmsgs: Deque[Message] = attr.ib(
        init=False,
        repr=False,
        factory=lambda: collections.deque([], _MAX_MESSAGES),
    )

    def addPrivateMessage(self, nickname: str, message: str) -> None:
        msg = Message.now(sender=nickname, text=message)
        self._privmsgs.append(msg)

    def getPrivateMessages(self) -> Sequence[Message]:
        return list(self._privmsgs)


class FailedToJoin(Exception):
    pass


def _joinErrorMethod(
    errorName: str
) -> Callable[[_ComposedIRCClient, str, Sequence[str]], None]:
    assert errorName.upper() == errorName and errorName.startswith('ERR_')
    methodName = f'irc_{errorName}'

    def method(self, prefix: str, params: Sequence[str]) -> None:  # pylint: disable=unused-argument
        channel, *rest = params
        self._log.warn(  # pylint: disable=protected-access
            'Failed to join {channel!r}: {code} {params}',
            channel=channel, code=errorName, params=rest,
        )
        err = FailedToJoin(errorName, channel, rest)
        self.state.actions.myJoins.error(channel, err)

    method.__name__ = methodName
    # TODO: Uh, what about __qualname__?
    return method


def _prefixNicknameThenForward(event):
    # XXX: Ugly. I think.
    log_format = '(nick:{log_source.nickname}) ' + event['log_format']
    tweaked = {**event, 'log_format': log_format}
    logger.globalLogPublisher(tweaked)


class _ComposedIRCClient(irc.IRCClient):  # pylint: disable=abstract-method
    """
    Goal: provide separations of concerns by dispatching events to
    other objects, instead of stuffing even more in the already-bloated
    IRCClient.
    """
    _log = logger.Logger(observer=_prefixNicknameThenForward)

    def __init__(self, nickname: str, password: str):
        self.nickname = nickname
        self.password = password
        self.state = _IRCClientState()
        self.signOnComplete = defer.Deferred()
        self.disconnected = defer.Deferred()

    def connectionMade(self) -> None:
        self._log.info('Connection established')
        super().connectionMade()

    def signedOn(self) -> None:
        self._log.info('Sign-on complete')
        self.signOnComplete.callback(None)

    def connectionLost(self, reason):
        try:
            super().connectionLost(reason)
        finally:
            self.disconnected.callback(None)

    def privmsg(self, user: str, channel: str, message: str) -> None:
        sender = user.split('!', 1)[0]
        if channel == self.nickname:
            self._log.info(
                'privmsg from {sender}: {message!r}',
                sender=sender, message=message,
            )
            self.state.addPrivateMessage(sender, message)
        else:
            self._log.info(
                'message in {channel} from {sender}: {message!r}',
                channel=channel, sender=sender, message=message,
            )
            self.state.channels.get(channel).addMessage(sender, message)

    def joined(self, channel: str) -> None:
        self._log.info('I joined channel {channel}', channel=channel)
        self.state.channels.add(channel)
        self.state.actions.myJoins.complete(channel)

    def left(self, channel: str) -> None:
        self._log.info('I left {channel}', channel=channel)
        self.state.channels.remove(channel)

    def modeChanged(
        self,
        user: str,
        channel: str,
        set: bool,
        modes: Sequence[str],
        args: Sequence[str],
    ) -> None:
        modePfx = '-+'[set]
        if channel == self.nickname:
            # Server-level user mode change, ignore for now.
            self._log.info(
                'Mode change [{pfx}{modes}] for user {target}',
                pfx=modePfx, modes=modes, target=self.nickname,
            )
            return
        chan = self.state.channels.get(channel)
        for mode, arg in zip(modes, args):
            self._log.info(
                'Mode change for {target}: [{pfx}{mode}{maybearg}] by {user}',
                target=channel, pfx=modePfx, mode=mode, user=user,
                maybearg='' if arg is None else f' {arg}',
            )
            if mode == 'o':
                if set:
                    chan.operators.add(arg)
                else:
                    with contextlib.suppress(KeyError):
                        chan.operators.remove(arg)
                if arg == self.nickname:
                    with contextlib.suppress(_NoActionInFlight):
                        chan.opAttempts.complete(channel)
            if arg == self.nickname:
                # Channel user mode change affecting me.
                continue
            if mode == 'b':
                setterMask = user
                banMask = arg
                if set:
                    chan.addBan(mask=banMask, setter=setterMask)
                else:
                    chan.removeBan(mask=banMask, unsetter=setterMask)

    def kickedFrom(self, channel: str, kicker: str, message: str) -> None:
        self._log.info(
            'I was kicked from {channel} by {kicker}: {message}',
            channel=channel, kicker=kicker, message=message,
        )
        self.state.channels.remove(channel)

    def userJoined(self, user: str, channel: str) -> None:
        self._log.info(
            'User {user} joined {channel}',
            user=user, channel=channel,
        )
        self.state.channels.get(channel).addNick(user)

    def userLeft(self, user: str, channel: str) -> None:
        self._log.info(
            'User {user} left {channel}',
            user=user, channel=channel,
        )
        self.state.channels.get(channel).removeNick(user)

    def userQuit(self, user: str, quitMessage: str) -> None:
        self._log.info(
            'User {user} quit: {message!r}',
            user=user, message=quitMessage,
        )
        self.state.channels.userQuit(user)

    def userKicked(
        self, kickee: str, channel: str, kicker: str, message: str
    ) -> None:
        self._log.info(
            'User {kickee} was kicked from {channel} by {kicker}: {message!r}',
            kickee=kickee, channel=channel, kicker=kicker, message=message,
        )
        self.state.channels.get(channel).removeNick(kickee)

    def userRenamed(self, oldname: str, newname: str) -> None:
        self._log.info(
            'User {oldname} is now known as {newname}',
            oldname=oldname, newname=newname,
        )
        self.state.channels.userRenamed(oldname, newname)

    ### Low-level protocol events
    def irc_unknown(self, prefix, command, params):
        self._log.warn(
            "received command we aren't prepared to handle: "
                "{pfx} {cmd} {pms}",
            cmd=command, pfx=prefix, pms=params,
        )

    def lineReceived(self, line):
        self._log.debug('lineReceived({line!r})', line=line)
        super().lineReceived(line)

    def sendLine(self, line):
        self._log.debug('sendLine({line!r})', line=line)
        super().sendLine(line)

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

    ### NAMES
    # Ignore until otherwise necessary to handle, to avoid noisy logging
    # from `irc_unknown`.
    def irc_RPL_NAMREPLY(self, prefix, params): pass
    def irc_RPL_ENDOFNAMES(self, prefix, params): pass

    ###
    def irc_RPL_BANLIST(self, prefix, params):
        _, channelName, banMask, setterMask, when = params
        chan = self.state.channels.get(channelName)
        chan.addBan(mask=banMask, setter=setterMask)

    def irc_RPL_ENDOFBANLIST(self, prefix, params):
        channelName = params[1]
        chan = self.state.channels.get(channelName)
        with contextlib.suppress(_NoActionInFlight):
            chan.banlistReceives.complete(channelName)

    ### Ignore generic info replies
    def irc_RPL_LUSERUNKNOWN(self, prefix, params): pass
    def irc_RPL_STATSDLINE(self, prefix, params): pass
    def irc_RPL_LOCALUSERS(self, prefix, params): pass
    def irc_RPL_GLOBALUSERS(self, prefix, params): pass

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
    # ERR_TOOMANYMATCHES - to: NAMES or LIST
    # XXX: These could just be fatal, maybe.
    # ERR_NEEDMOREPARAMS - to: numerous commands
    # ERR_NOSUCHSERVER - to: numerous commands


def _add_numerics() -> None:
    """
    Update the IRC numerics registry in
    :mod:`twisted.words.protocols.irc`.
    """
    numeric_addendum = dict(
        RPL_WHOISACCOUNT='330',
        RPL_QUIETLIST='728',
        RPL_ENDOFQUIETLIST='729',
        # 250 is "reserved": https://tools.ietf.org/html/rfc2812#section-5.3
        RPL_STATSDLINE='250',
        RPL_LOCALUSERS='265',  # aka RPL_CURRENT_LOCAL
        RPL_GLOBALUSERS='266',  # aka RPL_CURRENT_GLOBAL
    )
    for name, numeric in numeric_addendum.items():
        irc.numeric_to_symbolic[numeric] = name
        irc.symbolic_to_numeric[name] = numeric

_add_numerics()

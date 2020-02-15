import collections
import itertools
import traceback
import operator
import sys
import re
from datetime import timedelta
from urllib import urlencode
from urlparse import urljoin

from twisted.words.protocols import irc
from twisted.internet import reactor, defer, error, protocol, task
from twisted.web import xmlrpc
from twisted import logger
import lxml.html

from infobob.redent import redent
from infobob import database, http, util
from infobob.pastebin import make_paster, make_repaster


numeric_addendum = dict(
    RPL_WHOISACCOUNT='330',
    RPL_QUIETLIST='728',
    RPL_ENDOFQUIETLIST='729',
)
for name, numeric in numeric_addendum.iteritems():
    irc.numeric_to_symbolic[numeric] = name
    irc.symbolic_to_numeric[name] = numeric

_lol_regex = re.compile(r'\b(lo+l[lo]*|rofl+|lmao+|lel|kek)z*\b', re.I)
_lol_message = '%s is a no-LOL zone.'


_EXEC_PRELUDE = """#coding:utf-8
import os, sys, math, re, random
"""
_MAX_LINES = 2


class Infobob(irc.IRCClient):
    identified = False
    outstandingPings = 0

    sourceURL = 'https://github.com/pound-python/infobob'
    versionName = 'infobob'
    versionNum = 'latest'
    versionEnv = 'twisted'

    db = dbpool = manhole_service = None
    log = logger.Logger()

    def __init__(self, conf, paster=None, repaster=None):
        self._conf = conf
        self._paster = paster or make_paster()
        self._repaster = repaster or make_repaster(self._paster)
        self.nickname = conf['irc.nickname'].encode()
        if conf['irc.password']:
            self.password = conf['irc.password'].encode()
        self.dbpool = conf.dbpool
        self.is_opped = set()
        self._op_deferreds = {}
        self.channel_collation = collections.defaultdict(dict)
        self.most_recent_bans = {}
        self._channel_update_deferred = defer.succeed(None)
        self._whois_collation = {}
        self._whois_deferred = None
        self._whois_queue = defer.DeferredSemaphore(1)
        self._waiting_on_queue = collections.defaultdict(
            lambda: defer.DeferredSemaphore(1))
        self._waiting_on_deferred = {}
        self._loopers = {}
        self._ban_collation = collections.defaultdict(list)
        self._quiet_collation = collections.defaultdict(list)

    def autojoinChannels(self):
        for channel in self._conf['irc.autojoin']:
            channel_obj = self._conf.channel(channel)
            self.join(channel_obj.name.encode(), channel_obj.key)

    def startTimer(self, name, interval, method, *a, **kw):
        def wrap():
            d = defer.maybeDeferred(method, *a, **kw)
            d.addErrback(
                lambda f: self.log.failure(
                    u'error in looper {name}',
                    f,
                    name=name,
                )
            )
            return d
        self._loopers[name] = looper = task.LoopingCall(wrap)
        looper.start(interval)

    def stopTimer(self, name):
        # FIXME: Probable should be self._loopers
        looper = self._looper.pop(name, None)
        if looper is not None:
            looper.stop()

    def signedOn(self):
        self.factory.resetDelay()
        nickserv_pw = self._conf['irc.nickserv_pw']
        if nickserv_pw:
            self.msg('NickServ', 'identify %s' % nickserv_pw.encode())
        else:
            self.autojoinChannels()
        self.startTimer('serverPing', 60, self._serverPing)
        self.startTimer('expireBans', 60, self._expireBans)
        self.startTimer('pastebinPing', 60*60*3, self._pastebinPing)

    def ensureOps(self, channel):
        if self._op_deferreds.get(channel) is None:
            self._op_deferreds[channel] = defer.Deferred()
            self.msg('ChanServ', 'op %s' % channel)
        return self._op_deferreds[channel]

    def _serverPing(self):
        if self.outstandingPings > 5:
            self.loseConnection()
        self.sendLine('PING bollocks')
        self.outstandingPings += 1

    def irc_PONG(self, prefix, params):
        self.outstandingPings -= 1

    def msg(self, target, message):
        # Prevent excess flood.
        irc.IRCClient.msg(self, target, message[:512])

    def irc_INVITE(self, prefix, params):
        self.invited(params[1], prefix)

    def invited(self, channel, inviter):
        self.join(channel)

    def kickedFrom(self, channel, kicker, message):
        self.join(channel)

    @defer.inlineCallbacks
    def whois(self, nickname, server=None):
        yield self._whois_queue.acquire()
        try:
            self._whois_deferred = defer.Deferred()
            irc.IRCClient.whois(self, nickname, server)
            ret = yield self._whois_deferred
            self._whois_deferred = None
            defer.returnValue(ret)
        finally:
            self._whois_queue.release()

    def irc_unknown(self, command, prefix, params):
        self.log.warn(
            u"received command we aren't prepared to handle: {cmd} {pfx} {pms}",
            cmd=command, pfx=prefix, pms=params,
        )

    def irc_RPL_WHOISUSER(self, prefix, params):
        c = self._whois_collation
        c['nick'], c['user'], c['host'] = params[1:4]
        c['realname'] = params[-1]

    def irc_RPL_WHOISACCOUNT(self, prefix, params):
        self._whois_collation['accountname'] = params[2]

    def irc_RPL_ENDOFWHOIS(self, prefix, params):
        ret, self._whois_collation = self._whois_collation, {}
        self._whois_deferred.callback(ret)

    def who(self, target):
        self.sendLine('WHO %s' % (target,))

    def joined(self, channel):
        channel_obj = self._conf.channel(channel)
        if channel_obj.anti_redirect:
            self.part(channel)
            reactor.callLater(5, self.join, channel_obj.anti_redirect.encode())
            return

        self.who(channel)
        if channel_obj.have_ops:
            self.mode(channel, True, 'b')
            self.mode(channel, True, 'q')

    def irc_RPL_WHOREPLY(self, prefix, params):
        channel, user, host, _, nick = params[1:6]
        self.channel_collation[channel][nick] = '%s@%s' % (user, host)

    def irc_RPL_ENDOFWHO(self, prefix, params):
        channel = params[1]
        self.fillChannel(self.channel_collation.pop(channel), channel)

    def irc_RPL_BANLIST(self, prefix, params):
        _, channel, mask, setter, when = params
        self._ban_collation[channel].append((mask, setter, when))

    def irc_RPL_ENDOFBANLIST(self, prefix, params):
        channel = params[1]
        bans = self._ban_collation.pop(channel, [])
        self.dbpool.ensure_active_bans(channel, 'b', bans)

    def irc_RPL_QUIETLIST(self, prefix, params):
        _, channel, _, mask, setter, when = params
        self._quiet_collation[channel].append((mask, setter, when))

    def irc_RPL_ENDOFQUIETLIST(self, prefix, params):
        channel = params[1]
        quiets = self._quiet_collation.pop(channel, [])
        self.dbpool.ensure_active_bans(channel, 'q', quiets)

    def _blockChannelUpdates(self):
        if self._channel_update_deferred.called:
            self._channel_update_deferred = defer.Deferred()

    def _unblockChannelUpdates(self):
        if not self._channel_update_deferred.called:
            self._channel_update_deferred.callback(None)

    @defer.inlineCallbacks
    def fillChannel(self, users, channel):
        yield self._channel_update_deferred
        yield self.dbpool.set_users_in_channel(users, channel)

    @defer.inlineCallbacks
    def addNick(self, nick, host, channel):
        yield self._channel_update_deferred
        yield self.dbpool.add_user_to_channel(nick, host, channel)

    @defer.inlineCallbacks
    def removeNick(self, nick, channel=None):
        yield self._channel_update_deferred
        if channel is None:
            yield self.dbpool.remove_nick_from_channels(nick)
        else:
            yield self.dbpool.remove_nick_from_channel(nick, channel)

    @defer.inlineCallbacks
    def renameNick(self, oldname, newname):
        yield self._channel_update_deferred
        yield self.dbpool.rename_nick(oldname, newname)

    def irc_JOIN(self, prefix, params):
        """
        Called when a user joins a channel.
        """
        nick, _, host = prefix.partition('!')
        channel = params[-1]
        if nick == self.nickname:
            self.joined(channel)
        else:
            self.userJoined(prefix, channel)

    def userJoined(self, user, channel):
        nick, _, host = user.partition('!')
        self.addNick(nick, host, channel)

    def userLeft(self, user, channel):
        nick, _, host = user.partition('!')
        self.removeNick(nick, channel)

    def userQuit(self, user, quitMessage):
        nick, _, host = user.partition('!')
        self.removeNick(nick)

    def userKicked(self, kickee, channel, kicker, message):
        nick, _, host = kickee.partition('!')
        self.removeNick(nick, channel)

    def userRenamed(self, oldname, newname):
        self.renameNick(oldname, newname)

    def connectionLost(self, reason):
        if self.dbpool:
            self.dbpool.close()
        irc.IRCClient.connectionLost(self, reason)

    @defer.inlineCallbacks
    def waitForPrivmsgFrom(self, nick, waitFor=1200):
        semaphore = self._waiting_on_queue[nick]
        yield semaphore.acquire()
        d = self._waiting_on_deferred[nick] = defer.Deferred()
        timeout = reactor.callLater(waitFor, d.errback, error.TimeoutError())
        def _release(r):
            if timeout.active():
                timeout.cancel()
            semaphore.release()
            return r
        d.addBoth(_release)
        defer.returnValue((d,))

    def privmsg(self, user, channel, message):
        self._autojoinIfJustIdentified(user, message)
        if not user: return
        user = user.split('!', 1)[0]
        if user.lower() in ('nickserv', 'chanserv', 'memoserv',
                self.nickname.lower()):
            return
        if channel == self.nickname:
            d = self._waiting_on_deferred.pop(user, None)
            if d is not None:
                d.callback(message)
                return
            self.log.info(
                u'privmsg from {user}: {message}', user=user, message=message
            )
            target = user
            channel_obj = self._conf.channel('privmsg')
        else:
            target = channel
            channel_obj = self._conf.channel(channel)
        _ = channel_obj.translate
        if channel_obj.is_usable('lol') and _lol_regex.search(message):
            self.do_lol(user, channel, _)
        if channel_obj.is_usable('repaste'):
            to_repaste = self._repaster.extractBadPasteSpecs(message)
            if to_repaste:
                self.repaste(target, user, to_repaste, _)

        m = re.match(
            r'^s*%s\s*[,:> ]+(\S?.*?)[.!?]?\s*$' % self.nickname, message, re.I)
        if m:
            command, = m.groups()
        elif channel == self.nickname:
            command = message
        else:
            command = None

        if command:
            s_command = command.split(' ')
            command_func = getattr(self, 'infobob_' + s_command[0], None)
            if command_func is not None and channel_obj.is_usable(s_command[0]):
                command_func(target, channel_obj, *s_command[1:])

    def noticed(self, user, channel, message):
        self._autojoinIfJustIdentified(user, message)

    def _autojoinIfJustIdentified(self, user, message):
        """
        Trigger autojoin if nickserv told us we've been identified.
        """
        if (
            not self.identified
            and user.lower().startswith('nickserv!')
            and ('identified' in message or 'recognized' in message)
        ):
            self.identified = True
            self.autojoinChannels()

    def modeChanged(self, user, channel, set, modes, args):
        for mode, arg in zip(modes, args):
            if mode == 'o' and arg == self.nickname:
                was_opped = channel in self.is_opped
                is_opped = set
                if is_opped and not was_opped:
                    self.is_opped.add(channel)
                    self._op_deferreds.setdefault(channel, defer.Deferred()
                        ).callback(None)
                    reactor.callLater(60*5, self._deopSelf)
                elif not is_opped and was_opped:
                    self.is_opped.remove(channel)
                    self._op_deferreds.pop(channel, None)
                self.is_opped = self.is_opped

            elif mode in ('b', 'q'):
                self.updateBan(user, channel, set, mode, arg)

    @defer.inlineCallbacks
    def updateBan(self, user, channel, mode_set, mode, mask):
        channel_obj = self._conf.channel(channel)
        if not channel_obj.have_ops:
            return
        _ = channel_obj.translate

        nick, _x, host = user.partition('!')
        self._blockChannelUpdates()
        try:
            if mode_set:
                if nick != self.nickname:
                    rowid = yield self.dbpool.add_ban(channel, user, mask, mode)
            else:
                not_expired = yield self.dbpool.remove_ban(
                    channel, user, mask, mode)
            if (not mode_set and not not_expired) or nick == self.nickname:
                return
            elif mask.startswith('$'):
                others = None
            elif mode_set:
                others = yield self.dbpool.check_mask(channel, mask)
        finally:
            self._unblockChannelUpdates()

        if not mode_set:
            if not_expired:
                set_by, set_at, expire_at = not_expired[0]
                set_nick, _x, set_host = set_by.partition('!')
                self.msg(nick,
                    _(u'fyi: %(who)s set "+%(mode)s %(mask)s" on %(channel)s '
                    u'%(when)s, which was set to expire %(expire)s.') % dict(
                        who=set_nick, mode=mode, mask=mask, channel=channel,
                        when=util.ctime(_, set_at),
                        expire=util.ctime(_, expire_at),
                    )
                )
            return

        if others is not None:
            if not others:
                self.msg(nick,
                    _(u'fyi: nobody on %(channel)s matches the mask '
                    u'%(mask)r') % dict(
                        channel=channel, mask=mask,
                    )
                )

            elif len(others) > 5:
                self.msg(nick,
                    _(u'fyi: more than 5 nicks on %(channel)s match the mask '
                    u'%(mask)r, including: %(affected)s') % dict(
                        channel=channel, mask=mask,
                        affected=', '.join(others[:5]),
                    )
                )

            else:
                others_by_account = collections.defaultdict(list)
                info_by_nick = {}
                for o_nick in others:
                    info = yield self.whois(o_nick)
                    others_by_account[info.get('accountname')].append(info)
                    info_by_nick[o_nick] = info
                # Fetch others_by_account[None] so that it'll always be
                # included in len(others_by_account), and we can always take
                # its length - 1.
                n_affected_nicks = (
                    len(others_by_account[None]) + len(others_by_account) - 1)

                if n_affected_nicks > 1 and len(others_by_account) > 1:
                    affected = ', '.join(
                        '%s (%s)' % (
                            account if account is not None
                                else _(u'[no account]'),
                            ', '.join(info['nick'] for info in infos)
                        )
                        for account, infos in others_by_account.iteritems())
                    ready, = yield self.waitForPrivmsgFrom(nick)
                    self.msg(nick,
                        _(u'fyi: more than one account on %(channel)s matches '
                        u'the mask %(mask)r, including: %(affected)s') % dict(
                            channel=channel, mask=mask, affected=affected,
                        )
                    )
                    self.msg(nick,
                        _(u'reply with a nickname to disambiguate the mask, '
                        u'or "(none)" (without quotes, with parentheses) to '
                        u'ignore this warning.')
                    )
                    correct = set(info_by_nick) | set([_(u'(none)')])
                    while True:
                        try:
                            msg = yield ready
                        except error.TimeoutError:
                            self.msg(nick,
                                _(u'timeout; not disambiguating.'))
                            msg = None
                            break
                        if msg in correct:
                            break
                        ready, = yield self.waitForPrivmsgFrom(nick)
                        self.msg(nick,
                            _(u'%(msg)r is not in %(correct)r') % dict(
                                msg=msg, correct=correct,
                            )
                        )
                    if msg is not None and msg != _(u'(none)'):
                        yield self.ensureOps(channel)
                        info = info_by_nick[msg]
                        if 'accountname' in info:
                            new_mask = '$a:%(accountname)s' % info
                        else:
                            new_mask = '%(nick)s!*@*' % info
                        self.msg(nick,
                            _(u'updating %(old)r to %(new)r.') % dict(
                                old=mask, new=new_mask,
                            )
                        )
                        self.mode(channel, True, mode, mask=new_mask)
                        self.mode(channel, False, mode, mask=mask)
                        rowid = yield self.dbpool.add_ban(
                            channel, user, new_mask, mode)
                        mask = new_mask

                elif n_affected_nicks == 1 and not others_by_account[None]:
                    account, = (account for account in others_by_account
                        if account is not None)
                    ready, = yield self.waitForPrivmsgFrom(nick)
                    self.msg(nick,
                        _(u'the mask %(mask)r on %(channel)s matches only one '
                        u'account (%(account)s). change this to a per-account '
                        u'mask? (y/n)') % dict(
                            mask=mask, channel=channel, account=account,
                        )
                    )
                    try:
                        msg = yield ready
                    except error.TimeoutError:
                        self.msg(nick,
                            _(u'timeout; changing to per-account mask.'))
                        msg = 'yes'
                    if msg.lower().startswith('y'):
                        yield self.ensureOps(channel)
                        new_mask = '$a:%s' % account
                        self.msg(nick,
                            _(u'updating %(old)r to %(new)r.') % dict(
                                old=mask, new=new_mask,
                            )
                        )
                        self.mode(channel, True, mode, mask=new_mask)
                        self.mode(channel, False, mode, mask=mask)
                        rowid = yield self.dbpool.add_ban(
                            channel, user, new_mask, mode)
                        mask = new_mask

        auth = yield self.dbpool.add_ban_auth(rowid)
        url = urljoin(
            self._conf['web.url'], '/bans/edit/%s/%s' % (rowid, auth)
        ).encode()
        self.msg(nick, _(u'to enter and edit details about this ban, please visit %s') % (url,))

    def _deopSelf(self):
        for channel in self.is_opped:
            self.mode(channel, False, 'o', user=self.nickname)

    @defer.inlineCallbacks
    def _expireBans(self):
        expired = yield self.dbpool.get_expired_bans()
        for channel, it in itertools.groupby(expired, operator.itemgetter(0)):
            if not self._conf.channel(channel).have_ops:
                continue
            yield self.ensureOps(channel)
            for _, mask, mode in it:
                self.mode(channel, False, mode, mask=mask)

    @defer.inlineCallbacks
    def do_lol(self, nick, channel, _):
        yield self.dbpool.add_lol(nick)
        self.msg(nick, _(_lol_message) % channel)

    def pastebin(self, language, data):
        d = self._paster.createPaste(language, data)
        d.addCallback(lambda binurl: binurl.encode('utf-8'))
        return d

    def _pastebinPing(self):
        return self._paster.checkAvailabilities()

    @defer.inlineCallbacks
    def repaste(self, target, user, pastes, _):
        repasted_url = yield self._repaster.repaste(pastes)
        if repasted_url is None:
            return

        self.msg(target, _(u'%(url)s (repasted for %(user)s)') %
            dict(url=repasted_url.encode('utf-8'), user=user))

    @defer.inlineCallbacks
    def infobob_redent(self, target, channel, paste_target, *text):
        _ = channel.translate
        redented = (
            redent(' '.join(text).decode('utf8', 'replace')).encode('utf8'))
        try:
            paste_url = yield self.pastebin(redented, u'python')
        except:
            self.msg(target, _(u'Error: %r') % sys.exc_info()[1])
            raise
        else:
            self.msg(target, '%s, %s' % (paste_target, paste_url))

    def infobob_stop(self, target, channel):
        _ = channel.translate
        self.msg(target, _(u'Okay!'))
        reactor.stop()

class InfobobFactory(protocol.ReconnectingClientFactory):
    protocol = Infobob
    maxDelay = 120
    lastProtocol = None

    def __init__(self, conf):
        self._conf = conf

    def buildProtocol(self, addr):
        self.lastProtocol = p = self.protocol(self._conf)
        p.factory = self
        return p

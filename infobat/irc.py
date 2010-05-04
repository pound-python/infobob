from twisted.internet import reactor, protocol, task, defer
from twisted.enterprise import adbapi
from twisted.protocols import amp
from twisted.python import log, reflect
from twisted.web import xmlrpc
from infobat.redent import redent
from infobat.config import conf
from infobat import chains, database, http, util
from datetime import datetime
from urllib import urlencode
from urlparse import urljoin
import lxml.html
import operator
import ampirc
import random
import time
import sys
import os
import re

_lol_regex = re.compile(r'\b(lo+l[lo]*|rofl+|lmao+)z*\b', re.I)
_lol_messages = [
    '%s is a no-LOL zone.',
    'i mean it: no LOL in %s.',
    'seriously, dude, no LOL in %s.',
]

_etherpad_like = ['ietherpad.com', 'piratepad.net', 'piratenpad.de',
    'pad.spline.de', 'typewith.me', 'edupad.ch', 'etherpad.netluchs.de',
    'meetingworlds.com', 'netpad.com.br', 'openetherpad.org', 'titanpad.com',
    'pad.telecomix.org']

_etherpad_like_regex = '|'.join(re.escape(ep) for ep in _etherpad_like)

_bad_pastebin_regex = re.compile(
    r'((?:https?://)?((?:[a-z0-9-]+\.)*)(pastebin\.(?:com|org|ca)'
    r'|%s)/)([a-z0-9]+)/?' % (_etherpad_like_regex,), re.I)

_pastebin_raw = {
    'pastebin.com': 'http://%spastebin.com/download.php?i=%s',
    'pastebin.org': 'http://%spastebin.org/pastebin.php?dl=%s',
    'pastebin.ca': 'http://%spastebin.ca/raw/%s',
}

for ep in _etherpad_like:
    _pastebin_raw[ep] = 'http://%%s%s/ep/pad/export/%%s/latest?format=txt' % (ep,)

_EXEC_PRELUDE = """#coding:utf-8
import os, sys, math, re, random
"""
_MAX_LINES = 2

class CouldNotPastebinError(Exception):
    pass

class Infobat(ampirc.IrcChildBase):
    datastore_properties = [
        'is_opped', 'outstandingPings', 'identified', 'countdown'
    ]
    identified = False
    outstandingPings = 0

    sourceURL = 'https://code.launchpad.net/~pound-python/infobat/infobob'
    versionName = 'infobat-infobob'
    versionNum = 'latest'
    versionEnv = 'twisted'

    db = dbpool = manhole_service = None

    def __init__(self, amp, uuid):
        ampirc.IrcChildBase.__init__(self, amp, uuid)
        self.nickname = conf['irc.nickname'].encode()
        self.max_countdown = conf['database.dbm.sync_time']
        self.dbpool = database.InfobatDatabaseRunner()
        self._load_database()
        self.is_opped = set()
        self._op_deferreds = {}
        if (conf['misc.manhole.socket_prefix'] is not None
                and conf['misc.manhole.passwd_file']):
            from twisted.conch.manhole_tap import makeService
            self.manhole_service = service = makeService(dict(
                telnetPort="unix:%s%d.sock" % (
                    conf['misc.manhole.socket_prefix'], os.getpid()),
                sshPort=None,
                namespace={'self': self},
                passwd=conf['misc.manhole.passwd_file'],
            ))
            service.startService()
            reactor.addSystemEventTrigger(
                'before', 'shutdown', service.stopService)

    def autojoinChannels(self):
        for channel in conf['irc.autojoin']:
            channel_obj = conf.channel(channel)
            self.join(channel_obj.name.encode(), channel_obj.key)

    def signedOn(self):
        nickserv_pw = conf['irc.nickserv_pw']
        if nickserv_pw:
            self.msg('NickServ', 'identify %s' % nickserv_pw.encode())
        else:
            self.autojoinChannels()
        self.startTimer('serverPing', 60)

    def protocolReady(self, first_time=False):
        self._op_deferreds = dict.fromkeys(self.is_opped, defer.succeed(None))
        if first_time:
            self.startTimer('dbsync', 30)
            self.startTimer('pastebinPing', 60*60*3)
            self.countdown = self.max_countdown

    def ensureOps(self, channel):
        if self._op_deferreds.get(channel) is None:
            self._op_deferreds[channel] = defer.Deferred()
            self.msg('ChanServ', 'op %s' % channel)
        return self._op_deferreds[channel]

    def ampircTimer_serverPing(self):
        if self.outstandingPings > 5:
            self.loseConnection()
        self.sendLine('PING bollocks')
        self.outstandingPings += 1

    def irc_PONG(self, prefix, params):
        self.outstandingPings -= 1

    def msg(self, target, message):
        # Prevent excess flood.
        ampirc.IrcChildBase.msg(self, target, message[:512])

    def irc_INVITE(self, prefix, params):
        self.invited(params[1], prefix)

    def invited(self, channel, inviter):
        self.join(channel)

    def kickedFrom(self, channel, kicker, message):
        self.join(channel)

    def connectionLost(self, reason):
        if self.db:
            self.db.sync()
        if self.dbpool:
            self.dbpool.close()
        ampirc.IrcChildBase.connectionLost(self, reason)

    def _load_database(self):
        self.db = chains.Database(conf['database.dbm.db_file'].encode())

    def ampircTimer_dbsync(self):
        if self.db is None:
            return
        self.countdown -= 1
        if self.countdown == 0:
            self.db.sync()
            self.countdown = self.max_countdown

    @defer.inlineCallbacks
    def ampircTimer_pastebinPing(self):
        def _eb(_):
            return None, None
        def _cb((latency, _), name):
            return defer.DeferredList([
                self.dbpool.record_is_up(name, bool(latency)),
                self.dbpool.set_latency(name, latency),
            ])
        def do_ping((name, url)):
            proxy = xmlrpc.Proxy(url + '/xmlrpc/')
            d = proxy.callRemote('pastes.getLanguages')
            util.time_deferred(d)
            d.addErrback(_eb)
            d.addCallback(_cb, name)
            return d
        pastebins = yield self.dbpool.get_all_pastebins()
        yield util.parallel(pastebins, 10, do_ping)

    def learn(self, string, action=False):
        if self.db is None:
            return
        self.db.learn(self.sanitize_learn_input(string), action)
       
    def sanitize_learn_input(self, string):
        """Remove extraneous/irrelevant data from input to markov chain
        
        - remove nickname prefixes: "nick: foo bar" -> "foo bar"
        
        """
        # channel is included for future use, e.g. in checking the channel
        # username list
        
        # doing more is a good idea?
        
        # TODO: use real usernames from the nick list
        # idea: handle "nick1, nick2: thanks!"
        # idea: handle obviously technical data like URIs. Too hard? heheh.
        
        nick_regex = (r'(?P<nick>[A-Za-z\x5b-\x60\x7b-\x7d]'
            r'[0-9A-Za-z\x5b-\x60\x7b-\x7d\-]*)') # RFC2812 2.3.1 , extended
        regex = re.compile(nick_regex + r'\s*(?P<sep>:)\s*(?P<message>.*)')
        
        match = regex.match(string)
        
        if match is None:
            sanitized = string
        else:
            # in future, do elif to check if the nick group is actually used
            # in the channel
            sanitized = match.group('message')
        
        return sanitized

    def action(self, user, channel, message):
        if not user:
            return
        if channel == self.nickname:
            log.msg('privaction: * %s %s' % (user, message))
            target = user
            channel_obj = conf.channel('privmsg')
        else:
            target = channel
            channel_obj = conf.channel(channel)
        if channel != self.nickname and channel_obj.is_usable('chain_learn'):
            self.learn(message, True)

    def privmsg(self, user, channel, message):
        learn_this = channel != '*'
        if (not self.identified and user.lower().startswith('nickserv!') and
                'identified' in message):
            self.identified = True
            self.autojoinChannels()
        if not user: return
        user = user.split('!', 1)[0]
        if user.lower() in ('nickserv', 'chanserv', 'memoserv'): return
        if channel == self.nickname:
            log.msg('privmsg from %s: %s' % (user, message))
            target = user
            channel_obj = conf.channel('privmsg')
        else:
            target = channel
            channel_obj = conf.channel(channel)

        if channel_obj.is_usable('anti_trigger') and (
                message.startswith('!') and message.lstrip('!')):
            self.notice(user, 'no triggers in %s.' % channel)
        if channel_obj.is_usable('lol') and _lol_regex.search(message):
            self.do_lol(user, channel)
        if channel_obj.is_usable('repaste'):
            to_repaste = set(_bad_pastebin_regex.findall(message))
            if to_repaste:
                self.repaste(target, user, to_repaste)

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
            command_func = getattr(self, 'infobat_' + s_command[0], None)
            if command_func is not None and channel_obj.is_usable(s_command[0]):
                command_func(target, *s_command[1:])
                learn_this = False
            elif self.db and channel_obj.is_usable('chain_splice'):
                action, result = self.db.splice()
                if action:
                    self.describe(target, result)
                else:
                    if channel != self.nickname:
                        result = user + ', ' + result
                    self.msg(target, result)

        if (learn_this and channel != self.nickname 
                and channel_obj.is_usable('chain_learn')):
            self.learn(message)

    def modeChanged(self, user, channel, set, modes, args):
        for mode, arg in zip(modes, args):
            if mode == 'o' and arg == self.nickname:
                was_opped = channel in self.is_opped
                is_opped = set
                if is_opped and not was_opped:
                    self.is_opped.add(channel)
                    self._op_deferreds.setdefault(channel, defer.Deferred()
                        ).callback(None)
                    self.startTimer('deopSelf', 60*5, alsoRunImmediately=False)
                elif not is_opped and was_opped:
                    self.is_opped.remove(channel)
                    self._op_deferreds.pop(channel, None)
                    self.stopTimer('deopSelf')
                # XXX: Ugly hack since is_opped is mutable.
                self.is_opped = self.is_opped

    def ampircTimer_deopSelf(self):
        for channel in self.is_opped:
            self.mode(channel, False, 'o', user=self.nickname)

    @defer.inlineCallbacks
    def do_lol(self, nick, channel):
        offenses = yield self.dbpool.add_lol(nick)
        message_idx = min(offenses, len(_lol_messages)) - 1
        self.notice(nick, _lol_messages[message_idx] % channel)

    @defer.inlineCallbacks
    def pastebin(self, language, data):
        for name, url in (yield self.dbpool.get_pastebins()):
            proxy = xmlrpc.Proxy(url + '/xmlrpc/')
            try:
                new_paste_id = yield proxy.callRemote(
                    'pastes.newPaste', language, data)
            except:
                log.err()
                yield self.dbpool.set_latency(name, None)
                yield self.dbpool.record_is_up(name, False)
                continue
            else:
                yield self.dbpool.record_is_up(name, True)
                defer.returnValue('%s/show/%s/' % (url, new_paste_id))
        raise CouldNotPastebinError()

    @defer.inlineCallbacks
    def repaste(self, target, user, pastes):
        which_bin = ', '.join(set(bin for _, _, bin, _ in pastes))
        self.notice(user, 'in the future, please use a less awful pastebin '
            '(e.g. paste.pocoo.org) instead of %s.' % which_bin)
        urls = '|'.join(sorted(base + p_id for base, _, _, p_id in pastes))
        repasted_url = yield self.dbpool.get_repaste(urls)
        if repasted_url is None:
            defs = [http.get_page(_pastebin_raw[bin] % (prefix, paste_id))
                for _, prefix, bin, paste_id in pastes]
            pastes_data = yield defer.gatherResults(defs)
            if len(pastes_data) == 1:
                data = pastes_data[0][0]
                language = 'python'
            else:
                data = '\n'.join('### %s.py\n%s' % (paste_id, paste)
                    for (_, _, _, paste_id), (paste, _)
                    in zip(pastes, pastes_data))
                language = 'multi'
            repasted_url = yield self.pastebin(language, data)
            yield self.dbpool.add_repaste(urls, repasted_url)
        self.msg(target, '%s (repasted for %s)' % (repasted_url, user))

    @defer.inlineCallbacks
    def infobat_redent(self, target, paste_target, *text):
        redented = (
            redent(' '.join(text).decode('utf8', 'replace')).encode('utf8'))
        try:
            paste_url = yield self.pastebin('python', redented)
        except:
            self.msg(target, 'Error: %r' % sys.exc_info()[1])
            raise
        else:
            self.msg(target, '%s, %s' % (paste_target, paste_url))

    @defer.inlineCallbacks
    def _codepad(self, code, lang='Python', run=True):
        redented = redent(code.decode('utf8', 'replace')).encode('utf8')
        post_data = dict(
            code=redented, lang='Python', submit='Submit', private='True')
        if run:
            post_data['run'] = 'True'
        post_data = urlencode(post_data)
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        ign, fac = yield http.get_page('http://codepad.org/',
            method='POST', postdata=post_data, headers=headers)
        paste_url = urljoin('http://codepad.org/',
            fac.response_headers['location'][0])
        defer.returnValue(paste_url)

    @defer.inlineCallbacks
    def infobat_codepad(self, target, paste_target, *text):
        try:
            paste_url = yield self._codepad(' '.join(text))
        except:
            self.msg(target, 'Error: %r' % sys.exc_info()[1])
            raise
        else:
            self.msg(target, '%s, %s' % (paste_target, paste_url))

    @defer.inlineCallbacks
    def infobat_exec(self, target, *text):
        try:
            paste_url = yield self._codepad(_EXEC_PRELUDE + ' '.join(text))
            page, ign = yield http.get_page(paste_url)
        except:
            self.msg(target, 'Error: %r' % sys.exc_info()[1])
            raise
        else:
            doc = lxml.html.fromstring(page.decode('utf8', 'replace'))
            response = u''.join(doc.xpath("//a[@name='output']"
                "/following-sibling::div/table/tr/td[2]/div/pre/text()"))
            response = [line.rstrip()
                for line in response.encode('utf-8').splitlines()
                if line.strip()]
            nlines = len(response)
            if nlines > _MAX_LINES:
                response[_MAX_LINES-1:] = ['(... %d lines, entire response in '
                    '%s ...)' % (nlines, paste_url)]
            for part in response:
                self.msg(target, part)

    def infobat_print(self, target, *text):
        """Alias to print the result, aka eval"""
        return self.infobat_exec(target, 'print', *text)

    def infobat_sync(self, target):
        if self.db is not None:
            self.db.sync()
            self.msg(target, 'Done.')
        else:
            self.msg(target, 'Database not loaded.')

    def infobat_unlock(self, target):
        if self.db is not None:
            self.stopTimer('dbsync')
            self.db.sync()
            self.db.close()
            self.db = None
            self.msg(target, 'Database unlocked.')
        else:
            self.msg(target, 'Database was unlocked.')

    def infobat_lock(self, target):
        if self.db is None:
            self._load_database()
            self.startTimer('dbsync', 30)
            self.msg(target, 'Database locked.')
        else:
            self.msg(target, 'Database was unlocked.')

    def infobat_stats(self, target):
        if self.db is None: return
        delta = datetime.now() - self.amp.parent_start
        timestr = []
        if delta.days:
            timestr.append('%d days' % delta.days)
        minutes, seconds = divmod(delta.seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            timestr.append('%d hours' % hours)
        if minutes:
            timestr.append('%d minutes' % minutes)
        if seconds:
            timestr.append('%d seconds' % seconds)
        if not timestr:
            timestr = ''
        elif len(timestr) == 1:
            timestr = timestr[0]
        else:
            timestr = '%s and %s' % (', '.join(timestr[:-1]), timestr[-1])
        result = ("I have been online for %s. In that time, I've processed %d "
            "characters and spliced %d chains. Currently, I reference %d "
            "chains with %d beginnings (%d actions).") % (
                timestr, self.db.wordcount, self.db.chaincount, len(self.db),
                self.db.start_offset + self.db.actions, self.db.actions
            )
        self.msg(target, result)

    def infobat_divine(self, target, *seed):
        fortunes = conf['misc.magic8_file']
        if not fortunes:
            log.msg('no magic8 file')
            return
        self.describe(target, 'shakes the psychic black sphere.')
        r = random.Random(''.join(seed) + datetime.now().isoformat())
        st = open(fortunes.encode())
        l = st.readlines()
        st.close()
        l = r.choice(l).strip()
        self.msg(target, 'It says: "%s"' % l)

    def infobat_probability(self, target, *sentence):
        if self.db is None: return
        sentence = ' '.join(sentence) + '\0'
        if len(sentence) < chains.ORDER:
            return
        start_count = 0
        search = sentence[:chains.ORDER]
        for which in xrange(self.db.start_fragment + 1):
            chain = self.db['__start%d__' % which]
            idx = -1
            while True:
                idx = chain.find(search, idx + 1)
                if idx == -1:
                    break
                elif idx % chains.ORDER == 0:
                    start_count += 1
        probabilities = [float(start_count) / self.db.start_offset]
        for start in xrange(len(sentence) - chains.ORDER):
            chunk = sentence[start:start + chains.ORDER]
            next = sentence[start + chains.ORDER]
            chain = self.db.get(chunk)
            if chain:
                chain = chains.Chain(chain)
                probabilities.append(float(chain[next]) / sum(chain.data))
            else:
                probabilities.append(0)
        tot_probability = reduce(operator.mul, probabilities)
        average = sum(probabilities) / len(probabilities)
        std_dev = (sum((i - average) ** 2 for i in probabilities) /
            len(probabilities)) ** .5
        try:
            inverse = '%.0f' % (1 / tot_probability)
        except (OverflowError, ZeroDivisionError):
            inverse = 'inf'
        self.msg(target,
            '%0.6f%% chance (1 in %s) across %d probabilities; '
            '%0.6f%% average, standard deviation %0.6f%%, '
            '%0.6f%% low, %0.6f%% high.' % (
                tot_probability * 100, inverse,
                len(probabilities),
                average * 100, std_dev * 100,
                min(probabilities) * 100, max(probabilities) * 100))

    def infobat_reload(self, target):
        self.msg(target, 'Okay!')
        self.reload()

class InfobatAmpIrcChild(ampirc.AmpIrcChild):
    childProtocol = Infobat

    def __init__(self, config_loc, start_time):
        with open(config_loc) as cfgFile:
            conf.load(cfgFile)
        self.parent_start = datetime.strptime(start_time, util.ISOFORMAT)
        ampirc.AmpIrcChild.__init__(self)

class InfobatFactory(ampirc.AmpIrcFactory):
    ampChildProtocol = InfobatAmpIrcChild

    def __init__(self):
        ampirc.AmpIrcFactory.__init__(self)
        self.start = datetime.now()

    def getExtraArguments(self):
        return (
            conf.config_loc,
            self.start.strftime(util.ISOFORMAT)
        )

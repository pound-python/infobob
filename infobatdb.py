#!/usr/bin/twistd -y
from __future__ import with_statement
import collections
import operator
import random
import struct
import bsddb
import time
import sys
import re
from lxml import html
from urllib import urlencode
from urlparse import urljoin
from datetime import datetime
from pygments import highlight
from pygments.filter import Filter
from pygments.formatters import NullFormatter
from pygments.lexers import PythonLexer
from pygments.token import Token
from twisted.web import xmlrpc, client
from twisted.words.protocols import irc
from twitsed.python import log
from twisted.internet import reactor, protocol, task, defer
from twisted.protocols import policies
from twisted.enterprise import adbapi
from twisted.application import internet, service
ORDER = 8
FRAGMENT = ORDER * 16384
punctuation = set(' .!?')
_lol_regex = re.compile(r'\b([lo]{3,}|rofl+|lmao+)z*\b', re.I)
_lol_messages = [
    '#python is a no-LOL zone.', 
    'i mean it: no LOL in #python.', 
    'seriously, dude, no LOL in #python.']
_bad_pastebin_regex = re.compile(
    r'((https?://)?([a-z0-9-]+\.)*(pastebin\.(com|org|ca)|etherpad\.com)/)'
    r'([a-z0-9]+)/?', re.I)
_pastebin_textareas = {
    'pastebin.ca': 'content',
    'pastebin.com': 'code2',
    'pastebin.org': 'code2'}
start = datetime.now()

import ConfigParser
conf = ConfigParser.ConfigParser(dict(port='6667', channels='#infobat'))
conf.read(['/usr/infobob/infobat.cfg'])

_chain_struct = struct.Struct('>128I')
class Chain(object):
    def __init__(self, data=None):
        if data is None:
            self.data = [0] * 128
        else:
            self.data = list(_chain_struct.unpack(data))
    
    def __setitem__(self, key, value):
        self.data[ord(key)] = value
    def __getitem__(self, key):
        return self.data[ord(key)]
    def __delitem__(self, key):
        self.data[ord(key)] = 0
    
    def pack(self):
        return _chain_struct.pack(*self.data)
    
    def choice(self):
        which = random.randrange(sum(self.data))
        partial_sum = 0
        for index, val in enumerate(self.data):
            if val + partial_sum > which:
                return chr(index)
            partial_sum += val
    
    def append(self, key):
        self.data[ord(key)] += 1
    
    def merge(self, data=None, chainobj=None):
        if chainobj is None:
            chainobj = Chain(data)
        self.data = [v1 + v2 for v1, v2 in zip(self.data, chainobj.data)]

class Database(object):
    def __init__(self, filename):
        self.filename = filename
        self.start_updates = []
        self.act_updates = []
        self.updates = collections.defaultdict(Chain)
        self.db = bsddb.hashopen(filename, 'c')
        if '__offset__' not in self.db:
            self.db['__offset__'] = '0;0'
            self.db['__fragment__'] = '0;0'
            self.db['__start0__'] = ''
            self.db['__act0__'] = ''
            self.db['__length__'] = '0'
            self.db.sync()
        self.start_offset, self.actions = [
            int(x) for x in self.db['__offset__'].split(';')]
        self.start_fragment, self.act_fragment = [
            int(x) for x in self.db['__fragment__'].split(';')]
        self.length = int(self.db['__length__'])
    
    def sync(self):
        for chain, chainobj in self.updates.iteritems():
            dbchain = self.db.get(chain)
            if dbchain:
                chainobj.merge(dbchain)
            else:
                self.length += 1
            self.db[chain] = chainobj.pack()
        self.updates.clear()
        if self.start_updates:
            self.start_offset += len(self.start_updates)
            self.start_fragment = self.update_fragment('__start%d__', 
                self.start_fragment, ''.join(self.start_updates))
            self.start_updates = []
        if self.act_updates:
            self.actions += len(self.act_updates)
            self.act_fragment = self.update_fragment('__act%d__', 
                self.act_fragment, ''.join(self.act_updates))
            self.act_updates = []
        self.db['__offset__'] = '%d;%d' % (self.start_offset, self.actions)
        self.db['__fragment__'] = '%d;%d' % (
            self.start_fragment, self.act_fragment)
        self.db['__length__'] = str(self.length)
        self.db.sync()
    
    def update_fragment(self, fmt, which, value):
        existing = self.db[fmt % which] + value
        while existing:
            self.db[fmt % which], existing = (
                existing[:FRAGMENT], existing[FRAGMENT:])
            if existing:
                which += 1
        return which
    
    def append_chain(self, chain, val):
        self.updates[chain].append(val)
    
    def _random_beginning(self):
        start_choice = random.randrange(self.start_offset + self.actions)
        action = False
        if start_choice < self.start_offset:
            start_choice *= ORDER
            which, offset = divmod(start_choice, FRAGMENT)
            which = '__start%d__' % which
        else:
            start_choice = (start_choice - self.start_offset) * ORDER
            which, offset = divmod(start_choice, FRAGMENT)
            which = '__act%d__' % which
            action = True
        result = self.db[which][offset:offset + ORDER]
        return result, action
    
    def random_beginning(self):
        if not self:
            raise ValueError('No beginnings in database')
        while True:
            result, action = self._random_beginning()
            if result in self.db:
                return result, action
    
    def __getitem__(self, key):
        return self.db[key]
    
    def __contains__(self, key):
        return key in self.db
    
    def get(self, key, default=None):
        return self.db.get(key, default)
    
    def __nonzero__(self):
        return self.start_offset + self.actions > 0
    
    def __len__(self):
        return self.length

class _RedentFilter(Filter):
    def filter(self, lexer, stream):
        indent = 0
        cruft_stack = []
        eat_whitespace = False
        for ttype, value in stream:
            if eat_whitespace:
                if ttype is Token.Text and value.isspace():
                    continue
                elif ttype is Token.Punctuation and value == ';':
                    indent -= 1
                    continue
                else:
                    yield Token.Text, '    ' * indent
                    eat_whitespace = False
            if ttype is Token.Punctuation:
                if value == '{':
                    cruft_stack.append('brace')
                elif value == '}':
                    assert cruft_stack.pop() == 'brace'
                elif value == ':':
                    if cruft_stack and cruft_stack[-1] == 'lambda':
                        cruft_stack.pop()
                    elif not cruft_stack:
                        indent += 1
                        yield ttype, value
                        yield Token.Text, '\n'
                        eat_whitespace = True
                        continue
                elif value == ';':
                    yield Token.Text, '\n'
                    eat_whitespace = True
                    continue
            elif ttype is Token.Keyword and value == 'lambda':
                cruft_stack.append('lambda')
            yield ttype, value

def redent(s):
    lexer = PythonLexer()
    lexer.add_filter(_RedentFilter())
    return highlight(s, lexer, NullFormatter())

class NoRedirectHTTPPageGetter(client.HTTPPageGetter):
    handleStatus_301 = handleStatus_302 = handleStatus_302 = lambda self: None

class MarginallyImprovedHTTPClientFactory(client.HTTPClientFactory):
    protocol = NoRedirectHTTPPageGetter
    
    def page(self, page):
        if self.waiting:
            self.waiting = 0
            self.deferred.callback((page, self))

def get_page(url, *a, **kw):
    scheme, host, port, path = client._parse(url)
    factory = MarginallyImprovedHTTPClientFactory(url, *a, **kw)
    reactor.connectTCP(host, port, factory)
    return factory.deferred

def gen_shuffle(iter_obj):
    sample = range(len(iter_obj))
    while sample:
        n = sample.pop(random.randrange(len(sample)))
        yield iter_obj[n]

class Infobat(irc.IRCClient):
    nickname = conf.get('irc', 'nickname')
    max_countdown = conf.getint('database', 'sync_time')
    wordcount = chaincount = 0
    identified = False
    db = None
    
    sourceURL = 'https://code.launchpad.net/~pound-python/infobat/infobob'
    versionName = 'infobat-infobob'
    versionNum = 'latest'
    versionEnv = 'twisted'
    
    def signedOn(self):
        self.lol_timeouts = {}
        self.lol_offenses = {}
        nickserv_pw = conf.get('irc', 'nickserv_pw')
        if nickserv_pw:
            self.msg('NickServ', 'identify %s' % nickserv_pw)
        self._load_database()
        self.looper = task.LoopingCall(self._sync_countdown)
        self.countdown = self.max_countdown
        self.looper.start(30)
    
    def _load_database(self):
        self.db = Database(conf.get('database', 'db_file'))
    
    def _sync_countdown(self):
        if self.db is None: 
            return
        self.countdown -= 1
        if self.countdown == 0:
            self.db.sync()
            self.countdown = self.max_countdown
    
    def learn(self, string, action=False):
        if self.db is None: 
            return
        if string.startswith('*'):
            action = True
            string = string.lstrip('*')
        queue, length = '', 0
        for w in string:
            if w > '\x7f':
                continue
            self.wordcount += 1
            if not queue and w == ' ': 
                continue
            if len(queue) == ORDER:
                self.db.append_chain(queue, w)
            if w[-1] in '.!?':
                queue, length, action = '', 0, False
            else:
                queue += w
                length += 1
                if length == ORDER:
                    if action:
                        self.db.act_updates.append(queue)
                    else:
                        self.db.start_updates.append(queue)
                queue = queue[-ORDER:]
    
    def msg(self, target, message):
        # Prevent excess flood.
        irc.IRCClient.msg(self, target, message[:512])
    
    def irc_INVITE(self, prefix, params):
        self.invited(params[1], prefix)
    
    def invited(self, channel, inviter):
        self.join(channel)
    
    def kickedFrom(self, channel, kicker, message):
        self.join(channel)
    
    def action(self, user, channel, message):
        if not user: 
            return
        if channel != self.nickname:
            self.learn(message, True)
    
    def privmsg(self, user, channel, message):
        if not user: return
        user = user.split('!', 1)[0]
        if (not self.identified and user.lower() == 'nickserv' and 
                'identified' in message):
            self.identified = True
            self.join(conf.get('irc', 'channels'))
        if user.lower() in ('nickserv', 'chanserv', 'memoserv'): return
        if channel == self.nickname:
            log.msg('privmsg from %s: %s' % (user, message))
            target = user
        else:
            target = channel
       
        if channel == '#python':
            if message.startswith('!') and message.lstrip('!'):
                self.notice(user, 'no triggers in %s.' % channel)
            elif _lol_regex.search(message):
                if self.lol_timeouts.get(user, 0) < time.time():
                    self.lol_timeouts[user] = time.time() + 120
                    self.lol_offenses[user] = 0
                self.lol_offenses[user] += 1
                message_idx = min(
                    self.lol_offenses[user] - 1, len(_lol_messages))
                self.notice(user, _lol_messages[message_idx])
            else:
                m = _bad_pastebin_regex.search(message)
                if m:
                    base, _, _, which_bin, _, paste_id = m.groups()
                    full_url = m.group(0)
                    self.repaste(
                        target, user, base, which_bin, paste_id, full_url)
        
        if channel != self.nickname:
            self.learn(message)
        
        m = re.match(
            r'^s*%s\s*[,:> ]+(\S?.*?)[.!?]?\s*$' % self.nickname, message, re.I)
        if m:
            command, = m.groups()
        elif channel == self.nickname:
            command = message
        else:
            return
        s_command = command.split(' ')
        command_func = getattr(self, 'infobat_' + s_command[0], None)
        if command_func is not None:
            command_func(target, *s_command[1:])
        elif self.db and (channel in (
                self.nickname, '#python-offtopic', '#infobob')):
            result, action = self.db.random_beginning()
            search, result = result, list(result)
            while 1:
                chain = self.db.get(search)
                if chain is None:
                    break
                else:
                    chain = Chain(chain)
                self.chaincount += 1
                while True:
                    next = chain.choice()
                    if next in punctuation and random.randrange(ORDER) == 0:
                        continue
                    break
                if len(result) + len(next) > 255:
                    break
                result.append(next)
                search = ''.join(result[-ORDER:])
            result = ''.join(result)
            if action:
                self.me(target, result)
            else:
                if channel != self.nickname:
                    result = user + ', ' + result
                self.msg(target, result)
    
    @defer.inlineCallbacks
    def repaste(self, target, user, base, which_bin, paste_id, full_url):
        self.notice(user, 'in the future, please use a less awful pastebin '
            '(e.g. paste.pocoo.org) instead of %s.' % which_bin)
        if which_bin == 'etherpad.com':
            data, _ = yield get_page((
                'http://etherpad.com/ep/pad/export/%s/latest?format=txt'
            ) % paste_id)
        else:
            page, _ = yield get_page(full_url)
            tree = html.document_fromstring(page)
            textareas = tree.xpath(
                '//textarea[@name="%s"]' % _pastebin_textareas[which_bin])
            data = textareas[0].text
        
        try:
            new_paste_id = yield self.factory.paste_proxy.callRemote(
                'pastes.newPaste', 'python', data)
        except:
            self.msg(target, 'Error: %r' % sys.exc_info()[1])
            raise
        else:
            self.msg(target, 
                'http://paste.pocoo.org/show/%s/ (repasted for %s)' % (
                    new_paste_id, user))
    
    @defer.inlineCallbacks
    def infobat_redent(self, target, paste_target, *text):
        redented = (
            redent(' '.join(text).decode('utf8', 'replace')).encode('utf8'))
        try:
            paste_id = yield self.factory.paste_proxy.callRemote(
                'pastes.newPaste', 'python', redented)
        except:
            self.msg(target, 'Error: %r' % sys.exc_info()[1])
            raise
        else:
            self.msg(target, '%s, http://paste.pocoo.org/show/%s/' % (
                paste_target, paste_id))
    
    @defer.inlineCallbacks
    def infobat_codepad(self, target, paste_target, *text):
        redented = (
            redent(' '.join(text).decode('utf8', 'replace')).encode('utf8'))
        post_data = urlencode(dict(
            code=redented, lang='Python', submit='Submit', run='True'))
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        try:
            _, fac = yield get_page('http://codepad.org/', 
                method='POST', postdata=post_data, headers=headers)
        except:
            self.msg(target, 'Error: %r' % sys.exc_info()[1])
            raise
        else:
            paste_url = urljoin(
                'http://codepad.org/', fac.response_headers['location'][0])
            self.msg(target, '%s, %s' % (paste_target, paste_url))
    
    def infobat_sync(self, target):
        if self.db is not None: 
            self.db.sync()
            self.msg(target, 'Done.')
        else:
            self.msg(target, 'Database not loaded.')
    
    def infobat_unlock(self, target):
        if self.db is not None:
            self.looper.stop()
            self.db.sync()
            self.db.close()
            self.db = None
            self.msg(target, 'Database unlocked.')
        else:
            self.msg(target, 'Database was unlocked.')
    
    def infobat_lock(self, target):
        if self.db is None:
            self._load_database()
            self.looper.start(30)
            self.msg(target, 'Database locked.')
        else:
            self.msg(target, 'Database was unlocked.')
    
    def infobat_stats(self, target):
        if self.db is None: return
        delta = datetime.now() - start
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
                timestr, self.wordcount, self.chaincount, len(self.db),
                self.db.start_offset + self.db.actions, self.db.actions
            )
        self.msg(target, result)
    
    def infobat_divine(self, target, *seed):
        self.me(target, 'shakes the psychic black sphere.')
        r = random.Random(''.join(seed) + datetime.now().isoformat())
        st = open('magic8.txt')
        l = st.readlines()
        st.close()
        l = r.choice(l).strip()
        self.msg(target, 'It says: "%s"' % l)
    
    def infobat_probability(self, target, *sentence):
        if self.db is None: return
        sentence = ' '.join(sentence)
        if len(sentence) < ORDER:
            return
        start_count = 0
        search = sentence[:ORDER]
        for which in xrange(self.db.start_fragment + 1):
            chain = self.db['__start%d__' % which]
            idx = -1
            while True:
                idx = chain.find(search, idx + 1)
                if idx == -1:
                    break
                elif idx % ORDER == 0:
                    start_count += 1
        probabilities = [float(start_count) / self.db.start_offset]
        for start in xrange(len(sentence) - ORDER):
            chunk = sentence[start:start + ORDER]
            next = sentence[start + ORDER]
            chain = self.db.get(chunk)
            if chain:
                chain = Chain(chain)
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

class InfobatFactory(protocol.ReconnectingClientFactory):
    def __init__(self):
        self.dbpool = adbapi.ConnectionPool(
            'sqlite3', conf.get('sqlite', 'db_file'))
        self.paste_proxy = xmlrpc.Proxy('http://paste.pocoo.org/xmlrpc/')
    
    protocol = Infobat

ircFactory = InfobatFactory()
ircService = internet.TCPClient(
    conf.get('irc', 'server'), conf.getint('irc', 'port'), ircFactory, 20, None)

application = service.Application('infobat')
ircService.setServiceParent(application)

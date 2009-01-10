#!/usr/bin/twistd -y
from __future__ import with_statement
import re, random, collections, operator, urllib, struct
import bsddb3
from datetime import datetime
from twisted.web import xmlrpc
from twisted.words.protocols import irc
from twisted.internet import reactor, protocol, task
from twisted.protocols import policies
from twisted.application import internet, service
ORDER = 5
FRAGMENT = ORDER * 16384
punctuation = set(' .!?')
_words_regex = re.compile(r"(^\*)?[a-zA-Z',.!?\-:; ]")
_paste_regex = re.compile(r"URL: (http://[a-zA-Z0-9/_.-]+)")
start = datetime.now()

import ConfigParser
conf = ConfigParser.ConfigParser(dict(port='6667', channels='#infobat'))
conf.read(['infobat.cfg'])

_chain_struct = struct.Struct('>128H')
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
        self.db = bsddb3.hashopen(filename, 'c')
        if '__offset__' not in self.db:
            self.db['__offset__'] = '0;0'
            self.db['__fragment__'] = '0;0'
            self.db['__start0__'] = ''
            self.db['__act0__'] = ''
            self.db.sync()
        self.start_offset, self.actions = [
            int(x) for x in self.db['__offset__'].split(';')]
        self.start_fragment, self.act_fragment = [
            int(x) for x in self.db['__fragment__'].split(';')]
    
    def sync(self):
        for chain, chainobj in self.updates.iteritems():
            dbchain = self.db.get(chain)
            if dbchain:
                chainobj.merge(dbchain)
            self.db[chain] = chainobj.pack()
        self.updates.clear()
        if self.start_update:
            self.start_offset += len(self.start_update)
            self.start_fragment = self.update_fragment('__start%d__', 
                self.start_fragment, ''.join(self.start_update))
            self.start_update = []
        if self.act_update:
            self.actions += len(self.act_update)
            self.act_fragment = self.update_fragment('__act%d__', 
                self.act_fragment, ''.join(self.act_update))
            self.act_update = []
        self.db['__offset__'] = '%d;%d' % (self.start_offset, self.actions)
        self.db['__fragment__'] = '%d;%d' % (
            self.start_fragment, self.act_fragment)
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
        self._updates[chain].append(val)
    
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

_redent_pattern = re.compile('([^:;]*)([:;]|$)')
_valid_first_words = set(
    'if elif else for while try except finally class def with'.split())
def redent(s):
    ret, indent = [['']], 0
    for tok in _redent_pattern.finditer(s):
        line, end = [st.strip() for st in tok.groups()]
        first_word = line.partition(' ')[0]
        if line:
            if end == ':':
                line += ':'
            ret[-1].append(line)
            if end == ':' and first_word in _valid_first_words:
                indent += 1
            if end != ':' or first_word in _valid_first_words:
                ret.append(['    ' * indent])
        else:
            indent -= 1
            ret[-1][0] = '    ' * indent
    return '\n'.join(''.join(line) for line in ret)

def gen_shuffle(iter_obj):
    sample = range(len(iter_obj))
    while sample:
        n = sample.pop(random.randrange(len(sample)))
        yield iter_obj[n]

svnURL = '$HeadURL$'.split()[1]
svnRevision = 'r' + '$Revision$'.split()[1]

class Infobat(irc.IRCClient):
    nickname = conf.get('irc', 'nickname')
    max_countdown = conf.getint('database', 'sync_time')
    wordcount = chaincount = 0
    identified = False
    db = None
    
    sourceURL = svnURL
    versionName = 'infobat'
    versionNum = svnRevision
    versionEnv = 'twisted'
    
    def signedOn(self):
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
        for w in _words_regex.finditer(string):
            self.wordcount += 1
            w = w.group().lower()
            if not queue and w == ' ': continue
            if len(queue) == ORDER:
                self.db.append_chain(queue, w)
            if w[-1] in '.!?':
                queue, length, action = '', 0, False
            else:
                queue += w
                length += 1
                if length == ORDER:
                    if action:
                        self.db.act_update.append(queue)
                    else:
                        self.db.start_update.append(queue)
                queue = queue[-ORDER:]
    
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
        target = (user if channel == self.nickname else channel)
       
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
        elif self.db:
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
    
    def infobat_redent(self, target, paste_target, *text):
        redented = redent(' '.join(text))
        proxy = xmlrpc.Proxy('http://paste.pocoo.org/xmlrpc/')
        d = proxy.callRemote('pastes.newPaste', 'python', redented)
        d.addCallbacks(self._redent_success, self._redent_failure,
            callbackArgs=(target, paste_target), errbackArgs=(target,))
    
    def _redent_success(self, id, target, paste_target):
        self.msg(target, '%s, http://paste.pocoo.org/show/%s/' % (
            paste_target, id))
    def _redent_failure(self, failure, target):
        self.msg(target, 'Error: %s' % failure.getErrorMessage())
        return failure
    
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
                self.start_offset + self.actions, self.actions
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
            idx = 0
            while True:
                idx = chain.find(search, idx)
                if idx == -1:
                    break
                elif idx % ORDER == 0:
                    start_count += 1
        probabilities = [float(start_count) / self.db.start_offset]
        for start in xrange(1, len(sentence) - ORDER - 1):
            chunk = sentence[start:start + ORDER]
            next = sentence[start + ORDER]
            chain = self.db.get(chunk)
            if chain:
                chain = Chain(chain)
                probabilities.append(float(chain[next]) / sum(chain.data))
            else:
                probabilities.append(0)
        probabilities = [100 * prob for prob in probabilities]
        tot_probability = reduce(operator.mul, probabilities)
        self.msg(target, 
            '%0.6f%% chance across %d probabilities; '
            '%0.6f%% average, %0.6ff%% highest.' % (
                tot_probability, len(probabilities), 
                float(sum(probabilities)) / len(probabilities), 
                max(probabilities)))

class InfobatFactory(protocol.ReconnectingClientFactory):
    protocol = Infobat

ircFactory = InfobatFactory()
ircService = internet.TCPClient(
    conf.get('irc', 'server'), conf.getint('irc', 'port'), ircFactory, 20, None)

application = service.Application('infobat')
ircService.setServiceParent(application)

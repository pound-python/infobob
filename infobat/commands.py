from twisted.internet import reactor, protocol, task, defer
from twisted.enterprise import adbapi
from twisted.protocols import amp
from twisted.python import log, reflect
from twisted.web import xmlrpc
from infobat.redent import redent
from infobat.config import conf
from infobat import amp, chains, database, http
from datetime import datetime
from lxml import html, etree
from urllib import urlencode
from urlparse import urljoin
import operator
import random
import time
import sys
import re

_lol_regex = re.compile(r'\b([lo]{3,}|rofl+|lmao+)z*\b', re.I)
_lol_messages = [
    '#python is a no-LOL zone.', 
    'i mean it: no LOL in #python.', 
    'seriously, dude, no LOL in #python.']
_bad_pastebin_regex = re.compile(
    r'((https?://)?([a-z0-9-]+\.)*(pastebin\.(com|org|ca)|etherpad\.com)/)'
    r'([a-z0-9]+)/?', re.I)
_textarea_xpath = etree.XPath('//textarea[@name=$name][1]/text()')
_pastebin_textareas = {
    'pastebin.ca': 'content',
    'pastebin.com': 'code2',
    'pastebin.org': 'code2'}

class InfobatChild(amp.InfobatChildBase):
    db = dbpool = None
    
    def __init__(self):
        amp.InfobatChildBase.__init__(self)
        self.dbpool = database.InfobatDatabaseRunner()
        self.paste_proxy = xmlrpc.Proxy('http://paste.pocoo.org/xmlrpc/')
        self._load_database()
        self.looper = task.LoopingCall(self._sync_countdown)
        self.countdown = self.max_countdown
        self.looper.start(30)
    
    def connectionLost(self, reason):
        if self.db:
            self.db.sync()
        if self.dbpool:
            self.dbpool.close()
        amp.InfobatChildBase.connectionLost(self, reason)
    
    def _load_database(self):
        self.db = chains.Database(conf.get('database', 'db_file'))
    
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
        self.db.learn(string, action)
    
    def action(self, user, channel, message):
        if not user: 
            return
        if channel != self.nickname:
            self.learn(message, True)
    
    def privmsg(self, user, channel, message):
        if not user: return
        user = user.split('!', 1)[0]
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
                self.do_lol(user)
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
            action, result = self.db.splice()
            if action:
                self.me(target, result)
            else:
                if channel != self.nickname:
                    result = user + ', ' + result
                self.msg(target, result)
    
    @defer.inlineCallbacks
    def do_lol(self, nick):
        offenses = yield self.dbpool.add_lol(nick)
        message_idx = min(offenses, len(_lol_messages)) - 1
        self.notice(nick, _lol_messages[message_idx])
    
    @defer.inlineCallbacks
    def repaste(self, target, user, base, which_bin, paste_id, full_url):
        self.notice(user, 'in the future, please use a less awful pastebin '
            '(e.g. paste.pocoo.org) instead of %s.' % which_bin)
        if which_bin == 'etherpad.com':
            data, _ = yield http.get_page((
                'http://etherpad.com/ep/pad/export/%s/latest?format=txt'
            ) % paste_id)
        else:
            page, _ = yield http.get_page(full_url)
            tree = html.document_fromstring(page)
            data, = _textarea_xpath(tree, name=_pastebin_textareas[which_bin])
        new_paste_id = yield self.paste_proxy.callRemote(
            'pastes.newPaste', 'python', data)
        self.msg(target, 
            'http://paste.pocoo.org/show/%s/ (repasted for %s)' % (
                new_paste_id, user))
    
    @defer.inlineCallbacks
    def infobat_redent(self, target, paste_target, *text):
        redented = (
            redent(' '.join(text).decode('utf8', 'replace')).encode('utf8'))
        try:
            paste_id = yield self.paste_proxy.callRemote(
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
            _, fac = yield http.get_page('http://codepad.org/', 
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
        delta = datetime.now() - self.parent_start
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
        self.callRemote(amp.ShutdownRequest, requester=target)

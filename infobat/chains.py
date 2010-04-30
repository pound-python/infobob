import collections
import random
import struct
import bsddb
ORDER = 8
FRAGMENT = ORDER * 16384
punctuation = set(' .!?')

def forceUnicode(bytestring):
    try:
        obj = bytestring.decode('utf-8')
    except UnicodeDecodeError:
        obj = bytestring.decode('latin1')
    return obj

_chain_struct = struct.Struct('>256I')
class Chain(object):
    def __init__(self, data=None):
        if data is None:
            self.data = [0] * 256
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
        self.wordcount = self.chaincount = 0
    
    def close(self):
        self.db.close()
    
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
    
    def learn(self, string, action=False):
        if string.startswith('*'):
            action = True
            string = string.lstrip('*')
        string = forceUnicode(string).encode('utf-8')
        queue, length = '', 0
        def _finalize():
            self.append_chain(queue, '\0')
        for w in string:
            if w == '\0':
                continue
            self.wordcount += 1
            if w.isspace():
                if not queue:
                    continue
                elif queue[-1] in '.!?':
                    _finalize()
                    queue, length, action = '', 0, False
                    continue
            if len(queue) == ORDER:
                self.append_chain(queue, w)
            queue += w
            length += 1
            if length == ORDER:
                if action:
                    self.act_updates.append(queue)
                else:
                    self.start_updates.append(queue)
            queue = queue[-ORDER:]
        _finalize()
    
    def _splice(self):
        result, action = self.random_beginning()
        search, result = result, list(result)
        if action:
            yield '*'
        while True:
            chain = self.get(search)
            if chain is None:
                break
            else:
                chain = Chain(chain)
            self.chaincount += 1
            while True:
                next = chain.choice()
                if next == '\0':
                    return
                elif next in punctuation and random.randrange(ORDER) == 0:
                    continue
                break
            if len(result) + len(next) > 255:
                break
            yield next
            result.append(next)
            search = ''.join(result[-ORDER:])
    
    def splice(self):
        action = False
        result = ''.join(self._splice())
        if result.startswith('*'):
            action = True
            result = result.lstrip('*')
        return action, result
    
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

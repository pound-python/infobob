try:
    import json
except ImportError:
    import simplejson as json # json not available, trying simplejson

_channel_defaults = dict(
    have_ops=False,
    commands=(),
    flood_control=None,
    key=None,
)

class Channel(object):
    def __init__(self, name, attrs):
        self.name = name
        self.command_usable = {}
        self.default_usable = False
        self.update(attrs)
    
    def update(self, attrs):
        for kv in attrs.iteritems():
            setattr(self, *kv)
    
    def _commands_set(self, values):
        for value in values:
            will_set = {'allow': True, 'deny': False}[value[0]]
            params = value[1:]
            if params == ['all']:
                self.default_usable = will_set
                self.command_usable.clear()
                continue
            for command in params:
                self.command_usable[command] = will_set
    
    commands = property(None, _commands_set)
    
    def is_usable(self, command):
        return self.command_usable.get(command, self.default_usable)

class _Config(object):
    def __init__(self):
        self.config = {}
        self.channels = {}

    def load(self, fobj):
        self.config.update(json.load(fobj))
        self.apply_defaults()

    def apply_defaults(self):
        self.setdefault('irc.port', 6667)
        self.setdefault('misc.magic8_file', None)
        self.setdefault('misc.manhole.socket_prefix', None)
        self.setdefault('misc.manhole.passwd_file', None)
        self.setdefault('channels.defaults', {})

    def __getitem__(self, item):
        obj = self.config
        for part in item.split('.'):
            obj = obj[part]
        return obj

    def __setitem__(self, item, value):
        section = self.config
        item = item.split('.')
        for part in item[:-1]:
            section = section.setdefault(part, {})
        section[item[-1]] = value

    def setdefault(self, item, value):
        section = self.config
        item = item.split('.')
        for part in item[:-1]:
            section = section.setdefault(part, {})
        return section.setdefault(item[-1], value)

    def channel(self, name):
        ret = self.channels.get(name)
        if ret is not None:
            return ret
        ret = self.channels[name] = Channel(name, _channel_defaults)
        channels = self['channels']
        ret.update(channels.get('defaults', {}))
        ret.update(channels.get(name, {}))
        return ret

    def __repr__(self):
        return '_Config(%r)' % (self.config,)

# global config object
conf = _Config()

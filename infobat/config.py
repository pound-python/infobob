try:
    import json
except ImportError:
    import simplejson as json # json not available, trying simplejson

class _Config(object):
    def __init__(self):
        self.config = {}
        self.config_loc = None

    def load(self, fobj):
        self.config.update(json.load(fobj))
        self.apply_defaults()

    def apply_defaults(self):
        self.setdefault('irc.port', 6667)

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
        
    def __repr__(self):
        return '_Config(%r)' % (self.config,)

# global config object
conf = _Config()

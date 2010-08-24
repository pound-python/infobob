import warnings
import gettext
import os

try:
    import json
except ImportError:
    import simplejson as json # json not available, trying simplejson

_channel_defaults = dict(
    have_ops=False,
    commands=(),
    flood_control=None,
    key=None,
    default_ban_time=28800,
)

class Channel(object):
    def __init__(self, name, attrs, conf=None):
        self._conf = conf
        self.name = name
        self.command_usable = {}
        self.default_usable = False
        self.lang = conf['misc.locale.default_lang']
        self.encoding = conf['misc.locale.default_encoding']
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

    def translate(self, message):
        return self._conf.translate(message, lang=self.lang,
            encoding=self.encoding)

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
        self.setdefault('misc.locale.dir',
            os.path.join(os.path.dirname(__file__), 'locale'))
        self.setdefault('misc.locale.default_lang', 'en')
        self.setdefault('misc.locale.default_encoding', 'utf-8')

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
        ret = self.channels[name] = Channel(name, _channel_defaults, conf=self)
        channels = self['channels']
        ret.update(channels.get('defaults', {}))
        ret.update(channels.get(name, {}))
        return ret

    def getTranslator(self, lang=None, encoding=None):
        langs = []
        if lang is not None:
            langs.append(lang)
        langs.append(self['misc.locale.default_lang'])
        if encoding is None:
            encoding = self['misc.locale.default_encoding']
        try:
            t = gettext.translation('infobat', self['misc.locale.dir'],
                languages=langs)
        except IOError:
            warnings.warn('Translation not found for %r' % (lang,))
            t = gettext.NullTranslations()
        return t

    def translate(self, message, lang=None, encoding=None):
        t = self.getTranslator(lang=lang)
        return t.ugettext(message).encode(encoding)
    
    def __repr__(self):
        return '_Config(%r)' % (self.config,)

# global config object
conf = _Config()

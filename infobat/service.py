from zope.interface import implements
from twisted.plugin import IPlugin
from twisted.python import usage
from twisted.application import internet, service
from twisted.application.service import IServiceMaker
from infobat.config import conf
from infobat import irc

class InfobatOptions(usage.Options):
    def parseArgs(self, *args):
        if len(args) == 1:
            self.config, = args
        else:
            self.opt_help()
    
    def getSynopsis(self):
        return 'Usage: twistd [options] infobat <config file>'

class InfobatServiceMaker(object):
    implements(IServiceMaker, IPlugin)
    tapname = "infobat"
    description = "An irc bot!"
    options = InfobatOptions
    
    def makeService(self, options):
        conf.read([options.config])
        conf.config_loc = options.config
        ircFactory = irc.InfobatFactory()
        ircService = internet.TCPClient(
            conf.get('irc', 'server'), conf.getint('irc', 'port'), 
            ircFactory, 20, None)
        return ircService

from zope.interface import implements
from twisted.plugin import IPlugin
from twisted.python import usage
from twisted.application import internet, service
from twisted.application.service import IServiceMaker
from infobat.config import conf
from infobat import irc

class InfobatServiceMaker(object):
    implements(IServiceMaker, IPlugin)
    tapname = "infobat"
    description = "An irc bot!"
    options = usage.Options
    
    def makeService(self, options):
        ircFactory = irc.InfobatFactory()
        ircService = internet.TCPClient(
            conf.get('irc', 'server'), conf.getint('irc', 'port'), 
            ircFactory, 20, None)
        return ircService

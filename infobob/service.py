from __future__ import with_statement
from functools import partial
from zope.interface import implements
from twisted.internet.ssl import ClientContextFactory
from twisted.plugin import IPlugin
from twisted.python import usage
from twisted.application import internet, service
from twisted.application.service import IServiceMaker
from infobob.config import InfobobConfig
from infobob import irc, database, http

class InfobobOptions(usage.Options):
    def parseArgs(self, *args):
        if len(args) == 1:
            self.config, = args
        else:
            self.opt_help()

    def getSynopsis(self):
        return 'Usage: twistd [options] infobob <config file>'

class InfobobServiceMaker(object):
    implements(IServiceMaker, IPlugin)
    tapname = "infobob"
    description = "An irc bot!"
    options = InfobobOptions

    def makeService(self, options):
        multiService = service.MultiService()

        conf = InfobobConfig()
        with open(options.config) as cfgFile:
            conf.load(cfgFile)
        conf.config_loc = options.config
        self.ircFactory = irc.InfobobFactory(conf)
        clientService = internet.TCPClient
        if conf['irc.ssl']:
            clientService = partial(internet.SSLClient,
                                    contextFactory=ClientContextFactory())
        self.ircService = clientService(
            conf['irc.server'], conf['irc.port'], self.ircFactory)
        self.ircService.setServiceParent(multiService)

        conf.dbpool = database.InfobobDatabaseRunner(conf)

        if (conf['misc.manhole.socket'] is not None
                and conf['misc.manhole.passwd_file']):
            from twisted.conch.manhole_tap import makeService
            self.manholeService = makeService(dict(
                telnetPort="unix:" + conf['misc.manhole.socket'].encode(),
                sshPort=None,
                namespace={'self': self, 'conf': conf},
                passwd=conf['misc.manhole.passwd_file'],
            ))
            self.manholeService.setServiceParent(multiService)

        self.webService = internet.TCPServer(
            conf['web.port'],
            http.makeSite(http.DEFAULT_TEMPLATES_DIR, conf.dbpool))
        self.webService.setServiceParent(multiService)

        return multiService

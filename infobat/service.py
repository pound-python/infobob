from __future__ import with_statement
from functools import partial
from zope.interface import implements
from twisted.internet.ssl import ClientContextFactory
from twisted.plugin import IPlugin
from twisted.python import usage
from twisted.application import internet, service
from twisted.application.service import IServiceMaker
from infobat.config import conf
from infobat import irc, database, http

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
        multiService = service.MultiService()

        with open(options.config) as cfgFile:
            conf.load(cfgFile)
        conf.config_loc = options.config
        self.ircFactory = irc.InfobatFactory()
        clientService = internet.TCPClient
        if conf['irc.ssl']:
            clientService = partial(internet.SSLClient,
                                    contextFactory=ClientContextFactory())
        self.ircService = clientService(
            conf['irc.server'], conf['irc.port'], self.ircFactory)
        self.ircService.setServiceParent(multiService)

        conf.dbpool = database.InfobatDatabaseRunner()

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
            http.makeSite(conf.dbpool),
            interface='127.0.0.1')
        self.webService.setServiceParent(multiService)

        return multiService

from __future__ import with_statement
from zope.interface import implements
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
        ircFactory = irc.InfobatFactory()
        ircService = internet.TCPClient(
            conf['irc.server'], conf['irc.port'],
            ircFactory, 20, None)
        ircService.setServiceParent(multiService)

        conf.dbpool = database.InfobatDatabaseRunner()

        if (conf['misc.manhole.socket'] is not None
                and conf['misc.manhole.passwd_file']):
            from twisted.conch.manhole_tap import makeService
            manholeService = makeService(dict(
                telnetPort="unix:" + conf['misc.manhole.socket'].encode(),
                sshPort=None,
                namespace={'self': self, 'conf': conf},
                passwd=conf['misc.manhole.passwd_file'],
            ))
            manholeService.setServiceParent(multiService)

        webService = internet.TCPServer(
            conf['web.port'],
            http.makeSite(conf.dbpool),
            interface='127.0.0.1')
        webService.setServiceParent(multiService)

        return multiService

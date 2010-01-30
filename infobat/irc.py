from twisted.internet import reactor, protocol, task, defer
from twisted.words.protocols import irc
from twisted.enterprise import adbapi
from twisted.web import xmlrpc
from ampoule.pool import ProcessPool
from infobat.config import conf
from infobat import amp, commands, chains
from datetime import datetime
import os

class Infobat(irc.IRCClient):
    identified = False
    pool = None
    outstandingPings = 0
    
    sourceURL = 'https://code.launchpad.net/~pound-python/infobat/infobob'
    versionName = 'infobat-infobob'
    versionNum = 'latest'
    versionEnv = 'twisted'
    
    def __init__(self):
        self.nickname = conf.get('irc', 'nickname')
    
    def signedOn(self):
        self.factory.resetDelay()
        self.pool = ProcessPool(
            starter=amp.InfobatChildStarter(
                irc=self, 
                env=dict(PYTHONPATH=os.environ.get('PYTHONPATH', '')),
                args=(
                    conf.config_loc, 
                    self.factory.start.strftime(amp.ISOFORMAT),
                ),
            ),
            ampChild=commands.InfobatChild, ampParent=amp.InfobatParent,
            min=1, max=1, maxIdle=3600, recycleAfter=None)
        self.pool.start()
        nickserv_pw = conf.get('irc', 'nickserv_pw')
        if nickserv_pw:
            self.msg('NickServ', 'identify %s' % nickserv_pw)
        else:
            self.join(conf.get('irc', 'channels'))
        self.ping_looper = task.LoopingCall(self.pingServer)
        self.ping_looper.start(60)
    
    def pingServer(self):
        if self.outstandingPings > 5:
            self.transport.loseConnection()
        self.sendLine('PING bollocks')
        self.outstandingPings += 1
    
    def irc_PONG(self, prefix, params):
        self.outstandingPings -= 1
    
    def msg(self, target, message):
        # Prevent excess flood.
        irc.IRCClient.msg(self, target, message[:512])
    
    def irc_INVITE(self, prefix, params):
        self.invited(params[1], prefix)
    
    def invited(self, channel, inviter):
        self.join(channel)
    
    def kickedFrom(self, channel, kicker, message):
        self.join(channel)
    
    def action(self, user, channel, message):
        if self.pool:
            self.pool.doWork(amp.ActionIn,
                user=user, channel=channel, message=message)
    
    def privmsg(self, user, channel, message):
        if (not self.identified and user.lower().startswith('nickserv!') and 
                'identified' in message):
            self.identified = True
            self.join(conf.get('irc', 'channels'))
        if self.pool:
            self.pool.doWork(amp.PrivmsgIn, 
                user=user, channel=channel, message=message)

class InfobatFactory(protocol.ReconnectingClientFactory):
    def __init__(self):
        self.start = datetime.now()
    
    protocol = Infobat

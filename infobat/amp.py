from twisted.internet import reactor, protocol, task, defer
from twisted.protocols import amp
from twisted.python import log, reflect
from ampoule.commands import Shutdown
from ampoule.main import ProcessStarter
from ampoule import child
from infobat.config import conf
from datetime import datetime
import sys

ISOFORMAT = '%Y-%m-%dT%H:%M:%S'

class NullableCommand(amp.Command):
    @classmethod
    def null_responder(cls, func):
        def wrap(*a, **kw):
            func(*a, **kw)
            return {}
        return cls.responder(wrap)

class MessageFromChannelCommand(NullableCommand):
    arguments = [
        ('user', amp.String()),
        ('channel', amp.String()),
        ('message', amp.String()),
    ]

class TargetedMessageCommand(NullableCommand):
    arguments = [
        ('target', amp.String()),
        ('message', amp.String()),
    ]

class ActionIn(MessageFromChannelCommand):
    pass

class ActionOut(TargetedMessageCommand):
    pass

class PrivmsgIn(MessageFromChannelCommand):
    pass

class PrivmsgOut(TargetedMessageCommand):
    pass

class NoticeOut(TargetedMessageCommand):
    pass

class ShutdownRequest(NullableCommand):
    arguments = [
        ('requester', amp.String()),
    ]

class InfobatParent(amp.AMP):
    def __init__(self, *a, **kw):
        self.irc = kw.pop('irc')
        amp.AMP.__init__(self, *a, **kw)
    
    @NoticeOut.null_responder
    def handle_notice(self, target, message):
        self.irc.notice(target, message)
    
    @PrivmsgOut.null_responder
    def handle_privmsg(self, target, message):
        self.irc.msg(target, message)
    
    @ActionOut.null_responder
    def handle_action(self, target, message):
        self.irc.me(target, message)
    
    @ShutdownRequest.null_responder
    def handle_shutdown_request(self, requester):
        self.irc.msg(requester, 'Okay!')
        self.callRemote(Shutdown)

class InfobatChildStarter(ProcessStarter):
    def __init__(self, *a, **kw):
        self.irc = kw.pop('irc')
        super(InfobatChildStarter, self).__init__(*a, **kw)
    
    def startAMPProcess(self, ampChild, ampParent):
        self._checkRoundTrip(ampChild)
        fullPath = reflect.qual(ampChild)
        prot = self.connectorFactory(ampParent(irc=self.irc))
        
        return self.startPythonProcess(prot, self.childReactor, fullPath)

class InfobatChildBase(child.AMPChild):
    def __init__(self):
        child.AMPChild.__init__(self)
        conf.read([sys.argv[1]])
        self.parent_start = datetime.strptime(sys.argv[2], ISOFORMAT)
        self.nickname = conf.get('irc', 'nickname')
        self.max_countdown = conf.getint('database', 'sync_time')
    
    @ActionIn.null_responder
    def _action(self, user, channel, message):
        self.action(user, channel, message)
    
    @PrivmsgIn.null_responder
    def privmsg(self, user, channel, message):
        self.privmsg(user, channel, message)
    
    def me(self, target, message):
        self.callRemote(ActionOut, target=target, message=message)
    
    def msg(self, target, message):
        self.callRemote(PrivmsgOut, target=target, message=message)
    
    def notice(self, target, message):
        self.callRemote(NoticeOut, target=target, message=message)

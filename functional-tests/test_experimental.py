import sys
import json
import io
import os
import tempfile
import sqlite3

from twisted.internet import defer
from twisted.internet import task
from twisted.internet import endpoints
from twisted import logger
from twisted.trial.unittest import TestCase as TrialTestCase
from twisted.words.protocols import irc
from twisted.words.protocols.irc import IRC as BaseIRCProtocol
from twisted.words import service as wsvc
from twisted.words import iwords
from twisted.cred.portal import Portal
from twisted.cred.checkers import InMemoryUsernamePasswordDatabaseDontUse
from twisted.cred.credentials import UsernamePassword
import zope.interface as zi
import attr

from infobob.irc import InfobobFactory
from infobob.config import InfobobConfig


SERVER_PORT = 6667


class ExperimentalFunctionalTestCase(TrialTestCase):
    server = None
    botconn = None
    tdir = None

    @defer.inlineCallbacks
    def setUp(self):
        from twisted.internet import reactor
        self.botnick = u'infobob'
        server = IRCTestServer()
        server.cred_db.addUser(botnick.encode('ascii'), b'botpass')
        self.server = server
        yield server.listen(
            reactor, 'tcp:{}:interface=127.0.0.1'.format(SERVER_PORT))

        self.tdir = tempfile.TemporaryDirectory()
        dbfile = os.path.join(self.tdir.name, 'db.sqlite')
        with sqlite3.connect(dbfile) as dbconn:
            with open(os.path.join(__file__, '../db.schema')) as schema:
                dbconn.executescript(schema.read())

        conf = InfobobConfig()
        conf.load(io.BytesIO(json.dumps({
            'irc': {
                'nickname': 'infobob',
                'password': 'botpass',
                'nickserv_pw': None,
                'autojoin': ['#project', '##offtopic'],
            },
        })))
        conf.dbpool = database.InfobobDatabaseRunner(conf)
        botfactory = InfobobFactory(conf)
        self.botconn = reactor.connectTCP(
            '127.0.0.1', SERVER_PORT, botfactory, timeout=2)

    def tearDown(self):
        if self.botconn is not None:
            self.botconn.disconnect()
        if self.tdir is not None:
            self.tdir.cleanup()
        return self.server.stop()


class IRCTestServer(object):
    def __init__(self):
        realm = wsvc.InMemoryWordsRealm(b'testserver')
        cred_db = InMemoryUsernamePasswordDatabaseDontUse()
        portal = Portal(realm, [cred_db])
        factory = wsvc.IRCFactory(realm, portal)
        self.realm = realm
        self.portal = Portal(realm, [cred_db])
        self.cred_db = cred_db
        self.factory = factory
        self.endpoint = None
        self.lport = None

    @defer.inlineCallbacks
    def listen(self, reactor, description):
        endpoint = endpoints.serverFromString(reactor, description)
        self.lport = yield endpoint.listen(self.factory)
        self.endpoint = endpoint

    def stop(self):
        return self.lport.stopListening()


@zi.implementer(iwords.IChatClient)
class DummyIRCUser(object):
    log = logger.Logger()

    def __init__(self, name):
        self.name = name

    def receive(self, sender, recipient, message):
        """
        Callback notifying this user of the given message sent by the
        given user.
        This will be invoked whenever another user sends a message to a
        group this user is participating in, or whenever another user sends
        a message directly to this user.  In the former case, C{recipient}
        will be the group to which the message was sent; in the latter, it
        will be the same object as the user who is receiving the message.
        @type sender: L{IUser}
        @type recipient: L{IUser} or L{IGroup}
        @type message: C{dict}
        @rtype: L{twisted.internet.defer.Deferred}
        @return: A Deferred which fires when the message has been delivered,
        or which fails in some way.  If the Deferred fails and the message
        was directed at a group, this user will be removed from that group.
        """
        self.log.debug(
            u'receive(sender={s!r}, recipient={r!r}, message={m!r})',
            s=sender, r=recipient, m=message,
        )
        return defer.succeed(None)

    def groupMetaUpdate(self, group, meta):
        """
        Callback notifying this user that the metadata for the given
        group has changed.
        @type group: L{IGroup}
        @type meta: C{dict}
        @rtype: L{twisted.internet.defer.Deferred}
        """
        self.log.debug(
            u'groupMetaUpdate(group={g!r}, meta={m!r})', g=group, m=meta,
        )
        return defer.succeed(None)

    def userJoined(self, group, user):
        """
        Callback notifying this user that the given user has joined
        the given group.
        @type group: L{IGroup}
        @type user: L{IUser}
        @rtype: L{twisted.internet.defer.Deferred}
        """
        self.log.debug(
            u'userJoined(group={g!r}, user={u!r})', g=group, u=user,
        )
        return defer.succeed(None)

    def userLeft(self, group, user, reason=None):
        """
        Callback notifying this user that the given user has left the
        given group for the given reason.
        @type group: L{IGroup}
        @type user: L{IUser}
        @type reason: C{unicode}
        @rtype: L{twisted.internet.defer.Deferred}
        """
        self.log.debug(
            u'userLeft(group={g!r}, user={u!r}, reason={r!r})',
            g=group, u=user, r=reason,
        )
        return defer.succeed(None)



@defer.inlineCallbacks
def main(reactor):
    logger.globalLogBeginner.beginLoggingTo([logger.textFileLogObserver(sys.stderr)])
    server = IRCTestServer()
    server.cred_db.addUser(b'user', b'password')
    server.cred_db.addUser(b'dummy', b'password')
    dummymind = DummyIRCUser(b'dummy')
    _, dummy, _ = yield server.portal.login(
        UsernamePassword(b'dummy', b'password'),
        dummymind,
        iwords.IUser,
    )
    testchan = yield server.realm.createGroup(u'test')
    yield dummy.join(testchan)
    yield server.listen(
        reactor, 'tcp:{}:interface=127.0.0.1'.format(SERVER_PORT))
    yield task.deferLater(reactor, 10, defer.succeed, None)
    # send a message to a channel
    yield server.realm.lookupGroup(u'test').addCallback(dummy.send, {'text': u'hello'})
    # send a query to a user
    yield (server.realm.lookupUser(u'user')
            .addCallback(lambda user: user.mind)
            .addCallback(dummy.send, {'text': u'*psst* hi'}))
    yield task.deferLater(reactor, 300, defer.succeed, None)


if __name__ == '__main__':
    task.react(main)

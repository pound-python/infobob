import json
import io

from twisted.internet import defer
from twisted.trial.unittest import TestCase as TrialTestCase
from twisted.test.proto_helpers import StringTransport

from infobob.irc import Infobob
from infobob.config import InfobobConfig



class InfobobProtocolTestCase(TrialTestCase):
    def setUp(self):
        self.proto = None
        self.factory = FakeInfobobFactory()
        self.transport = StringTransport()

    def initProto(self, configStructure, stubTimer=True):
        conf = InfobobConfig()
        conf.load(io.BytesIO(json.dumps(configStructure)))
        conf.dbpool = None
        self.proto = Infobob(conf)
        self.proto.factory = self.factory
        self.proto.makeConnection(self.transport)
        if stubTimer:
            # Stub out startTimer to avoid unclean reactor
            self.proto.startTimer = lambda *a, **kw: None

    def clearWritten(self):
        self.transport.clear()

    def assertWritten(self, expectedBytes):
        self.assertEqual(self.transport.value(), expectedBytes)
        self.clearWritten()

    def test_autojoin_with_nickserv_pw_waits_for_identified(self):
        self.initProto({
            'irc': {
                'nickname': 'testnick',
                'password': 'ircpass',
                'nickserv_pw': 'nickservpass',
                'autojoin': ['#project', '##offtopic'],
            },
        })

        p = self.proto
        p.connectionMade()
        self.clearWritten()

        p.signedOn()
        self.assertWritten(
            b'PRIVMSG NickServ :identify nickservpass\r\n',
        )

        p.noticed(
            b'NickServ!foo@host',
            b'testnick',
            b'You are now identified as "testnick"',
        )
        self.assertWritten(b'JOIN #project\r\nJOIN ##offtopic\r\n')
        self.assertIs(p.identified, True)

    def test_autojoin_no_nickserv_pw(self):
        self.initProto({
            'irc': {
                'nickname': 'testnick',
                'password': 'ircpass',
                'nickserv_pw': None,
                'autojoin': ['#project', '##offtopic'],
            },
        })

        p = self.proto
        p.connectionMade()
        self.clearWritten()

        p.signedOn()
        self.assertWritten(b'JOIN #project\r\nJOIN ##offtopic\r\n')
        self.assertIs(p.identified, False)


class FakeInfobobFactory:
    def resetDelay(self):
        pass

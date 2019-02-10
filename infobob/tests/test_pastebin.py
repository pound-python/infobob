import unittest

from twisted.trial.unittest import TestCase as TrialTestCase
import ddt

from infobob import pastebin


@ddt.ddt
class ExtractBadPasteSpecsTestCase(unittest.TestCase):
    def setUp(self):
        badPastebins = [
            pastebin.GenericBadPastebin(
                u'pastebin.com',
                [u'www.pastebin.com'],
                u'([a-z0-9]{4,12})',
                u'/raw/',
                None,
            ),
            pastebin.GenericBadPastebin(
                u'pastebin.ca',
                [u'www.pastebin.ca'],
                u'([0-9]{4,12})',
                u'/raw/',
                None,
            ),
            pastebin.GenericBadPastebin(
                u'hastebin.com',
                [u'www.hastebin.com'],
                u'([a-z0-9]{4,12})',
                u'/raw/',
                None,
            ),
        ]
        self.repaster = pastebin.BadPasteRepaster(
            None, None, badPastebins
        )

    def assertResults(self, message, expected):
        result = self.repaster.extractBadPasteSpecs(message)
        self.assertEqual(result, expected)

    @ddt.data(b'', b'http://www.google.com/939')
    def test_no_results(self, message):
        self.assertResults(message, [])

    @ddt.unpack
    @ddt.data(*[
        (sch + sub + rest, domain, pid)
        for sch in (b'https://', b'http://', b'')
        for sub in (b'www.', b'')
        for rest, domain, pid in [
            (b'pastebin.com/pwZA', u'pastebin.com', u'pwZA'),
            (b'pastebin.ca/123986', u'pastebin.ca', u'123986'),
            (b'hastebin.com/asdflkkfig', u'hastebin.com', u'asdflkkfig'),
        ]
    ])
    def test_scheme_optional(self, message, domain, pasteid):
        expected = pastebin.BadPaste(domain, pasteid)
        self.assertResults(message, [expected])

    @ddt.data(
        b'https://ww.pastebin.com/asdfasdf',
        b'https://wwww.pastebin.com/asdfasdf',
        b'https://ww.hastebin.com/asdfasdf',
        b'https://wwww.hastebin.com/asdfasdf',
    )
    def test_bogus_subdomain(self, message):
        self.assertResults(message, [])

    def test_multiple(self):
        self.assertResults(
            b' and '.join([
                b'https://pastebin.com/pwZA',
                b'http://pastebin.com/RwZA',
                b'hastebin.com/aasdfkjgog',
            ]),
            [
                pastebin.BadPaste(u'pastebin.com', u'pwZA'),
                pastebin.BadPaste(u'pastebin.com', u'RwZA'),
                pastebin.BadPaste(u'hastebin.com', u'aasdfkjgog'),
            ]
        )

    def test_dedupe(self):
        self.assertResults(
            b' '.join([
                b'http://pastebin.com/Same',
                b'https://pastebin.com/Same',
                b'pastebin.com/Same',
                b'http://www.pastebin.com/Same',
                b'https://www.pastebin.com/Same',
                b'www.pastebin.com/Same',
            ]),
            [
                pastebin.BadPaste(u'pastebin.com', u'Same'),
            ]
        )

    # TODO: Add some tricky messages, like those involving URLs with
    #       trailing punctuation.

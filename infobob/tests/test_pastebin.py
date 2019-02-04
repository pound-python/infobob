from twisted.trial.unittest import TestCase as TrialTestCase

from infobob import pastebin


class ExtractBadPasteSpecsTestCase(TrialTestCase):
    def setUp(self):
        self.repaster = pastebin.BadPasteRepaster(None, None)

    def assertResults(self, message, expected):
        result = self.repaster.extractBadPasteSpecs(message)
        self.assertEqual(result, expected)

    def test_null(self):
        self.assertResults(b'http://www.google.com/939', set())

    def test_multiple(self):
        self.assertResults(
            b'https://pastebin.com/pwZA and https://pastebin.com/RwZA',
            {
                (b'https://pastebin.com/', b'', b'pastebin.com', b'pwZA'),
                (b'https://pastebin.com/', b'', b'pastebin.com', b'RwZA'),
            }
        )

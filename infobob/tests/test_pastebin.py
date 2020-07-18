import unittest
import urllib
import re

from twisted.internet import defer
from twisted.trial.unittest import SynchronousTestCase as TrialSyncTestCase
from twisted.trial.unittest import TestCase as TrialTestCase
from twisted.web.resource import IResource
import treq.testing
import ddt
import zope.interface as zi

from infobob import pastebin
import infobob.tests.support as sp


@ddt.ddt
class ExtractBadPasteSpecsTestCase(TrialSyncTestCase):
    """
    Integration tests for bad pastebin URL detection.
    """
    def setUp(self):
        self.repaster = pastebin.make_repaster(None)

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
            # Also works with trailing slashes
            (b'pastebin.com/pwZA/', u'pastebin.com', u'pwZA'),
            (b'pastebin.ca/123986/', u'pastebin.ca', u'123986'),
            (b'hastebin.com/asdflkkfig/', u'hastebin.com', u'asdflkkfig'),
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

    @ddt.data(
        b'https://user@pastebin.com/idid',
        b'https://user:pass@pastebin.com/idid',
        b'https://pastebin.com:80/idid',
        b'https://pastebin.com:6500/idid',
    )
    def test_extra_netloc_crap(self, message):
        self.assertResults(
            message, [pastebin.BadPaste(u'pastebin.com', u'idid')]
        )

    @ddt.unpack
    @ddt.data(
        (u'pastebin.com', u'/raw/abYZ09', u'abYZ09'),
        (u'hastebin.com', u'/raw/abcxyz', u'abcxyz'),
    )
    def test_raw_url_paths(self, domain, path, pasteid):
        url = u'https://{0}{1}'.format(domain, path)
        expected = pastebin.BadPaste(domain, pasteid)
        self.assertResults(url.encode('ascii'), [expected])

    @ddt.unpack
    @ddt.data(*[
        (pfx + u'abcxyz' + sfx, u'abcxyz')
        for pfx in (u'/', u'/raw/')
        for sfx in (u'.', u'.a', u'.longsuffix')
    ])
    def test_hastebin_suffix_ignored(self, path, pasteid):
        url = u'https://hastebin.com{0}'.format(path)
        expected = pastebin.BadPaste(u'hastebin.com', pasteid)
        self.assertResults(url.encode('ascii'), [expected])

    # TODO: Add some tricky messages, like those involving URLs with
    #       trailing punctuation, no-scheme URLs with leading chars, etc.


class RepasteTestCase(TrialTestCase):
    def setUp(self):
        fakeBadPastebin = sp.FakeObj()
        fakeBadPastebin.name = u'testbadpb'
        fakeBadPastebin.domains = [u'paste.example.com']
        fakeBadPastebin.contentFromPaste = sp.DeferredSequentialReturner([])

        fakePaster = sp.FakeObj()
        fakePaster.createPaste = sp.DeferredSequentialReturner([])

        self.repaster = pastebin.BadPasteRepaster([fakeBadPastebin], fakePaster)
        self.fakeContentFromPaste = fakeBadPastebin.contentFromPaste
        self.fakeCreatePaste = fakePaster.createPaste

    @defer.inlineCallbacks
    def test_basic_cacheing(self):
        badPaste = pastebin.BadPaste(u'testbadpb', u'allgood')
        expectedRepastedUrl = u'https://paste.example.com/outputid'
        self.fakeContentFromPaste.reset([b'testing testing'])
        self.fakeCreatePaste.reset([expectedRepastedUrl])
        self.repaster._cache._now = lambda: 1

        repastedUrl = yield self.repaster.repaste([badPaste])

        self.assertEqual(
            self.fakeContentFromPaste.calls,
            [sp.Call(badPaste)],
        )
        self.assertEqual(
            self.fakeCreatePaste.calls,
            [sp.Call(b'testing testing', u'python')],
        )
        self.assertEqual(repastedUrl, expectedRepastedUrl)

        # Now make sure these aren't called, only the cache is involved.
        self.fakeContentFromPaste.reset([])
        self.fakeCreatePaste.reset([])

        self.repaster._cache._now = lambda: 2
        repastedUrl = yield self.repaster.repaste([badPaste])
        self.assertEqual(self.fakeContentFromPaste.calls, [])
        self.assertEqual(self.fakeCreatePaste.calls, [])
        # Too soon, so should be None.
        self.assertIsNone(repastedUrl)

        self.repaster._cache._now = lambda: 20
        repastedUrl = yield self.repaster.repaste([badPaste])
        self.assertEqual(self.fakeContentFromPaste.calls, [])
        self.assertEqual(self.fakeCreatePaste.calls, [])
        # After enough of a delay, the url comes through.
        self.assertEqual(repastedUrl, expectedRepastedUrl)

    @defer.inlineCallbacks
    def test_multifile_paste(self):
        badPastes = [
            pastebin.BadPaste(u'testbadpb', u'first'),
            pastebin.BadPaste(u'testbadpb', u'second'),
        ]
        expectedRepastedUrl = u'https://paste.example.com/outputid'
        self.fakeContentFromPaste.reset([
            b"first's content\n",
            b"second's content\n",
        ])
        self.fakeCreatePaste.reset([expectedRepastedUrl])
        self.repaster._cache._now = lambda: 1

        repastedUrl = yield self.repaster.repaste(badPastes)

        self.assertEqual(repastedUrl, expectedRepastedUrl)
        expectedPastedContent = (
            b'### testbadpb::first.py\n'
            b"first's content\n"
            b'\n'
            b'### testbadpb::second.py\n'
            b"second's content\n"
        )
        self.assertEqual(
            self.fakeContentFromPaste.calls,
            [sp.Call(bp) for bp in badPastes],
        )
        self.assertEqual(
            self.fakeCreatePaste.calls,
            [sp.Call(expectedPastedContent, u'multi')],
        )

    @defer.inlineCallbacks
    def test_cache_lru(self):
        # max cache size is 10
        badPasteIds = [unicode(n) for n in range(1, 11 + 1)]
        badPastes = [pastebin.BadPaste(u'testbadpb', id) for id in badPasteIds]
        badPasteContents = tuple([b'' for _ in badPasteIds])
        badPasteRepastedUrls = tuple([
            u'https://paste.example.com/repasted{0}'.format(id)
            for id in badPasteIds
        ])

        self.fakeCreatePaste.reset(badPasteRepastedUrls)
        self.fakeContentFromPaste.reset(badPasteContents)
        self.repaster._cache._now = lambda: 1

        # Don't do the last one yet
        for bp, expUrl in zip(badPastes[:-1], badPasteRepastedUrls):
            resUrl = yield self.repaster.repaste([bp])
            self.assertEqual(resUrl, expUrl)

        # Now the cache should be full.
        self.assertEqual(len(self.repaster._cache), 10)

        self.repaster._cache._now = lambda: 20

        # Cache hit on all but the first badPaste should make
        # it be the one removed later on add.
        for bp, expUrl in zip(badPastes[1:-1], badPasteRepastedUrls[1:]):
            resUrl = yield self.repaster.repaste([bp])
            self.assertEqual(resUrl, expUrl)

        self.assertEqual(len(self.repaster._cache), 10)  # still 10

        # First badPaste still in the cache
        self.assertIn(badPastes[0].identity, self.repaster._cache)
        # Make a new one get added to the cache...
        resUrl = yield self.repaster.repaste([badPastes[-1]])
        self.assertEqual(resUrl, badPasteRepastedUrls[-1])
        self.assertEqual(len(self.repaster._cache), 10)  # still 10
        # ...and verify the first badPaste was dropped from the cache
        self.assertNotIn(badPastes[0].identity, self.repaster._cache)


def contentFromPathComponent(url):
    _, _, badPasteId = url.rpartition(u'/')
    content = b'content for ' + badPasteId.encode('ascii')
    return defer.succeed(content)


@ddt.ddt
class GenericBadPastebinTestCase(TrialTestCase):
    def setUp(self):
        pasteIdGrabber = pastebin.pasteIdFromFirstComponent(
            u'([0-9]{4,12})'
        )
        self.bp = self.makeOne(pasteIdGrabber)

    def makeOne(self, pasteIdFromPath):
        return pastebin.GenericBadPastebin(
            u'paste.example.com',
            [u'alt1.paste.example.com', 'alt2.paste.example.com'],
            pasteIdFromPath,
            u'/raw/',
            contentFromPathComponent,
        )

    @ddt.data(*[
        pfx + u'paste.example.com'
        for pfx in (u'', u'alt1.', u'alt2.')
    ])
    def test_identify_domains(self, domain):
        result = self.bp.identifyPaste(domain, u'/1234', None, None, None)
        expected = pastebin.BadPaste(u'paste.example.com', u'1234')
        self.assertEqual(result, expected)

    def test_identify_rejects_unknown_domain(self):
        errPattern = r"Unknown domain u?'wrong.example.com' for "
        with self.assertRaisesRegexp(ValueError, errPattern):
            self.bp.identifyPaste(u'wrong.example.com', None, None, None, None)

    def test_identify_delegates(self):
        idFinder = sp.SequentialReturner([u'1234'])
        bp = self.makeOne(idFinder)
        result = bp.identifyPaste(
            u'paste.example.com',
            u'/id/path',
            u'',
            u'',
            u'',
        )
        self.assertEqual(
            result,
            pastebin.BadPaste(u'paste.example.com', u'1234'),
        )
        expectedCall = sp.Call(u'/id/path')
        self.assertEqual(idFinder.calls, [expectedCall])

    @ddt.data(u'/', u'/alpha', u'/123')
    def test_identify_rejects_nonmatching_path(self, path):
        errPattern = r"Could not locate paste ID from path u?'{0}'".format(
            re.escape(path)
        )
        with self.assertRaisesRegexp(ValueError, errPattern):
            self.bp.identifyPaste(u'paste.example.com', path, None, None, None)

    @defer.inlineCallbacks
    def test_contentFromPaste(self):
        badPaste = pastebin.BadPaste(u'paste.example.com', u'1234')
        content = yield self.bp.contentFromPaste(badPaste)
        self.assertEqual(content, b'content for 1234')

    @defer.inlineCallbacks
    def test_contentFromPaste_rejects_foreign_badPaste(self):
        foreignBadPaste = pastebin.BadPaste(u'other.example.com', u'1234')
        errPattern = (
            r"Cannot retrieve paste .*u?'other.example.com'.*, "
            "not created by .*u?'paste.example.com'"
        )
        with self.assertRaisesRegexp(ValueError, errPattern):
            yield self.bp.contentFromPaste(foreignBadPaste)


@zi.implementer(IResource)
class CustomResource(object):
    isLeaf = True  # NB: means getChildWithDefault will not be called

    def __init__(self, status, content):
        self.status = status
        self.content = content

    def render(self, request):
        request.setResponseCode(self.status)
        return self.content


class RetrieveUrlContentTestCase(TrialSyncTestCase):
    def doRetrieve(self, status, content):
        self.treqStub = treq.testing.StubTreq(CustomResource(status, content))
        url = 'http://example.com'
        return pastebin.retrieveUrlContent(url, client=self.treqStub)

    def test_success(self):
        d = self.doRetrieve(200, b'foo bar')
        d.addCallback(self.assertEqual, b'foo bar')
        return d

    def test_rejects_non_200_response(self):
        d = self.doRetrieve(400, b'epic fail')
        f = self.failureResultOf(d)
        self.assertRegex(str(f), r'Expected 200 response .* but got 400')

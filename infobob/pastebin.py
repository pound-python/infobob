import re
import operator
import urlparse

from twisted.internet import reactor
from twisted.internet import defer
from twisted.web import client, xmlrpc
from twisted import logger
import treq
import zope.interface as zi
import attr

from infobob import database
from infobob import util

log = logger.Logger()


def make_repaster(db, paster):
    badPastebins = [
        GenericBadPastebin(
            u'pastebin.com',
            [u'www.pastebin.com'],
            u'([a-z0-9]{4,12})',
            u'/raw/',
            retrieveUrlContent,
        ),
        GenericBadPastebin(
            u'pastebin.ca',
            [u'www.pastebin.ca'],
            u'([0-9]{4,12})',
            u'/raw/',
            retrieveUrlContent,
        ),
        GenericBadPastebin(
            u'hastebin.com',
            [u'www.hastebin.com'],
            u'([a-z0-9]{4,12})',
            u'/raw/',
            retrieveUrlContent,
        ),
    ]
    return BadPasteRepaster(db, paster, badPastebins)


class BadPasteRepaster(object):
    def __init__(self, db, paster, badPastebins):
        self._db = db
        self._paster = paster
        self._nameToPastebin = {}
        self._domainToPastebin = {}

        for pb in badPastebins:
            if pb.name in self._nameToPastebin:
                raise ValueError(
                    'Duplicate pastebin name {0!r}'.format(pb.name)
                )
            self._nameToPastebin[pb.name] = pb
            for domain in pb.domains:
                if domain in self._domainToPastebin:
                    raise ValueError(
                        'Duplicate pastebin domain {0!r}'.format(domain)
                    )
                self._domainToPastebin[domain] = pb

    def extractBadPasteSpecs(self, message):
        """
        Find all the "bad" pastebin URLs in a message.

        Returns a list of `IBadPaste` providers, one for each unique
        bad paste found.
        """
        potentialUrls = re.findall(
            b'(?:https?://)?[a-z0-9.-]+/[a-z0-9/]+',
            message,
            flags=re.IGNORECASE,
        )
        pastes = []
        for url in potentialUrls:
            # Prepend scheme if URL doesn't have it, otherwise
            # urlparse won't see the domain part as the netloc.
            hasScheme = re.match(b'^https?://', url, flags=re.IGNORECASE)
            if not hasScheme:
                url = b'http://' + url
            parsed = urlparse.urlparse(url.decode('utf-8'))
            # TODO: Get domain from parsed's netloc, without un/pw or port.
            domain = parsed.netloc.lower()
            pb = self._domainToPastebin.get(domain)
            if pb is None:
                continue
            try:
                paste = pb.identifyPaste(domain, *parsed[2:])
            except ValueError as e:
                log.warn(
                    u'Could not identify paste from URL {url!r}: {error!r}',
                    url=url,
                    err=str(e),
                )
                continue
            pastes.append(paste)

        return _dedupe(pastes, key=operator.attrgetter('identity'))

    @defer.inlineCallbacks
    def repaste(self, badPastes):
        """
        Collect the contents of the provided pastes, post them onto
        a different pastebin (all together), and fire the returned
        Deferred with the URL for the new paste, or None if the same
        repasting was requested again too soon.

        Caches URLs in the database.
        """
        repasteIdent = '|'.join(sorted(paste.identity for paste in badPastes))
        try:
            repasted_url = yield self._db.get_repaste(repasteIdent)
        except database.TooSoonError:
            defer.returnValue(None)
            return
        if repasted_url is not None:
            defer.returnValue(repasted_url)
            return

        defs = [
            self._nameToPastebin[paste.pastebinName].contentFromPaste(paste)
            for paste in badPastes
        ]
        pastes_datas = yield defer.gatherResults(defs)
        # TODO: Update this once outgoing pasting is refactored.
        if len(pastes_datas) == 1:
            data = pastes_datas[0]
            language = 'python'
        else:
            data = b'\n'.join(
                '### %s.py\n%s' % (paste.identity.encode('utf-8'), content)
                for paste, content in zip(badPastes, pastes_datas)
            )
            language = 'multi'
        repasted_url = yield self._paster.createPaste(language, data)
        yield self._db.add_repaste(repasteIdent, repasted_url)
        defer.returnValue(repasted_url)


def _same(value):
    return value


def _dedupe(items, key=_same):
    """
    Deduplicate items, preserving order.
    """
    seen = set()
    deduped = []
    for item in items:
        ident = key(item)
        if ident not in seen:
            seen.add(ident)
            deduped.append(item)
    return deduped


class IBadPastebin(zi.Interface):
    """
    A pastebin site that we'd rather not use, and instead extract
    pastes hosted on it automatically for viewing elsewhere.

    A "paste" here means a single chunk of arbitrary bytes hosted
    on the specific pastebin site handled by this instance.
    """

    name = zi.Attribute("""
        The name of this pastebin as a text string.

        This is used to associate the pastebin with the `IBadPaste`
        providers it produces (via their `pastebinName` attribute).
    """)

    domains = zi.Attribute("""
        A sequence of text strings containing at least one domain
        for which this object is responsible.

        Must all be lowercase, and ASCII-only.

        .. note::

            Multiple domains are allowed primarily to support URLs
            with and without a "www." subdomain. This only makes sense
            if a given paste from any of the declared domains is also
            accessible from a single domain.
    """)

    def identifyPaste(domain, path, params, query, fragment):
        """
        Canonically identify the paste from the parsed components of
        the URL, and return an `IBadPaste` provider.

        All arguments are text strings.
        """

    def contentFromPaste(badPaste):
        """
        Retrieve the raw content from the paste identified by the
        given `IBadPaste` provider (returned by this `IBadPastebin`
        provider's `identifyPaste` method).

        Return a Deferred that fires with the content bytes.
        """


class IBadPaste(zi.Interface):
    """
    The canonical identification of a paste, returned by the
    `identifyPaste` method of a specific `IBadPastebin` provider.

    A provider of this interface must contain enough information
    (not necessarily in the interface attributes) to allow its
    creator to retrieve the paste's content.
    """

    pastebinName = zi.Attribute("""
        The text name of the `IBadPastebin` provider that created
        this instance.
    """)

    identity = zi.Attribute("""
        The text string that identifies the paste and the originating
        pastebin uniquely across all `IBadPastebin` providers in use.
        This is used in the database.
    """)


@zi.implementer(IBadPaste)
@attr.s
class BadPaste(object):
    """
    Simply identify a paste by combining the pastebin's name and
    the paste ID.
    """
    pastebinName = attr.ib()
    id = attr.ib()
    identity = attr.ib(init=False)

    def __attrs_post_init__(self):
        self.identity = u'{0}::{1}'.format(self.pastebinName, self.id)


@zi.implementer(IBadPastebin)
class GenericBadPastebin(object):
    """
    A pastebin that produces paste URLs with an ID in the first
    path component, and offers an alternate URL for downloading the
    raw content given the ID.

    ``pasteIdPattern`` is a (text) regular expression pattern used to
    extract the paste ID from the first component in a paste URL. It
    must contain a single capturing group for the ID itself.
    """
    def __init__(
        self,
        mainDomain,
        altDomains,
        pasteIdPattern,
        rawUrlPathPrefix,
        rawContentRetriever,
    ):
        self.name = mainDomain
        self.domains = (mainDomain,) + tuple(altDomains)
        self._pasteIdRegex = re.compile(pasteIdPattern, flags=re.IGNORECASE)
        self._baseRawUrl = urlparse.urlunparse((
            u'https',
            mainDomain,
            u'/' + rawUrlPathPrefix.strip(u'/') + u'/',
            u'',
            u'',
            u'',
        ))
        self._retrieve = rawContentRetriever

    def identifyPaste(self, domain, path, params, query, fragment):
        if domain not in self.domains:
            raise ValueError('Unknown domain {domain!r} for {self!r}'.format(
                domain=domain, self=self
            ))
        firstComponent, _, _ = path.lstrip('/').partition('/')
        m = self._pasteIdRegex.match(firstComponent)
        if not m:
            raise ValueError(
                'Could not locate paste ID from path {0!r}'.format(path)
            )
        (pasteId,) = m.groups()
        return BadPaste(pastebinName=self.name, id=pasteId)

    def contentFromPaste(self, badPaste):
        if badPaste.pastebinName != self.name:
            raise ValueError('Unknown paste {paste!r} for {self!r}'.format(
                paste=badPaste, self=self
            ))
        url = self._baseRawUrl + badPaste.id
        return self._retrieve(url)

    def __repr__(self):
        return '<{classname}(name={name!r}, domains={domains!r})>'.format(
            classname=type(self).__name__,
            name=self.name,
            domains=self.domains,
        )


class FailedToRetrieve(Exception):
    pass


def retrieveUrlContent(url):
    respDfd = treq.get(url)

    def cbCheckResponseCode(response):
        if response.code != 200:
            raise FailedToRetrieve(
                'Expected 200 response from {url!r} but got {code}'.format(
                    url=url, code=response.code
                )
            )
        return response

    respOkDfd = respDfd.addCallback(cbCheckResponseCode)
    return respOkDfd.addCallback(treq.content)


### Support for outgoing pastes


class Paster(object):
    def __init__(self, db):
        self._db = db

    @defer.inlineCallbacks
    def createPaste(self, language, data):
        # FIXME: Is the language really necessary? Is it a
        #       spacepaste-specific thing?
        # TODO: Reimplement this to allow for non-spacepaste bins.
        available_pastebins = yield self._db.get_pastebins()
        for name, url in available_pastebins:
            proxy = xmlrpc.Proxy(url + '/xmlrpc/')
            try:
                new_paste_id = yield proxy.callRemote(
                    'pastes.newPaste', language, data)
            except:
                log.failure(
                    u'Problem pasting to {pastebin} via {url!r}',
                    pastebin=name,
                    url=url,
                )
                yield self._db.set_latency(name, None)
                yield self._db.record_is_up(name, False)
                continue
            else:
                yield self._db.record_is_up(name, True)
                defer.returnValue('%s/show/%s/' % (url, new_paste_id))
        raise CouldNotPastebinError()

    @defer.inlineCallbacks
    def recordPastebinAvailabilities(self):
        # TODO: Log attempts, successes, failures.
        # TODO: Refactor to support non-spacepaste bins.
        def _eb(_):
            return None, None
        def _cb((latency, _), name):
            return defer.DeferredList([
                self._db.record_is_up(name, bool(latency)),
                self._db.set_latency(name, latency),
            ])
        def do_ping((name, url)):
            proxy = xmlrpc.Proxy(url + '/xmlrpc/')
            d = proxy.callRemote('pastes.getLanguages')
            util.time_deferred(d)
            d.addErrback(_eb)
            d.addCallback(_cb, name)
            return d
        pastebins = yield self._db.get_all_pastebins()
        yield util.parallel(pastebins, 10, do_ping)


class CouldNotPastebinError(Exception):
    pass


# TODO: Define this. It needs methods for posting content and to check if
#       the pastebin is up, and a name that matches with the database's
#       pastebins.name column. That table will now just support the
#       latency stuff, not define which pastebins should be used.
#       Paster should check (on startup?) that all the registered
#       pastebins have an entry in the database.
#
#       Consider if we want to support multi-file pastebins differently.
#       Probably not yet.
class IPastebin(zi.Interface):
    """
    """

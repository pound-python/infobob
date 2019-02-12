import re
import operator
import urlparse
import time

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


def make_repaster(paster):
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
    return BadPasteRepaster(badPastebins, paster)


class BadPasteRepaster(object):
    """
    Manage the rehosting of content from pastebin sites we don't like.

    Arguments:
        badPastebins (iterable of IBadPastebin providers):
            The pastebins we don't like
        paster (Paster):
            Used to rehost the content
    """
    def __init__(self, badPastebins, paster):
        self._paster = paster
        self._nameToPastebin = {}
        self._domainToPastebin = {}

        self._cache = _RepasteCache(maxSize=10, minDelay=10)

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
        Deferred with the URL for the new paste (a text string), or
        None if the same repasting was requested again too soon.

        Caches recently repasted URLs in memory.
        """
        repasteIdent = '|'.join(sorted(paste.identity for paste in badPastes))
        try:
            repasted_url = self._cache[repasteIdent]
        except _TooSoon:
            defer.returnValue(None)
            return
        except KeyError:
            pass
        else:
            defer.returnValue(repasted_url)
            return

        # Cache missed, continue.
        defs = [
            self._nameToPastebin[paste.pastebinName].contentFromPaste(paste)
            for paste in badPastes
        ]
        pastes_datas = yield defer.gatherResults(defs)
        # TODO: Update this once outgoing pasting supports multi-file pastes.
        if len(pastes_datas) == 1:
            data = pastes_datas[0]
            language = u'python'
        else:
            data = b'\n'.join(
                '### %s.py\n%s' % (paste.identity.encode('utf-8'), content)
                for paste, content in zip(badPastes, pastes_datas)
            )
            language = u'multi'
        repasted_url = yield self._paster.createPaste(data, language)
        self._cache[repasteIdent] = repasted_url
        defer.returnValue(repasted_url)


# TODO: Document and test this
class _RepasteCache(object):
    def __init__(self, maxSize, minDelay):
        self._maxSize = maxSize
        self._minDelay = minDelay
        self._store = {}

    def __setitem__(self, pasteIdent, repasteUrl):
        self._store[pasteIdent] = (self._now(), repasteUrl)
        self._truncateToMax()

    def __getitem__(self, pasteIdent):
        storedAt, repasteUrl = self._store[pasteIdent]
        now = self._now()
        if now - storedAt < self._minDelay:
            raise _TooSoon()

        self._store[pasteIdent] = (now, repasteUrl)
        return repasteUrl

    def __len__(self):
        return len(self._store)

    def _now(self):
        return time.time()

    def _truncateToMax(self):
        oversub = len(self) - self._maxSize
        if oversub > 0:
            oldToNew = sorted(
                self._store,
                key=lambda k: self._store[k][0]
            )
            for key in oldToNew[:oversub]:
                self._store.pop(key)


class _TooSoon(Exception):
    pass


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

        This is used as a component of (or an entire) cache key.
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
    if isinstance(url, unicode):
        url = url.encode('utf-8')
    log.info(u'Attempting to retrieve {url!r}'.format(url=url))
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

def make_paster(db):
    pastebins = [
        SpacepastePastebin(u'habpaste', u'https://paste.pound-python.org')
    ]
    return Paster(db, pastebins)


class Paster(object):
    def __init__(self, db, pastebins):
        self._db = db
        self._pastebins = {}
        for pb in pastebins:
            if pb.name in pastebins:
                raise ValueError(
                    'Duplicate pastebin name {pb.name}'.format(pb=pb)
                )
            self._pastebins[pb.name] = pb

    @defer.inlineCallbacks
    def createPaste(self, data, language):
        # TODO: Log attempts, successes, failures.
        database_pastebins = yield self._db.get_pastebins()
        if not database_pastebins:
            log.error(u'No pastebins available')
            raise CouldNotPastebinError()

        for name, _ in database_pastebins:
            pb = self._pastebins.get(name)
            if pb is None:
                log.warn(
                    u'Pastebin in database with name {name!r} has no '
                    u'instance registered, skipping',
                    name=name,
                )
                continue
            try:
                url = yield pb.createPaste(data, language)
            except Exception:
                log.failure(u'Problem pasting to {pastebin}', pastebin=name)
                yield self._db.set_latency(name, None)
                yield self._db.record_is_up(name, False)
                continue
            else:
                yield self._db.record_is_up(name, True)
                defer.returnValue(url)

        log.error(u'Unable to pastebin')
        raise CouldNotPastebinError()

    @defer.inlineCallbacks
    def recordPastebinAvailabilities(self):
        # TODO: This is pretty hard to understand, refactor it.

        def ebSuppress(_):
            return None, None

        def cbRecordUpAndLatency(result, name):
            latency, _ = result
            return defer.DeferredList([
                self._db.record_is_up(name, bool(latency)),
                self._db.set_latency(name, latency),
            ])

        def doPing(pb_name_etc):
            name, _ = pb_name_etc
            pb = self._pastebins.get(name)
            if pb is None:
                log.warn(
                    u'Pastebin in database with name {name!r} has no '
                    u'instance registered',
                    name=name,
                )
                return None, None
            log.info(u'Checking if pastebin {name!r} is up', name=name)
            d = pb.checkIfAvailable()
            util.time_deferred(d)
            d.addErrback(ebSuppress)
            d.addCallback(cbRecordUpAndLatency, name)
            return d

        pastebins = yield self._db.get_all_pastebins()
        yield util.parallel(pastebins, 10, doPing)


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
    A pastebin site to which we can upload content.
    """
    name = zi.Attribute("""
        The name of this pastebin as a text string.

        This is used as the primary key in the database.
    """)

    def checkIfAvailable():
        """
        Check if the pastebin is available by making an idempotent
        HTTP request to it and verifying it responds properly.

        Return a Deferred that fires with True if it's up, False
        otherwise.
        """

    def createPaste(content, language):
        """
        Create a paste with the given ``content`` bytes, and using
        ``language`` (a text string) for syntax highlighting.

        Return a Deferred that fires with a text string URL where
        the paste's content can be viewed.
        """


@zi.implementer(IPastebin)
class SpacepastePastebin(object):
    def __init__(self, name, serviceUrl):
        self.name = name
        self._serviceUrl = serviceUrl
        self._proxy = xmlrpc.Proxy(serviceUrl.encode('ascii') + b'/xmlrpc/')

    def checkIfAvailable(self):
        d = self._proxy.callRemote(b'pastes.getLanguages')
        # TODO: Log failure before returning false.
        d.addCallbacks(lambda _: True, lambda f: False)
        return d

    @defer.inlineCallbacks
    def createPaste(self, content, language):
        pasteId = yield self._proxy.callRemote(
            b'pastes.newPaste', language.encode('ascii'), content)
        defer.returnValue(u'{0}/show/{1}/'.format(
            self._serviceUrl,
            pasteId.decode('ascii'),
        ))

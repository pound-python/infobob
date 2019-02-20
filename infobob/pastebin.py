"""
Pastebin site support.

Read from bad pastebins, and post to good pastebins.
"""
import re
import operator
import urlparse
import urllib
import time

from twisted.internet import reactor
from twisted.internet import defer
from twisted.web import xmlrpc
from twisted.web.http_headers import Headers
from twisted import logger
import treq
import zope.interface as zi
import attr

from infobob import util

log = logger.Logger()


def make_repaster(paster):
    """
    Create the :class:`BadPasteRepaster` instance to be used by
    the bot.

    Requires an instance of :class:`Paster` which will be used
    to re-host the bad pastes.
    """
    badPastebins = [
        GenericBadPastebin(
            u'pastebin.com',
            [u'www.pastebin.com'],
            pasteIdFromFirstOrRaw(u'([a-zA-Z0-9]{4,12})$'),
            u'/raw/',
            retrieveUrlContent,
        ),
        GenericBadPastebin(
            u'pastebin.ca',
            [u'www.pastebin.ca'],
            pasteIdFromFirstComponent(u'([0-9]{4,12})$'),
            u'/raw/',
            retrieveUrlContent,
        ),
        GenericBadPastebin(
            u'hastebin.com',
            [u'www.hastebin.com'],
            pasteIdFromFirstOrRaw(u'([a-zA-Z0-9]{4,12})$'),
            u'/raw/',
            retrieveUrlContent,
        ),
    ]
    return BadPasteRepaster(badPastebins, paster)


def pasteIdFromFirstComponent(pattern):
    """
    Create a function suitable for the ``pasteIdFromPath`` argument
    to :class:`GenericBadPastebin`.

    The (text string) pattern is matched against the first path
    component, and must have a single capturing group to extract the
    paste ID.
    """

    def locatePasteId(path):
        firstComponent, _, _ = path.lstrip(u'/').partition(u'/')
        pasteId = _matchPasteId(pattern, firstComponent)
        if pasteId is not None:
            return pasteId
        raise ValueError(
            'Could not locate paste ID from path {0!r}'.format(path)
        )

    return locatePasteId


def pasteIdFromFirstOrRaw(pattern):
    """
    Create a function suitable for the ``pasteIdFromPath`` argument
    to :class:`GenericBadPastebin` which supports ``/raw/${PASTE_ID}``
    URLs as well.

    The (text string) pattern is matched against the first path
    component (or second if the first is exactly ``"raw"``), and must
    have a single capturing group to extract the paste ID.
    """

    def locatePasteId(path):
        components = path.lstrip(u'/').split(u'/', 3)[:2]
        pasteId = None
        if len(components) == 2 and components[0] == 'raw':
            pasteId = _matchPasteId(pattern, components[1])
        elif len(components) == 1:
            pasteId = _matchPasteId(pattern, components[0])

        if pasteId is not None:
            return pasteId
        raise ValueError(
            'Could not locate paste ID from path {0!r}'.format(path)
        )

    return locatePasteId


def _matchPasteId(pattern, component):
    m = re.match(pattern, component)
    if not m:
        return None
    (pasteId,) = m.groups()
    return pasteId


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
            b'(?:https?://)?[a-z0-9.-:]+/[a-z0-9/]+',
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
            domain = parsed.hostname
            pb = self._domainToPastebin.get(domain)
            if pb is None:
                continue
            try:
                paste = pb.identifyPaste(domain, *parsed[2:])
            except ValueError as e:
                raise
                log.warn(
                    u'Could not identify paste from URL {url!r}: {error!r}',
                    url=url,
                    error=str(e),
                )
                continue
            pastes.append(paste)

        return _dedupe(pastes, key=operator.attrgetter('identity'))

    @defer.inlineCallbacks
    def repaste(self, badPastes):
        """
        Collect the contents of the supplied pastes (an iterable of
        IBadPaste providers) and post them onto a different pastebin
        (all together).

        Return a Deferred that fires with the URL for the new paste
        (a text string), or None if the same repasting was requested
        again too soon.

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


class _RepasteCache(object):
    """
    Simple LRU cache for repaste URLs, which enforces a minimum
    access delay.

    Setting an item will record or update the key's last-access time,
    and trim the cache if there are more than ``maxSize`` items.

    Getting an item will check if the minimum delay has elapsed.
    If it has, the last-access time will be updated to the current
    time, and the item returned. If not, :exc:`._TooSoon` will be
    raised. If the item's key does not exist, :exc:`KeyError` will
    be raised.
    """
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

    def __contains__(self, pasteIdent):
        return pasteIdent in self._store

    def __len__(self):
        return len(self._store)

    def keys(self):
        return self._store.keys()

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

    def __repr__(self):
        return (
            '<{cls.__name__}('
            'maxSize={s._maxSize}, '
            'minDelay={s._minDelay}, '
            'keys={keys}'
            ')'
        ).format(cls=type(self), s=self, keys=sorted(self.keys()))


class _TooSoon(Exception):
    pass


def _same(value):
    return value


def _dedupe(items, key=_same):
    """
    Deduplicate items, preserving order. If a key function is provided,
    it will be used for the duplicate test.
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
    A pastebin that produces paste URLs with an ID in the URL's path,
    and offers a "raw" URL for downloading the raw content of a paste
    given the ID.

    ``pasteIdFromPath`` is a function used to extract the paste ID
    from a paste URL's path (a text string).
    """
    def __init__(
        self,
        mainDomain,
        altDomains,
        pasteIdFromPath,
        rawUrlPathPrefix,
        rawContentRetriever,
    ):
        self.name = mainDomain
        self.domains = (mainDomain,) + tuple(altDomains)
        self._pasteIdFromPath = pasteIdFromPath
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
        pasteId = self._pasteIdFromPath(path)
        return BadPaste(pastebinName=self.name, id=pasteId)

    def contentFromPaste(self, badPaste):
        if badPaste.pastebinName != self.name:
            msgfmt = (
                u'Cannot retrieve paste {paste!r}, not created by {self!r}'
            )
            raise ValueError(msgfmt.format(paste=badPaste, self=self))
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


def retrieveUrlContent(url, client=treq):
    """
    Make a GET request to ``url``, verify 200 status response, and
    return a Deferred that fires with the content as a byte string.

    Will errback with :exc:`FailedToRetrieve` if a non-200 response
    was received.
    """
    if isinstance(url, unicode):
        url = url.encode('utf-8')
    log.info(u'Attempting to retrieve {url!r}'.format(url=url))
    respDfd = client.get(url)

    def cbCheckResponseCode(response):
        #print('response!', response)
        if response.code != 200:
            raise FailedToRetrieve(
                'Expected 200 response from {url!r} but got {code}'.format(
                    url=url, code=response.code
                )
            )
        return response

    respOkDfd = respDfd.addCallback(cbCheckResponseCode)
    return respOkDfd.addCallback(client.content)


### Support for outgoing pastes

def make_paster():
    pastebins = [
        PinnwandPastebin(u'bpaste'),
        SpacepastePastebin(u'habpaste', u'https://paste.pound-python.org'),
    ]
    return Paster(pastebins)


_INF = float('infinity')


class Paster(object):
    """
    Allow for posting content to quickest-responding pastebin service
    by brokering `IPastebin` providers.

    Clients need to periodically call :meth:`checkAvailabilities`.
    """
    def __init__(self, pastebins):
        self._pastebins = pastebins
        self._pastebinLatencies = {}
        for pb in pastebins:
            if pb.name in self._pastebinLatencies:
                raise ValueError(
                    'Duplicate pastebin name {pb.name}'.format(pb=pb)
                )
            self._pastebinLatencies[pb.name] = _INF

    # TODO: Clarify what the `language` arg's semantics are (figure them
    #       out first, naturally).
    @defer.inlineCallbacks
    def createPaste(self, data, language):
        """
        Upload `data` (bytes) to a pastebin, preferring the one with
        the lowest recorded latency.

        Return a Deferred that fires with the new paste's URL (text)
        or errbacks with :exc:`.CouldNotPastebinError`.
        """
        log.info(u'Attempting to pastebin {len} bytes', len=len(data))
        bestFirst = sorted(
            self._pastebins,
            key=lambda pb: self._pastebinLatencies
        )
        for pb in bestFirst:
            log.info(u'Trying pastebin {pb_name!r}', pb_name=pb.name)
            try:
                start = time.time()
                url = yield pb.createPaste(data, language)
                latency = time.time() - start
            except Exception:
                log.failure(u'Error pasting to {pastebin}', pastebin=pb.name)
                self._pastebinLatencies[pb.name] = _INF
                continue
            else:
                self._pastebinLatencies[pb.name] = latency
                log.info(u'Pasted to {pb_name!r}', pb_name=pb.name)
                defer.returnValue(url)

        log.error(
            u'Unable to paste, tried {npastebins} sites',
            npastebins=len(bestFirst),
        )
        raise CouldNotPastebinError()

    @defer.inlineCallbacks
    def checkAvailabilities(self):
        """
        Make requests to all pastebins and record their latencies.
        """

        def ebReportInfLatency(fail, pb_name):
            log.failure(
                u'Error checking latency of pastebin {pb_name!}',
                pb_name=pb_name,
                failure=fail,
            )
            return _INF, None

        def cbRecordLatency(result, pb_name):
            latency, _ = result
            self._pastebinLatencies[pb_name] = latency
            log.info(
                u'Recorded latency for pastebin {pb_name!r} as {latency} seconds',
                pb_name=pb_name,
                latency=latency,
            )

        def doPing(pb):
            log.info(u'Checking if pastebin {pb_name!r} is up', pb_name=pb.name)
            d = pb.checkIfAvailable()
            util.time_deferred(d)
            d.addErrback(ebReportInfLatency, pb.name)
            d.addCallback(cbRecordLatency, pb.name)
            return d

        yield util.parallel(self._pastebins, 10, doPing)


class CouldNotPastebinError(Exception):
    pass


class IPastebin(zi.Interface):
    """
    A pastebin site to which we can upload content.
    """
    name = zi.Attribute("""
        The name of this pastebin as a text string.

        This must be unique across all configured pastebins.
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

        def ebLogAndReportUnavailable(f):
            log.failure(
                u'Unable to communicate with {pb_name!r} pastebin',
                pb_name=self.name,
            )
            return False

        d.addCallbacks(lambda _: True, ebLogAndReportUnavailable)
        return d

    @defer.inlineCallbacks
    def createPaste(self, content, language):
        pasteId = yield self._proxy.callRemote(
            b'pastes.newPaste', language.encode('ascii'), content)
        defer.returnValue(u'{0}/show/{1}/'.format(
            self._serviceUrl,
            pasteId.decode('ascii'),
        ))


@zi.implementer(IPastebin)
class PinnwandPastebin(object):
    def __init__(self, name, client=treq):
        self.name = name
        self._client = client
        # For now just hardcode, no clue if other pastebins run this.
        self._baseUrl = u'https://bpaste.net'
        self._uploadUrl = self._baseUrl + u'/json/new'
        self._showUrlPrefix = self._baseUrl + u'/show/'
        # Valid values are 1day, 1week, and 1month.
        self._expiry = b'1day'

    def checkIfAvailable(self):
        # Uh, I dunno. Just grab the main page I guess.
        d = retrieveUrlContent(
            self._baseUrl.encode('ascii'), client=self._client
        )

        def ebLogAndReportUnavailable(f):
            log.failure(
                u'Unable to communicate with {pb_name!r} pastebin',
                pb_name=self.name,
            )
            return False

        return d.addCallbacks(lambda _: True, ebLogAndReportUnavailable)

    @defer.inlineCallbacks
    def createPaste(self, content, language):
        payload = urllib.urlencode({
            b'lexer': b'python' if language == b'python' else b'text',
            b'code': content,
            b'expiry': self._expiry,
        })
        response = yield self._client.post(
            self._uploadUrl.encode('ascii'),
            data=payload,
            headers=Headers(
                {b'Content-Type': [b'application/x-www-form-urlencoded']}
            ),
        )
        structure = yield response.json()
        defer.returnValue(self._showUrlPrefix + structure['paste_id'])

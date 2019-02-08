import re

from twisted.internet import reactor
from twisted.internet import defer
from twisted.web import client, xmlrpc
from twisted import logger

from infobob import database
from infobob import util

log = logger.Logger()


# TODO: Holy regex batman! Refactor BadPasteRepaster such that the
#       regex shenanigans are no longer necessary. Probably should
#       use a _basic_ regex to scan for URLs, then parse them into
#       interesting parts, and use some other objects to deal with
#       the differences between the various pastebin flavors.
_etherpad_like = ['ietherpad.com', 'piratepad.net', 'piratenpad.de',
    'pad.spline.de', 'typewith.me', 'edupad.ch', 'etherpad.netluchs.de',
    'meetingworlds.com', 'netpad.com.br', 'openetherpad.org',
    'pad.telecomix.org']

_etherpad_like_regex = '|'.join(re.escape(ep) for ep in _etherpad_like)

_bad_pastebin_regex = re.compile(
    r'((?:https?://)?((?:[a-z0-9-]+\.)*)([ph]astebin\.(?:com|org|ca)'
    r'|ospaste\.com|%s)/)(?:raw\.php\?i=)?([a-z0-9]+)(?:\.[a-z0-9]+|/)?' % (_etherpad_like_regex,), re.I)

_pastebin_raw = {
    'hastebin.com': 'http://%shastebin.com/raw/%s',
    'pastebin.com': 'http://%spastebin.com/raw/%s',
    'pastebin.org': 'http://%spastebin.org/pastebin.php?dl=%s',
    'pastebin.ca': 'http://%spastebin.ca/raw/%s',
    'ospaste.com': 'http://%sospaste.com/index.php?dl=%s',
}

for ep in _etherpad_like:
    _pastebin_raw[ep] = 'http://%%s%s/ep/pad/export/%%s/latest?format=txt' % (ep,)


class BadPasteRepaster(object):
    def __init__(self, db, paster):
        self._db = db
        self._paster = paster

    def extractBadPasteSpecs(self, message):
        """
        Find all the "bad" pastebin URLs in a message.

        Returns a set of length-four tuples, containing these elements
        for each unique bad pastebin URL found:

        -   The scheme (if present), full domain name, and initial
            slash from path
        -   The subdomain prefix (if present, including trailing dot)
        -   The domain name without any subdomain prefix
        -   The paste ID
        """
        # TODO: Change this to return a non-tuple. Attrs!
        to_repaste = set(_bad_pastebin_regex.findall(message))
        return to_repaste

    @defer.inlineCallbacks
    def repaste(self, pastes):
        """
        Collect the contents of the provided pastes, post them onto
        a different pastebin (all together), and fire the returned
        Deferred with the URL for the new paste, or None if the same
        repasting was requested again too soon.

        Caches URLs in the database.
        """
        urls = '|'.join(sorted(base + p_id for base, pfix, bin, p_id in pastes))
        try:
            repasted_url = yield self._db.get_repaste(urls)
        except database.TooSoonError:
            defer.returnValue(None)
            return
        if repasted_url is not None:
            defer.returnValue(repasted_url)
            return

        defs = [self._getRawPasteContent(paste) for paste in pastes]
        pastes_datas = yield defer.gatherResults(defs)
        if len(pastes_datas) == 1:
            data = pastes_datas[0]
            language = 'python'
        else:
            data = '\n'.join(
                '### %s.py\n%s' % (paste_id, paste_data)
                for (base, prefix, bin, paste_id), paste_data
                in zip(pastes, pastes_datas)
            )
            language = 'multi'
        repasted_url = yield self._paster.createPaste(language, data)
        yield self._db.add_repaste(urls, repasted_url)
        defer.returnValue(repasted_url)

    def _getRawPasteContent(self, paste):
        """
        Retrieve the raw content from the given paste.

        Returns a Deferred that fires with the paste's content bytes.
        """
        base, prefix, bin, paste_id = paste
        url = _pastebin_raw[bin] % (prefix, paste_id)
        return get_page(url).addCallback(lambda pg_fac: pg_fac[0])


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


class NoRedirectHTTPPageGetter(client.HTTPPageGetter):
    handleStatus_301 = handleStatus_302 = handleStatus_302 = lambda self: None

class MarginallyImprovedHTTPClientFactory(client.HTTPClientFactory):
    protocol = NoRedirectHTTPPageGetter

    def page(self, page):
        if self.waiting:
            self.waiting = 0
            self.deferred.callback((page, self))


def _cbLogPageDetails(page_and_factory, url):
    page, fac = page_and_factory
    log.info(
        u'from {url!r} got {status} {message}, {length} bytes',
        url=url,
        status=fac.status,
        message=fac.message,
        length=len(page),
    )
    return page_and_factory


def _ebLogFailure(f, message, **formatparams):
    log.failure(message, f, **formatparams)
    return f


def get_page(url, *a, **kw):
    scheme, host, port, path = _parse(url)
    factory = MarginallyImprovedHTTPClientFactory(url, *a, **kw)
    reactor.connectTCP(host, port, factory)
    dfd = factory.deferred
    dfd.addCallback(_cbLogPageDetails, url)
    dfd.addErrback(_ebLogFailure, u'Failed fetching {url!r}', url=url)
    return dfd

# TODO: Remove this hack once migrated to treq.
from urlparse import urlunparse
from twisted.web.http import urlparse
# Borrowed from Twisted 13.0.0
def _parse(url, defaultPort=None):
    """
    Split the given URL into the scheme, host, port, and path.
    @type url: C{str}
    @param url: An URL to parse.
    @type defaultPort: C{int} or C{None}
    @param defaultPort: An alternate value to use as the port if the URL does
    not include one.
    @return: A four-tuple of the scheme, host, port, and path of the URL.  All
    of these are C{str} instances except for port, which is an C{int}.
    """
    url = url.strip()
    parsed = urlparse(url)
    scheme = parsed[0]
    path = urlunparse(('', '') + parsed[2:])

    if defaultPort is None:
        if scheme == 'https':
            defaultPort = 443
        else:
            defaultPort = 80

    host, port = parsed[1], defaultPort
    if ':' in host:
        host, port = host.split(':')
        try:
            port = int(port)
        except ValueError:
            port = defaultPort

    if path == '':
        path = '/'

    return scheme, host, port, path

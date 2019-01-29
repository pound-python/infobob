import os.path
import itertools
import operator

from twisted.internet.defer import inlineCallbacks
from twisted.internet import reactor
from twisted.web import client, server
from genshi.template import TemplateLoader
import klein

from infobob.database import NoSuchBan
from infobob.util import parse_time_string


class NoRedirectHTTPPageGetter(client.HTTPPageGetter):
    handleStatus_301 = handleStatus_302 = handleStatus_302 = lambda self: None

class MarginallyImprovedHTTPClientFactory(client.HTTPClientFactory):
    protocol = NoRedirectHTTPPageGetter

    def page(self, page):
        if self.waiting:
            self.waiting = 0
            self.deferred.callback((page, self))

def get_page(url, *a, **kw):
    scheme, host, port, path = _parse(url)
    factory = MarginallyImprovedHTTPClientFactory(url, *a, **kw)
    reactor.connectTCP(host, port, factory)
    return factory.deferred

# TODO: Replace this hack with something supportable.
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


DEFAULT_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'templates')


def renderTemplate(request, tmpl, **kwargs):
    request.setHeader('Content-type', 'text/html; charset=utf-8')
    request.write(tmpl
                  .generate(**kwargs)
                  .render('html', doctype='html5', encoding='utf-8'))
    request.finish()

class InfobobWebUI(object):
    app = klein.Klein()

    def __init__(self, loader, dbpool):
        self.loader = loader
        self.dbpool = dbpool

    @app.route('/bans')
    @inlineCallbacks
    def bans(self, request):
        bans = yield self.dbpool.get_active_bans()
        bans = itertools.groupby(bans, operator.itemgetter(0))
        renderTemplate(request, self.loader.load('bans.html'),
            bans=bans, show_unset=False, show_recent_expiration=False)

    @app.route('/bans/expired')
    @app.route('/bans/expired/<int:count>')
    @inlineCallbacks
    def expiredBans(self, request, count=10):
        bans = yield self.dbpool.get_recently_expired_bans(count)
        bans.sort(key=operator.itemgetter(0, 7))
        bans = itertools.groupby(bans, operator.itemgetter(0))
        renderTemplate(request, self.loader.load('bans.html'),
            bans=bans, show_unset=True, show_recent_expiration=True)

    @app.route('/bans/all')
    @inlineCallbacks
    def allBans(self, request):
        bans = yield self.dbpool.get_all_bans()
        bans = itertools.groupby(bans, operator.itemgetter(0))
        renderTemplate(request, self.loader.load('bans.html'),
            bans=bans, show_unset=True, show_recent_expiration=False)

    @app.route('/bans/edit/<rowid>/<auth>', methods=['GET', 'HEAD'])
    @inlineCallbacks
    def editBan(self, request, rowid, auth):
        ban = yield self.dbpool.get_ban_with_auth(rowid, auth)
        renderTemplate(request, self.loader.load('edit_ban.html'),
            ban=ban, message=None)

    @app.route('/bans/edit/<rowid>/<auth>', methods=['POST'])
    @inlineCallbacks
    def postEditBan(self, request, rowid, auth):
        ban = yield self.dbpool.get_ban_with_auth(rowid, auth)
        _, _, _, _, _, expire_at, reason, _, _ = ban
        if 'expire_at' in request.args:
            raw_expire_at = request.args['expire_at'][0]
            if raw_expire_at == 'never':
                expire_at = None
            else:
                try:
                    expire_at = parse_time_string(raw_expire_at)
                except ValueError:
                    # This will cause the ban reason in the form to be the old
                    # one (from the DB), not very user-friendly... but it
                    # prevents the exception from breaking the page.
                    message = (
                        'Invalid expiration timestamp or relative date {0!r}'
                    ).format(raw_expire_at)
                    renderTemplate(request, self.loader.load('edit_ban.html'),
                        ban=ban, message=message)
                    return
        if 'reason' in request.args:
            reason = request.args['reason'][0]
        yield self.dbpool.update_ban_by_rowid(rowid, expire_at, reason)
        ban = ban[:5] + (expire_at, reason) + ban[7:]
        renderTemplate(request, self.loader.load('edit_ban.html'),
            ban=ban, message='ban details updated')

def makeSite(templates_dir, dbpool):
    loader = TemplateLoader(templates_dir, auto_reload=True)
    webui = InfobobWebUI(loader, dbpool)
    return server.Site(webui.app.resource())

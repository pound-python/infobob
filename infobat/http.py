from twisted.internet.defer import inlineCallbacks
from twisted.internet import reactor
from twisted.web import client, server
from genshi.template import TemplateLoader
from infobat.database import NoSuchBan
from infobat.config import conf
from infobat.util import parse_time_string
from klein.resource import KleinResource
from klein.decorators import expose
import itertools
import operator

class NoRedirectHTTPPageGetter(client.HTTPPageGetter):
    handleStatus_301 = handleStatus_302 = handleStatus_302 = lambda self: None

class MarginallyImprovedHTTPClientFactory(client.HTTPClientFactory):
    protocol = NoRedirectHTTPPageGetter

    def page(self, page):
        if self.waiting:
            self.waiting = 0
            self.deferred.callback((page, self))

def get_page(url, *a, **kw):
    scheme, host, port, path = client._parse(url)
    factory = MarginallyImprovedHTTPClientFactory(url, *a, **kw)
    reactor.connectTCP(host, port, factory)
    return factory.deferred

def renderTemplate(request, tmpl, **kwargs):
    request.setHeader('Content-type', 'text/html; charset=utf-8')
    request.write(tmpl.generate(**kwargs).render('html', doctype='html'))
    request.finish()

class InfobatResource(KleinResource):
    def __init__(self, loader, dbpool):
        super(InfobatResource, self).__init__()
        self.loader = loader
        self.dbpool = dbpool

    @expose('/bans/all')
    @inlineCallbacks
    def allBans(self, request):
        bans = yield self.dbpool.get_all_bans()
        bans = itertools.groupby(bans, operator.itemgetter(0))
        renderTemplate(request, self.loader.load('bans.html'),
            bans=bans, show_unset=True, show_recent_expiration=False)

    @expose('/bans/expired')
    @expose('/bans/expired/<count:int>')
    @inlineCallbacks
    def expiredBans(self, request, count=10):
        bans = yield self.dbpool.get_recently_expired_bans(count)
        bans.sort(key=operator.itemgetter(0, 7))
        bans = itertools.groupby(bans, operator.itemgetter(0))
        renderTemplate(request, self.loader.load('bans.html'),
            bans=bans, show_unset=True, show_recent_expiration=True)

    @expose('/bans/edit/<rowid>/<auth>', methods=['GET', 'HEAD'])
    @inlineCallbacks
    def editBan(self, request, rowid, auth):
        ban = yield self.dbpool.get_ban_with_auth(rowid, auth)
        renderTemplate(request, self.loader.load('edit_ban.html'),
            ban=ban, message=None)

    @expose('/bans/edit/<rowid>/<auth>', methods=['POST'])
    @inlineCallbacks
    def postEditBan(self, request, rowid, auth):
        ban = yield self.dbpool.get_ban_with_auth(rowid, auth)
        _, _, _, _, _, expire_at, reason, _, _ = ban
        if 'expire_at' in request.args:
            raw_expire_at = request.args['expire_at'][0]
            if raw_expire_at == 'never':
                expire_at = None
            else:
                expire_at = parse_time_string(raw_expire_at)
        if 'reason' in request.args:
            reason = request.args['reason'][0]
        yield self.dbpool.update_ban_by_rowid(rowid, expire_at, reason)
        ban = ban[:5] + (expire_at, reason) + ban[7:]
        renderTemplate(request, self.loader.load('edit_ban.html'),
            ban=ban, message='ban details updated')

    @expose('/bans')
    @inlineCallbacks
    def bans(self, request):
        bans = yield self.dbpool.get_active_bans()
        bans = itertools.groupby(bans, operator.itemgetter(0))
        renderTemplate(request, self.loader.load('bans.html'),
            bans=bans, show_unset=False, show_recent_expiration=False)

def makeSite(dbpool):
    loader = TemplateLoader(conf['web.root'], auto_reload=True)
    return server.Site(InfobatResource(loader, conf.dbpool))

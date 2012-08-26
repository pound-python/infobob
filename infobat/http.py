from twisted.internet.defer import inlineCallbacks
from twisted.internet import reactor
from twisted.web import client, server
from genshi.template import TemplateLoader
from infobat.config import conf
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

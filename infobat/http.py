from twisted.web.resource import Resource
from twisted.internet import reactor, defer
from twisted.web import client, server
from genshi.template import TemplateLoader
from infobat.config import conf
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

def deferredPage(func):
    func = defer.inlineCallbacks(func)
    def wrap(self, request):
        (func(self, request)
            .addErrback(request.processingFailed)
            .addErrback(lambda f: None))
        return server.NOT_DONE_YET
    return wrap

def renderTemplate(request, tmpl, **kwargs):
    request.setHeader('Content-type', 'text/html; charset=utf-8')
    request.write(tmpl.generate(**kwargs).render('html', doctype='html'))
    request.finish()

class AllBansResource(Resource):
    def __init__(self, loader, dbpool):
        self.loader = loader
        self.dbpool = dbpool
        Resource.__init__(self)

    @deferredPage
    def render_GET(self, request):
        bans = yield self.dbpool.get_all_bans()
        bans = itertools.groupby(bans, operator.itemgetter(0))
        renderTemplate(request, self.loader.load('bans.html'),
            bans=bans, show_unset=True, show_recent_expiration=False)

class ExpiredBansResource(Resource):
    def __init__(self, loader, dbpool, count):
        self.loader = loader
        self.dbpool = dbpool
        self.count = count
        Resource.__init__(self)

    @deferredPage
    def render_GET(self, request):
        bans = yield self.dbpool.get_recently_expired_bans(self.count)
        bans.sort(key=operator.itemgetter(0, 7))
        bans = itertools.groupby(bans, operator.itemgetter(0))
        renderTemplate(request, self.loader.load('bans.html'),
            bans=bans, show_unset=True, show_recent_expiration=True)

class RootExpiredBansResource(Resource):
    def __init__(self, loader, dbpool):
        self.loader = loader
        self.dbpool = dbpool
        Resource.__init__(self)

    def render_GET(self, request):
        return ExpiredBansResource(self.loader, self.dbpool, 10).render_GET(request)

    def getChild(self, name, request):
        return ExpiredBansResource(self.loader, self.dbpool, int(name))

class BansResource(Resource):
    def __init__(self, loader, dbpool):
        self.loader = loader
        self.dbpool = dbpool
        Resource.__init__(self)
        self.putChild('all', AllBansResource(loader, dbpool))
        self.putChild('expired', RootExpiredBansResource(loader, dbpool))

    @deferredPage
    def render_GET(self, request):
        bans = yield self.dbpool.get_active_bans()
        bans = itertools.groupby(bans, operator.itemgetter(0))
        renderTemplate(request, self.loader.load('bans.html'),
            bans=bans, show_unset=False, show_recent_expiration=False)

def makeSite(dbpool):
    root_resource = Resource()
    loader = TemplateLoader(conf['web.root'], auto_reload=True)
    put = root_resource.putChild
    put('bans', BansResource(loader, dbpool))
    return server.Site(root_resource)


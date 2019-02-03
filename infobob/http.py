import os.path
import itertools
import operator

from twisted.internet.defer import inlineCallbacks
from twisted.web import server
from twisted import logger
from genshi.template import TemplateLoader
import klein

from infobob.database import NoSuchBan
from infobob.util import parse_time_string

# TODO: Remove this! Exporting get_page here only as temporary
#       compat hack.
from infobob.pastebin import get_page

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

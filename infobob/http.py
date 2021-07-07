import os.path
import datetime
import itertools
import operator
import json

import dateutil.tz
from twisted.internet.defer import inlineCallbacks
from twisted.web import server
from twisted import logger
from genshi.template import TemplateLoader
import klein

from infobob.database import NoSuchBan
from infobob.util import parse_time_string


UTC = dateutil.tz.tzutc()
DEFAULT_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'templates')


def renderTemplate(request, tmpl, **kwargs):
    request.setHeader('Content-type', 'text/html; charset=utf-8')
    request.write(tmpl
                  .generate(**kwargs)
                  .render('html', doctype='html5', encoding='utf-8'))
    request.finish()


def _banAsJSONable(bantuple, show_unset, is_expired=None):
    # XXX Badness:
    #       If set to None, `is_expired` will be computed from the ban expiry
    #       and current time, otherwise will use the provided boolean value.
    #       This just papers over the lack of real model objects in
    #       anticipation of a sweeping refactor, and the JSON stuff in general
    #       is really just a hack to make functional tests easier to write.
    (
        _, mask, mode, set_at, set_by, expire_at, reason, unset_at, unset_by
    ) = bantuple
    if is_expired is None:
        is_expired = (
            expire_at < datetime.datetime.now(tz=UTC)
            if expire_at
            else False
        )
    jsonable = dict(
        mask=mask,
        mode=mode,
        setBy=set_by.partition('!')[0],
        setAt=set_at.isoformat(),
        reason=reason,
        expiry=dict(
            when=expire_at.isoformat() if expire_at else None,
            expired=is_expired,
        ),
    )
    if show_unset:
        jsonable['unset'] = dict(
            unsetBy=unset_by.partition('!')[0] if unset_by else None,
            unsetAt=unset_at.isoformat() if unset_by else None,
        )
    return jsonable


def renderJSONBans(request, bans, show_unset, show_recent_expiration):
    byChannel = {}
    for channel, channelBans in bans:
        renderedBans = []
        for ban in channelBans:
            jsonable = _banAsJSONable(
                ban,
                show_unset=show_unset,
                is_expired=show_recent_expiration,
            )
            renderedBans.append(jsonable)
        byChannel[channel] = renderedBans

    _renderJSON(request, byChannel)


def _renderJSON(request, payload):
    request.setHeader('Content-type', 'application/json; charset=utf-8')
    request.write(json.dumps(payload, ensure_ascii=True))
    request.finish()


class InfobobWebUI(object):
    app = klein.Klein()

    def __init__(self, loader, dbpool):
        self.loader = loader
        self.dbpool = dbpool

    def renderBans(self, request, bans, show_unset, show_recent_expiration):
        variables = dict(
            bans=bans,
            show_unset=show_unset,
            show_recent_expiration=show_recent_expiration,
        )
        if request.getHeader('accept') == 'application/json':
            renderJSONBans(request, **variables)
        else:
            renderTemplate(request, self.loader.load('bans.html'), **variables)

    def renderEditBan(self, request, ban, message=None, jsonBadRequest=False):
        if request.getHeader('accept') == 'application/json':
            if jsonBadRequest:
                request.setResponseCode(400)
            jsonable = _banAsJSONable(ban, show_unset=True)
            _renderJSON(request, dict(ban=jsonable, message=message))
        else:
            renderTemplate(request, self.loader.load('edit_ban.html'),
                ban=ban, message=message)

    @app.route('/bans')
    @inlineCallbacks
    def bans(self, request):
        bans = yield self.dbpool.get_active_bans()
        bans = itertools.groupby(bans, operator.itemgetter(0))
        self.renderBans(
            request, bans=bans, show_unset=False, show_recent_expiration=False)

    @app.route('/bans/expired')
    @app.route('/bans/expired/<int:count>')
    @inlineCallbacks
    def expiredBans(self, request, count=10):
        bans = yield self.dbpool.get_recently_expired_bans(count)
        bans.sort(key=operator.itemgetter(0, 7))
        bans = itertools.groupby(bans, operator.itemgetter(0))
        self.renderBans(
            request, bans=bans, show_unset=True, show_recent_expiration=True)

    @app.route('/bans/all')
    @inlineCallbacks
    def allBans(self, request):
        bans = yield self.dbpool.get_all_bans()
        bans = itertools.groupby(bans, operator.itemgetter(0))
        self.renderBans(
            request, bans=bans, show_unset=True, show_recent_expiration=False)

    @app.route('/bans/edit/<rowid>/<auth>', methods=['GET', 'HEAD'])
    @inlineCallbacks
    def editBan(self, request, rowid, auth):
        ban = yield self.dbpool.get_ban_with_auth(rowid, auth)
        self.renderEditBan(request, ban=ban, message=None)

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
                    self.renderEditBan(
                        request, ban=ban, message=message, jsonBadRequest=True)
                    return
        if 'reason' in request.args:
            reason = request.args['reason'][0]
        yield self.dbpool.update_ban_by_rowid(rowid, expire_at, reason)
        ban = ban[:5] + (expire_at, reason) + ban[7:]
        self.renderEditBan(request, ban=ban, message='ban details updated')


def makeSite(templates_dir, dbpool):
    loader = TemplateLoader(templates_dir, auto_reload=True)
    webui = InfobobWebUI(loader, dbpool)
    return server.Site(webui.app.resource())

from twisted.enterprise import adbapi
from functools import partial
import dateutil.tz
import datetime
import sqlite3
import time
import uuid


# TODO: Clarify the semantics of the bans table.
#       Currently they are quite unclear, and are not symmetric between
#       `get_expired_bans` and `get_recently_expired_bans`.
#       Note that "ban is expired" and "ban has been unset" do NOT
#       imply the other. The database should be the single source of
#       truth for the *intention* of the chanops regarding the state of
#       the channel's banlist, and the bot should (periodically) ensure
#       that this intention declaration is reflected in the banlist state.

local = dateutil.tz.tzlocal()
sqlite3.register_adapter(datetime.datetime,
    lambda x: time.mktime(x.astimezone(local).timetuple()))
sqlite3.register_converter('datetime',
    lambda x: datetime.datetime.fromtimestamp(float(x)).replace(
        tzinfo=local))

def interaction(func):
    def wrap(self, *a, **kw):
        return self.dbpool.runInteraction(partial(func, self), *a, **kw)
    return wrap

class TooSoonError(Exception):
    pass

class NoSuchBan(Exception):
    pass

_ADD_USER_TO_CHANNEL = """
    REPLACE INTO channel_users
               (nick, channel)
    VALUES     (?, ?)
"""

_ADD_HOST_TO_USER = """
    REPLACE INTO user_hosts
               (nick, host)
    VALUES     (?, ?)
"""

class InfobobDatabaseRunner(object):
    def __init__(self, conf):
        self._conf = conf
        self.dbpool = adbapi.ConnectionPool(
            'sqlite3', self._conf['database.sqlite.db_file'],
            check_same_thread=False,
            cp_openfun=self._setup_connection,
            detect_types=sqlite3.PARSE_COLNAMES)

    def _setup_connection(self, conn):
        conn.text_factory = str

    def close(self):
        self.dbpool.close()

    @interaction
    def add_lol(self, txn, nick):
        txn.execute("""
            INSERT INTO lol_offenses
            VALUES     (?, ?)
        """, (nick, time.time()))
        txn.execute("""
            SELECT COUNT(*)
            FROM   lol_offenses
            WHERE  username = ?
                   AND time_of >= ?
        """, (nick, time.time() - 120))
        return txn.fetchall()[0][0]

    @interaction
    def set_users_in_channel(self, txn, nicks, channel):
        txn.execute("""
            DELETE FROM channel_users
            WHERE       channel = ?
        """, (channel,))
        txn.executemany(_ADD_HOST_TO_USER, nicks.iteritems())
        txn.executemany(_ADD_USER_TO_CHANNEL,
            ((nick, channel) for nick in nicks))

    @interaction
    def add_user_to_channel(self, txn, nick, host, channel):
        txn.execute(_ADD_HOST_TO_USER, (nick, host))
        txn.execute(_ADD_USER_TO_CHANNEL, (nick, channel))

    @interaction
    def remove_nick_from_channel(self, txn, nick, channel):
        txn.execute("""
            DELETE FROM channel_users
            WHERE       nick = ?
                        AND channel = ?
        """, (nick, channel))

    @interaction
    def remove_nick_from_channels(self, txn, nick):
        txn.execute("""
            DELETE FROM channel_users
            WHERE       nick = ?
        """, (nick,))

    @interaction
    def rename_nick(self, txn, oldnick, newnick):
        txn.execute("""
            DELETE FROM user_hosts
            WHERE       nick = ?
        """, (oldnick,))
        txn.execute("""
            UPDATE user_hosts
            SET    nick = ?
            WHERE  nick = ?
        """, (newnick, oldnick))

    @interaction
    def ensure_active_bans(self, txn, channel, mode, bans):
        bans = [(mask, set_by, set_at) for mask, set_by, set_at in bans
                if '!' in set_by or set_by.count('.') != 2 or not set_by.endswith('.freenode.net')]
        txn.execute("""
            CREATE TEMPORARY TABLE unsure_bans (
                mask TEXT,
                set_by TEXT,
                set_at REAL
            )
        """)
        txn.executemany("INSERT INTO unsure_bans VALUES (?, ?, ?)", bans)
        expire_at = time.time() + self._conf.channel(channel).default_ban_time
        reason = time.strftime("ban pulled from banlist on %F")
        txn.execute("""
            INSERT INTO bans
                        (channel,
                         mask,
                         mode,
                         set_at,
                         set_by,
                         expire_at,
                         reason)
            SELECT :channel,
                   mask,
                   :mode,
                   ub.set_at,
                   ub.set_by,
                   :expire_at,
                   :reason
              FROM unsure_bans ub
                   LEFT JOIN (SELECT rowid,
                                     mask
                                FROM bans
                               WHERE channel = :channel
                                 AND mode = :mode
                                 AND unset_at IS NULL) using (mask)
             WHERE rowid IS NULL;
        """, dict(channel=channel, mode=mode, expire_at=expire_at, reason=reason))
        txn.execute("DROP TABLE unsure_bans")

    @interaction
    def add_ban(self, txn, channel, host, mask, mode):
        now = time.time()
        expire_at = now + self._conf.channel(channel).default_ban_time
        txn.execute("""
            INSERT INTO bans
                        (channel, mask, mode, set_at, set_by, expire_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (channel, mask, mode, now, host, expire_at))
        return txn.lastrowid

    @interaction
    def add_ban_auth(self, txn, rowid):
        auth = uuid.uuid4().hex
        txn.execute("""
            INSERT INTO ban_authorizations
                        (ban, code)
            VALUES (?, ?)
        """, (rowid, auth))
        return auth

    @interaction
    def remove_ban(self, txn, channel, host, mask, mode):
        now = time.time()
        txn.execute("""
            SELECT set_by, set_at, expire_at
            FROM   bans
            WHERE  channel = ?
                   AND mask = ?
                   AND mode = ?
                   AND expire_at >= ?
                   AND unset_at IS NULL
            ORDER BY channel
        """, (channel, mask, mode, now))
        not_expired = txn.fetchall()
        txn.execute("""
            UPDATE bans
            SET    unset_at = ?,
                   unset_by = ?
            WHERE  channel = ?
                   AND mask = ?
                   AND mode = ?
                   AND unset_at IS NULL
        """, (now, host, channel, mask, mode))
        return not_expired

    @interaction
    def get_all_bans(self, txn):
        txn.execute("""
            SELECT channel, mask, mode, set_at as "set_at [datetime]", set_by, expire_at as "expire_at [datetime]", reason, unset_at as "unset_at [datetime]", unset_by
            FROM   bans
            ORDER BY channel, set_at DESC
        """)
        return txn.fetchall()

    @interaction
    def get_active_bans(self, txn):
        txn.execute("""
            SELECT channel, mask, mode, set_at as "set_at [datetime]", set_by, expire_at as "expire_at [datetime]", reason, unset_at as "unset_at [datetime]", unset_by
            FROM   bans
            WHERE  unset_at IS NULL
            ORDER BY channel, set_at DESC
        """)
        return txn.fetchall()

    @interaction
    def get_expired_bans(self, txn):
        txn.execute("""
            SELECT channel, mask, mode
            FROM   bans
            WHERE  expire_at <= ?
                   AND unset_at IS NULL
            ORDER BY channel
        """, (time.time(),))
        return txn.fetchall()

    @interaction
    def get_recently_expired_bans(self, txn, count=10):
        txn.execute("""
            SELECT channel, mask, mode, set_at as "set_at [datetime]", set_by, expire_at as "expire_at [datetime]", reason, unset_at as "unset_at [datetime]", unset_by
            FROM bans
            WHERE expire_at IS NOT NULL
                  AND unset_at IS NOT NULL
                  AND reason != ''
            ORDER BY unset_at DESC
            LIMIT ?
        """, (count,))
        return txn.fetchall()

    @interaction
    def get_ban_with_auth(self, txn, rowid, auth):
        txn.execute("""
            SELECT channel, mask, mode, set_at as "set_at [datetime]", set_by, expire_at as "expire_at [datetime]", reason, unset_at as "unset_at [datetime]", unset_by
            FROM   bans
            JOIN   ban_authorizations authz
                   ON bans.rowid = authz.ban
            WHERE  authz.ban = ?
                   AND authz.code = ?
            LIMIT  1
        """, (rowid, auth))
        res = txn.fetchall()
        if not res:
            raise NoSuchBan()
        return res[0]

    @interaction
    def check_mask(self, txn, channel, mask):
        txn.execute("""
            SELECT nick
            FROM   channel_users
            JOIN   user_hosts USING (nick)
            WHERE  (nick || '!' || host) GLOB ?
                   AND channel = ?
        """, (mask, channel))
        return [nick for nick, in txn]

    @interaction
    def update_ban_expiration(self, txn, channel, mask, mode, delta):
        txn.execute("""
            UPDATE bans
            SET    expire_at = ?
            WHERE  channel = ?
                   AND mask = ?
                   AND mode = ?
                   AND unset_at IS NULL
        """, (
            None if delta is None else time.time() + delta,
            channel, mask, mode)
        )

    @interaction
    def set_ban_reason(self, txn, channel, mask, mode, reason):
        txn.execute("""
            UPDATE bans
            SET    reason = ?
            WHERE  channel = ?
                   AND mask = ?
                   AND mode = ?
                   AND unset_at IS NULL
        """, (reason, channel, mask, mode))

    @interaction
    def update_ban_by_rowid(self, txn, rowid, expire_at, reason):
        txn.execute("""
            UPDATE bans
            SET    expire_at = ?,
                   reason = ?
            WHERE  rowid = ?
        """, (expire_at, reason, rowid))

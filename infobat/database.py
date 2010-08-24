from twisted.enterprise import adbapi
from infobat.config import conf
from functools import partial
import time

def interaction(func):
    def wrap(self, *a, **kw):
        return self.dbpool.runInteraction(partial(func, self), *a, **kw)
    return wrap

class TooSoonError(Exception):
    pass

_ADD_USER_TO_CHANNEL = """
    INSERT INTO channel_users
               (nick, channel)
    VALUES     (?, ?)
"""

_ADD_HOST_TO_USER = """
    REPLACE INTO user_hosts
               (nick, host)
    VALUES     (?, ?)
"""

class InfobatDatabaseRunner(object):
    def __init__(self):
        self.dbpool = adbapi.ConnectionPool(
            'sqlite3', conf['database.sqlite.db_file'],
            check_same_thread=False)

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
    def get_repaste(self, txn, orig_url):
        txn.execute("""
            SELECT repasted_url, time_of
            FROM   repastes
            WHERE  orig_url = ?
        """, (orig_url,))
        row = txn.fetchall()
        if row:
            url, time_of = row[0]
            delta = time.time() - time_of
            if delta > 60*60*24*7:
                return None
            elif delta < 10:
                raise TooSoonError()
            return url.encode()
        return None

    @interaction
    def add_repaste(self, txn, orig_url, repasted_url):
        txn.execute("""
            REPLACE INTO repastes
            VALUES     (?, ?, ?)
        """, (orig_url, repasted_url, time.time()))

    @interaction
    def get_pastebins(self, txn):
        txn.execute("""
            SELECT   name, service_url
            FROM     pastebin_ranks
            ORDER BY rank ASC
        """)
        return [(name, url.encode()) for name, url in txn]

    @interaction
    def get_all_pastebins(self, txn):
        txn.execute("""
            SELECT name, service_url
            FROM   pastebins
        """)
        return [(name, url.encode()) for name, url in txn]

    @interaction
    def set_latency(self, txn, name, latency):
        txn.execute("""
            UPDATE pastebins
            SET    latency = ?
            WHERE  name = ?
        """, (latency, name))
        return bool(txn.rowcount)

    @interaction
    def record_is_up(self, txn, name, is_up):
        txn.execute("""
            INSERT INTO pastebin_reliability
            VALUES     (?, ?, ?)
        """, (name, time.time(), is_up))

    @interaction
    def set_users_in_channel(self, txn, nicks, channel):
        txn.execute("""
            DELETE FROM channel_users
            WHERE       channel = ?
        """, (channel,))
        txn.executemany(_ADD_USER_TO_CHANNEL,
            ((nick, channel) for nick in nicks))
        txn.executemany(_ADD_HOST_TO_USER, nicks.iteritems())

    @interaction
    def add_user_to_channel(self, txn, nick, host, channel):
        txn.execute(_ADD_USER_TO_CHANNEL, (nick, channel))
        txn.execute(_ADD_HOST_TO_USER, (nick, host))

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
            UPDATE channel_users
            SET    nick = ?
            WHERE  nick = ?
        """, (newnick, oldnick))
        txn.execute("""
            UPDATE user_hosts
            SET    nick = ?
            WHERE  nick = ?
        """, (newnick, oldnick))

    @interaction
    def add_ban(self, txn, channel, host, mask, mode):
        now = time.time()
        expire_at = now + conf.channel(channel).default_ban_time
        txn.execute("""
            INSERT INTO bans
                        (channel, mask, mode, set_at, set_by, expire_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (channel, mask, mode, now, host, expire_at))
        return txn.lastrowid

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
    def get_expired_bans(self, txn):
        txn.execute("""
            SELECT channel, mask, mode
            FROM   bans
            WHERE  expire_at <= ?
                   AND unset_at IS NULL
            ORDER BY channel
        """, (time.time(),))
        return [tuple(s.encode() for s in row) for row in txn]

    @interaction
    def check_mask(self, txn, channel, mask):
        txn.execute("""
            SELECT nick
            FROM   channel_users
            JOIN   user_hosts USING (nick)
            WHERE  (nick || '!' || host) GLOB ?
                   AND channel = ?
        """, (mask, channel))
        return [nick.encode() for nick, in txn]

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

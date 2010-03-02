from twisted.enterprise import adbapi
from infobat.config import conf
from functools import partial
import time

def interaction(func):
    def wrap(self, *a, **kw):
        return self.dbpool.runInteraction(partial(func, self), *a, **kw)
    return wrap

class InfobatDatabaseRunner(object):
    def __init__(self):
        self.dbpool = adbapi.ConnectionPool(
            'sqlite3', conf.get('sqlite', 'db_file'), check_same_thread=False)
    
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
            SELECT repasted_url
            FROM   repastes
            WHERE  orig_url = ?
        """, (orig_url,))
        row = txn.fetchall()
        if row:
            return row[0][0].encode()
        return None
    
    @interaction
    def add_repaste(self, txn, orig_url, repasted_url):
        txn.execute("""
            REPLACE INTO repastes
            VALUES     (?, ?)
        """, (orig_url, repasted_url))
    
    @interaction
    def get_pastebins(self, txn):
        txn.execute("""
            SELECT   name,
                     service_url, 
                     latency * latency_multiplier AS rank 
            FROM     pastebins 
            WHERE    latency IS NOT NULL 
            ORDER BY rank ASC
        """)
        return [(name, url.encode()) for name, url, rank in txn]
    
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

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
            'sqlite3', conf.get('sqlite', 'db_file'))
    
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

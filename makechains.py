#!/usr/bin/python
from infobat.config import conf
from infobat import chains
import sys

def main(config_file):
    conf.load(open(config_file))
    conf.apply_defaults()
    db = chains.Database(conf['database.dbm'])
    for line in sys.stdin:
        db.learn(line.rstrip('\r\n'))
    db.sync()

if __name__ == '__main__': 
    main(sys.argv[1])

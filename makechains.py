#!/usr/bin/python
from infobat import chains
import sys

def main(dbfile):
    db = chains.Database(dbfile)
    for line in sys.stdin:
        db.learn(line.rstrip('\r\n'))
    db.sync()

if __name__ == '__main__': 
    main(sys.argv[1])

#!/usr/bin/env python3
"""
List movies, lines, and conversations from the
"Cornel Movie-Dialogs Corpus" data set
(http://www.cs.cornell.edu/~cristian/Cornell_Movie-Dialogs_Corpus.html).

Useful for generating fake chat messages and such, without it being
too fake.

::

    usage: movie_lines.py COMMAND ...

    commands:
        initdb              Initialize the database from the raw corpus
                            (downloading if necessary)
        movies              List the available movies (and their IDs)
        lines               Show all the lines from a given movie
        convos              Show the conversations from a given movie

::

    usage: movie_lines.py lines [-n] movie_id

    optional arguments:
      -n, --nonames  Print just the line, without the character's name

::

    usage: movie_lines.py convos movie_id

"""
import sys
import io
import pathlib
import sqlite3
import zipfile
import urllib.request
import argparse
from functools import partial
from operator import itemgetter
from itertools import groupby



CORPUS_URL = (
    'http://www.cs.cornell.edu/~cristian/data/'
    'cornell_movie_dialogs_corpus.zip'
)
CORPUS_ZIP = pathlib.Path('cornell_movie_dialogs_corpus.zip')
DBFILE = pathlib.Path('cornell_movie_dialogs_corpus.sqlite')


def main():
    parser = argparse.ArgumentParser()
    parser.set_defaults(func=None)
    subparsers = parser.add_subparsers()
    initdb = subparsers.add_parser('initdb', help=(
        'Initialize the database from the raw corpus '
        '(downloading if necessary).'
    ))
    initdb.set_defaults(
        func=lambda _: create_database(CORPUS_ZIP, CORPUS_URL, DBFILE))
    movies = subparsers.add_parser('movies',
        help='List the available movies (and their IDs)')
    movies.set_defaults(func=lambda _: list_movies(DBFILE))
    lines = subparsers.add_parser('lines',
        help='Show all the lines from a given movie')
    lines.add_argument('-n', '--nonames', action='store_false', dest='donames',
        help="Print just the line, without the character's name")
    lines.add_argument('movie_id')
    lines.set_defaults(
        func=lambda args: movie_lines(DBFILE, args.movie_id, args.donames))
    convos = subparsers.add_parser('convos',
        help='Show the conversations from a given movie')
    convos.add_argument('movie_id')
    convos.set_defaults(func=lambda args: conversations(DBFILE, args.movie_id))

    args = parser.parse_args()
    if args.func is None:
        parser.print_help()
    else:
        args.func(args)


def conversations(dbfile, movie_id):
    conn = sqlite3.connect(dbfile)
    sql = '''
        SELECT conversations.id, lines.name, lines.line
        FROM conversations
        JOIN conversation_lines
            ON conversations.id = conversation_lines.conversation_id
        JOIN lines
            ON lines.id = conversation_lines.line_id
        WHERE conversations.movie_id = ?
        ORDER BY conversations.id, lines.id
    '''
    rows = conn.execute(sql, (movie_id,))
    first = True
    sep = '-' * 40
    #for row in rows: print(row)
    for _, convo_lines in groupby(rows, key=itemgetter(0)):
        if first:
            first = False
        else:
            print(sep)
        for cid, character_name, line in convo_lines:
            print(f'{cid} -- {character_name}: {line}')


def movie_lines(dbfile, movie_id, donames):
    conn = sqlite3.connect(dbfile)
    sql = 'SELECT name, line FROM lines WHERE movie_id = ?'
    for character_name, line in conn.execute(sql, (movie_id,)):
        print((character_name + ': ' if donames else '') + line)


def list_movies(dbfile):
    conn = sqlite3.connect(dbfile)
    sql = 'SELECT id, title FROM movies'
    for mid, title in conn.execute(sql):
        print(f'{mid}: {title}')



def create_database(corpus_zip, corpus_url, dbfile):
    if not corpus_zip.exists():
        log(f'{corpus_zip} does not appear to exist')
        download(corpus_url, corpus_zip)
    populate_from_zip(corpus_zip, dbfile)


def download(url, outpath):
    log(f'Downloading {url} to {outpath}...')
    with urllib.request.urlopen(url) as response:
        headers = response.info()
        ctype = headers.get_content_type()
        if ctype != 'application/zip':
            log(
                "Error: expected 'Content-Type: application/zip' "
                f"but got {ctype}"
            )
            sys.exit(1)
        with outpath.open('wb') as outfile:
            for chunk in iter(lambda: response.read(4096), b''):
                outfile.write(chunk)
    log('Download complete')


def populate_from_zip(corpus_zip, dbfile):
    prefix = 'cornell movie-dialogs corpus/'
    log(f'Loading from {corpus_zip}')
    with zipfile.ZipFile(corpus_zip) as archive:
        if dbfile.exists():
            log(f'Removing extant database {dbfile}')
            dbfile.unlink()
        conn = sqlite3.connect(dbfile)
        log(f'Initializing database')
        initialize(conn)
        for filename, load_rows in FILE_HANDLERS:
            log(f'Loading {filename}')
            with archive.open(prefix + filename) as fp:
                rawlines = io.TextIOWrapper(fp, encoding='cp1252')
                with conn:
                    cur = conn.cursor()
                    load_rows(cur, map(split_rawline, rawlines))
    log('Done!')


def log(message):
    print(message, file=sys.stderr)

MOVIES_SCHEMA = '''
    CREATE TABLE movies (
        id text PRIMARY KEY,
        title text NOT NULL,
        year integer NOT NULL,
        rating real NOT NULL,
        votes integer NOT NULL,
        genres text NOT NULL
    );
'''
def load_titles(cursor, rawrows):
    sql = (
        'INSERT INTO movies '
        '(id, title, year, rating, votes, genres) '
        'VALUES (?, ?, ?, ?, ?, ?)'
    )
    rows = (
        (mid, title, int(year.rstrip('/I')), float(rating), int(votes),
            '|'.join(parse_corpus_list(genres)))
        for mid, title, year, rating, votes, genres in rawrows
    )
    cursor.executemany(sql, rows)

def parse_corpus_list(rawlist):
    return [item.strip("' ") for item in rawlist.strip('[]').split(',')]


CHARACTERS_SCHEMA = '''
    CREATE TABLE characters (
        id text PRIMARY KEY,
        movie_id text NOT NULL REFERENCES movies(id),
        name text NOT NULL,
        gender text NOT NULL,
        credits_position text NOT NULL
    );
'''
def load_characters(cursor, rawrows):
    sql = (
        'INSERT INTO characters '
        '(id, movie_id, name, gender, credits_position) '
        'VALUES (?, ?, ?, ?, ?)'
    )
    rows = (
        (cid, mid, name, gender, credpos)
        for cid, name, mid, _, gender, credpos in rawrows
    )
    cursor.executemany(sql, rows)


LINES_SCHEMA = '''
    CREATE TABLE lines (
        id text PRIMARY KEY,
        character_id text NOT NULL REFERENCES characters(id),
        movie_id text NOT NULL REFERENCES movies(id),
        name text NOT NULL,
        line text NOT NULL
    );
'''
def load_lines(cursor, rawrows):
    sql = (
        'INSERT INTO lines '
        '(id, character_id, movie_id, name, line) '
        'VALUES (?, ?, ?, ?, ?)'
    )
    rows = (
        (lid, cid, mid, character_name, line)
        for lid, cid, mid, character_name, line in rawrows
        if line  # Filter out blank lines
    )
    cursor.executemany(sql, rows)


CONVERSATIONS_SCHEMA = '''
    CREATE TABLE conversations (
        id INTEGER PRIMARY KEY,
        character1_id text NOT NULL REFERENCES characters(id),
        character2_id text NOT NULL REFERENCES characters(id),
        movie_id text NOT NULL REFERENCES movies(id)
    );
'''
CONVERSATION_LINES_SCHEMA = '''
    CREATE TABLE conversation_lines (
        conversation_id integer NOT NULL REFERENCES conversations(id),
        line_id text NOT NULL REFERENCES lines(id)
    );
'''
def load_conversations(cursor, rawrows):
    convos_sql = (
        'INSERT INTO conversations '
        '(character1_id, character2_id, movie_id) '
        'VALUES (?, ?, ?)'
    )
    convoline_sql = (
        'INSERT INTO conversation_lines '
        '(conversation_id, line_id) '
        'VALUES (?, ?)'
    )
    for cid1, cid2, mid, raw_lineids in rawrows:
        cursor.execute(convos_sql, (cid1, cid2, mid))
        convo_id = cursor.lastrowid
        line_ids = parse_corpus_list(raw_lineids)
        cursor.executemany(convoline_sql, [(convo_id, lid) for lid in line_ids])


FILE_HANDLERS = (
    ('movie_titles_metadata.txt', load_titles),
    ('movie_characters_metadata.txt', load_characters),
    ('movie_lines.txt', load_lines),
    ('movie_conversations.txt', load_conversations),
)


def split_rawline(rawline):
    return [word.strip() for word in rawline.split(' +++$+++ ')]


def initialize(conn):
    schemas = [
        MOVIES_SCHEMA,
        CHARACTERS_SCHEMA,
        LINES_SCHEMA,
        CONVERSATIONS_SCHEMA,
        CONVERSATION_LINES_SCHEMA,
    ]
    with conn:
        for schema in schemas:
            conn.executescript(schema)


if __name__ == '__main__':
    main()

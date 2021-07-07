Functional test suite for infobob
=================================

Uses charybdis (ircd) and atheme (services), configured to resemble Freenode.
Freenode uses modified versions of these, but so far I haven't been able to
find important differences, so hopefully it's close enough.

Requirements:

-   Docker for the ircd and services
-   Python 3.6+ for the tests
-   Python 2.7 and virtualenv for infobob itself

The `features/` directory has some [Gherkin](https://cucumber.io/docs/gherkin/)
feature files. I don't intend to make these actually executable; they're
primarily intended to document test cases that haven't yet been implemented.


Setup and Running
-----------------

    # tests env
    python3 -m venv tests-env
    tests-env/bin/pip install -r requirements.txt
    # infobob env (with py2)
    python2 -m virtualenv infobob-env
    infobob-env/bin/pip install -e ..
    export INFOBOB_PYTHON=infobob-env/bin/python

Run the ircd and services in another terminal:

    export COMPOSE_PROJECT_NAME=infobob-functests
    docker-compose build && docker-compose up

Wait for them to come up: atheme will output something like
`m_pong(): finished synching with uplink (465 ms)`.

Then run the tests:

    tests-env/bin/pytest


Other Goodies
-------------

If you want to poke around, point your IRC client at localhost:6667
(plaintext). The superadmin is `god`, password `letmein` (for both
NickServ and full IRCops).

There's a `simulate.py` script you can use to run infobob with a fresh db,
along with a few chatty bots. The script takes a single optional argument,
a filename containing phrases to use.

You can use the `movie_lines.py` script to generate a better phrases file,
e.g. `./movie_lines.py lines -n m13` if you're a fan of "Airplane!". Don't
forget to initialize with `./movie_lines.py initdb` first.


Updating the initial services database
--------------------------------------

The NickServ account credentials and channel configurations (with their
associated ChanServ settings) in `config.py` are preloaded in a database file
which is written into the atheme container image on build. If you change the
tests to rely on different services data (e.g. additional accounts or
registered channels), you'll need to update apps/atheme/initial.db.

While it appears to be a flat text file, you should consider it opaque: it's
difficult to hand-edit accurately, and it's probably a bad idea to assume
atheme can gracefully deal with invalid data. Instead, follow this procedure
to start from a clean slate, change the things you need, and update the file:

1.  Note which changes you need to make. If you don't remember exactly, you
    can run this to persist the db for manual comparison:

        docker run \
            --mount type=volume,source=infobob-functests_atheme-db,dst=/athemedb,readonly \
            debian:buster \
            cat /athemedb/services.db \
            > backup.db

2.  Stop docker-compose, then delete the container and volume:

        docker rm infobob-functests_atheme_1
        docker volume rm infobob-functests_atheme-db

3.  Spin it back up:

        export COMPOSE_PROJECT_NAME=infobob-functests
        docker-compose build && docker-compose up

4.  Make the changes you need. The convention for account password is
    `nickname + 'pass'` and email `nickname + '@infobobtest.local'`.
    At least with irssi, you can oneline stuff with `/eval`, which makes
    registering a bunch of users fairly easy:

        /eval nick flubber; \
            msg nickserv register flubberpass flubber@infobobtest.local; \
            msg nickserv logout

5.  Since atheme only writes its db every 5 minutes, you'll need to make sure
    the services DB is updated: log in as `god`, make sure you're identified
    with NickServ, use the `/oper` command to escalate, then
    `/msg operserv update`. Make sure you got a notice from OperServ saying
    `UPDATE completed`.

6.  Stop docker-compose, then grab the DB:

        docker run \
            --rm \
            --mount type=volume,source=infobob-functests_atheme-db,dst=/athemedb,readonly \
            debian:buster \
            cat /athemedb/services.db \
            > apps/atheme/initial.db

7.  Sanitize the DB by replacing your host username, which might show up:
    edit apps/atheme/initial.db and look for MDU lines, e.g.

        MDU somenick private:host:actual ~cdunklau@172.18.0.1
        MDU somenick private:host:vhost ~cdunklau@172.18.0.1

    Replace your username with `someone`, but be careful about it.

8.  Finally, repeat steps 2 and 3, then double check that the changes persist
    and the tests pass. You're ready to commit!

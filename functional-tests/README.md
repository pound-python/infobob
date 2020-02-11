Functional test suite for infobob
=================================


Requires Docker for the ircd (charybdis) and services (atheme). Run it like:

    docker-compose build && docker-compose up

Point your IRC client at localhost:6667 (plaintext) to hop in.

Superadmin user is `god`, password `letmein` (both for nickserv and IRCops).

To ensure the services DB is updated, use the `/oper` command as `god`, and
then `/msg operserv update`, otherwise the db is written every 5 minutes.

Docker volumes are used for persisting logs for the ircd and the database for
services (named `charybdis-logs` and `atheme-db`, respectively).



Python 3.6+. Setup:

    python3 -m venv tests-env
    tests-env/bin/pip install -r requirements.txt

Then make a separate virtualenv (with py2) and install infobob in it.

    python2 -m virtualenv infobob-env
    infobob-env/bin/pip install -e ..

Run:

    export INFOBOB_PYTHON=infobob-env/bin/python
    tests-env/bin/pytest

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

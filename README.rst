This is the minimal setup:

1.  Install requirements in a virtualenv.

2.  Copy infobat.cfg.example to a new file (say, tester.cfg.json) and edit it:

    -   Adjust nickname, channel list, and per-channel configuration as needed.
        You should probably the autojoin config so the bot only joins your
        testing channel.
    -   Add "web" key to root of the config object with an object value
        containing "port" and "web" keys: ``{"port": 8080, "root": "web"}``
    -   Add ``"socket": null`` to the "misc" -> "manhole" object.

3.  Create the db file (default infobat.sqlite) with the schema:
    ``sqlite3 infobat.sqlite < db.schema``

4.  Add a row in the pastebins table so repasting works::

        insert into pastebins (name, service_url) values
            ('pound-python', 'https://paste.pound-python.org');

5.  Run it: venv/bin/twistd -n infobat path/to/tester.cfg.json

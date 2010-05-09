#!/bin/bash

xgettext -d infobat --copyright-holder="Aaron Gallagher" \
    --package-name="infobat" --package-version="0.1r$(bzr revno)" \
    --msgid-bugs-address="habnabit@gmail.com" -p infobat/locale/ \
    --foreign-user -F infobat/*.py
mv infobat/locale/infobat.po infobat/locale/infobat.pot
mkdir -p infobat/locale/en/
msgen infobat/locale/infobat.pot -o infobat/locale/en/infobat.po

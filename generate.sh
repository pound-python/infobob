#!/bin/bash

xgettext -d infobob --copyright-holder="Aaron Gallagher" \
    --package-name="infobob" --package-version="0.1r$(bzr revno)" \
    --msgid-bugs-address="habnabit@gmail.com" -p infobob/locale/infobob/ \
    --foreign-user -F infobob/*.py --from-code=utf-8
mv infobob/locale/infobob/infobob.po infobob/locale/infobob/infobob.pot
msginit -i infobob/locale/infobob/infobob.pot -o infobob/locale/infobob/en.po \
    -l en --no-translator

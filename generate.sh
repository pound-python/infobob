#!/bin/bash

xgettext -d infobat --copyright-holder="Aaron Gallagher" \
    --package-name="infobat" --package-version="0.1r$(bzr revno)" \
    --msgid-bugs-address="habnabit@gmail.com" -p infobat/locale/infobat/ \
    --foreign-user -F infobat/*.py --from-code=utf-8
mv infobat/locale/infobat/infobat.po infobat/locale/infobat/infobat.pot
msginit -i infobat/locale/infobat/infobat.pot -o infobat/locale/infobat/en.po \
    -l en --no-translator

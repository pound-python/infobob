#!/bin/sh

for pofile in infobob/locale/*/*.po; do
    domain=$(basename $(dirname $pofile))
    lang=$(echo $pofile | sed 's:.*/\([a-zA-Z_]*\)\.po:\1:')
    mkdir -p infobob/locale/$lang/LC_MESSAGES
    msgfmt $pofile -o infobob/locale/$lang/LC_MESSAGES/$domain.mo
done

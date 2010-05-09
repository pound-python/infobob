#!/bin/sh

for pofile in infobat/locale/*/*.po; do
    domain=$(basename $(dirname $pofile))
    lang=$(echo $pofile | sed 's:.*/\([a-zA-Z_]*\)\.po:\1:')
    mkdir -p infobat/locale/$lang/LC_MESSAGES
    msgfmt $pofile -o infobat/locale/$lang/LC_MESSAGES/$domain.mo
done

#!/bin/sh

for somedir in infobat/locale/*; do
    if [ -d $somedir ] ; then
        mkdir -p $somedir/LC_MESSAGES
        msgfmt $somedir/infobat.po -o $somedir/LC_MESSAGES/infobat.mo
    fi
done

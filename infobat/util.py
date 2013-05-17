from twisted.internet import defer, task
from datetime import datetime
from dateutil.parser import parse
from dateutil.relativedelta import relativedelta
from dateutil.tz import tzlocal
import time
import re

local = tzlocal()
ISOFORMAT = '%Y-%m-%dT%H:%M:%S'

def parallel(iterable, count, f, *args, **named):
    coop = task.Cooperator()
    work = (f(elem, *args, **named) for elem in iterable)
    return defer.DeferredList([coop.coiterate(work) for i in xrange(count)])

def time_deferred(d):
    def _cb(x):
        return time.time() - now, x
    d.addCallback(_cb)
    now = time.time()
    return d

def delta_to_string(_, delta):
    timestr = []
    if delta.days:
        timestr.append(_(u'%d days') % delta.days)
    minutes, seconds = divmod(delta.seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        timestr.append(_(u'%d hours') % hours)
    if minutes:
        timestr.append(_(u'%d minutes') % minutes)
    if seconds:
        timestr.append(_(u'%d seconds') % seconds)
    if not timestr:
        timestr = u''
    elif len(timestr) == 1:
        timestr = timestr[0]
    else:
        timestr = _(u'%(group)s and %(last)s') % dict(
            group=', '.join(timestr[:-1]),
            last=timestr[-1],
        )
    return timestr

_time_coefficients = {
    's': 'seconds',
    'm': 'minutes',
    'h': 'hours',
    'd': 'days',
    'w': 'weeks', 'wk': 'weeks',
    'mo': 'months',
    'y': 'years', 'yr': 'years',
}
_time_regex = re.compile(r'([0-9]+)(\w+)$', re.I)
def parse_time_string(s):
    if not s.startswith('+'):
        return parse(s)
    args = {}
    for t in s[1:].split():
        m = _time_regex.match(t)
        if not m:
            raise ValueError('invalid relative date part: %r' % (s,))
        name = m.group(2)
        name = _time_coefficients.get(name, name + 's')
        args[name] = int(m.group(1))
    return datetime.now(local) + relativedelta(**args)

def ctime(_, i):
    if i is None:
        when = _(u'never')
    else:
        when = time.ctime(i)
    return _(u'at %(when)s') % dict(when=when)

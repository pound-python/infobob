from twisted.internet import defer, task
import time
import re

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
    's': 1,
    'm': 60,
    'h': 60 * 60,
    'd': 60 * 60 * 24,
    'w': 60 * 60 * 24 * 7,
}
_time_regex = re.compile(r'([0-9]+)([smhdw])$', re.I)
def parse_time_string(s):
    time_len = 0
    for t in s.split():
        m = _time_regex.match(t)
        if not m:
            raise ValueError('invalid time: %r' % (s,))
        time_len += int(m.group(1)) * _time_coefficients[m.group(2)]
    return time_len

def ctime(_, i):
    if i is None:
        when = _(u'never')
    else:
        when = time.ctime(i)
    return _(u'at %(when)s') % dict(when=when)

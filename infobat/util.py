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
    's': 'seconds', 'second': 'seconds', 'seconds': 'seconds',
    'min': 'minutes', 'minute': 'minutes', 'minutes': 'minutes',
    'h': 'hours', 'hour': 'hours', 'hours': 'hours',
    'd': 'days', 'day': 'days', 'days': 'days',
    'w': 'weeks', 'wk': 'weeks', 'week': 'weeks', 'weeks': 'weeks',
    'mo': 'months', 'month': 'months', 'months': 'months',
    'y': 'years', 'yr': 'years', 'year': 'years', 'years': 'years',
}
def parse_time_string(s):
    s = s.strip()
    if not s.startswith('+'):
        return parse(s)
    args = parse_relative_time_string(s)
    return datetime.now(local) + relativedelta(**args)

_time_regex = re.compile(
    r'(?: ^\+ | (?!^)\+?) ([0-9]+) ([^0-9+]*)', re.VERBOSE)
def  parse_relative_time_string(s):
  s = ''.join(s.split())
  parsed = {}
  for m in _time_regex.finditer(s):
      quantity, unit = m.groups()
      quantity = int(quantity)
      try:
          unit = _time_coefficients[unit.lower()]
      except KeyError:
          raise ValueError("Invalid unit %r" % (unit,))

      if unit in parsed and parsed[unit] != quantity:
          raise ValueError(
              "Conflicting quantities for unit %r: %r vs %r"
              % (unit, parsed[unit], quantity))
      parsed[unit] = quantity
  if not parsed:
      raise ValueError('invalid relative date: %r' % (s,))
  return parsed

def ctime(_, i):
    if i is None:
        when = _(u'never')
    else:
        when = time.ctime(i)
    return _(u'at %(when)s') % dict(when=when)

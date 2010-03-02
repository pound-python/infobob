from twisted.internet import defer, task
import time

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

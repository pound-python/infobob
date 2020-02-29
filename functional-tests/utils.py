from twisted.internet import defer
from twisted.internet import task


def sleep(secs: float) -> defer.Deferred:
    from twisted.internet import reactor
    return task.deferLater(reactor, secs, _noop)


def _noop():
    return None

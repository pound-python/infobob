from twisted.internet import defer


class FakeObj(object):
    pass


class SequentialReturner(object):
    """
    Record calls and return the provide values in sequence.
    """
    def __init__(self, return_values):
        self.reset(return_values)

    def __call__(self, *args, **kwargs):
        self.calls.append(Call(*args, **kwargs))
        return self._returns.pop()

    def reset(self, return_values):
        self._returns = list(reversed(return_values))
        self.calls = []


class DeferredSequentialReturner(SequentialReturner):
    """
    Record calls and return Deferreds that fire with the provided
    return values in sequence.
    """
    def __call__(self, *args, **kwargs):
        returnValue = super(DeferredSequentialReturner, self).__call__(
            *args, **kwargs
        )
        return defer.succeed(returnValue)


class Call(object):
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __eq__(self, other):
        if not isinstance(other, Call):
            return NotImplemented
        return (self.args, self.kwargs) == (other.args, other.kwargs)

    def __repr__(self):
        argslist = ', '.join(map(repr, self.args))
        kwargslist = ', '.join(
            '{0}={1!r}'.format(k, v) for k, v in self.kwargs.items()
        )
        callargs = ', '.join(s for s in (argslist, kwargslist) if s)
        return 'Call({0})'.format(callargs)

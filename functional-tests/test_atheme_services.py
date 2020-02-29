import pytest
from twisted.internet import defer
from twisted.web import xmlrpc

from config import (
    CHANOP,
    ALL_USERS,
    ALL_CHANS,
    SERVICES_XMLRPC_URL,
)


@pytest.fixture(name='proxy')
def fixture_proxy():
    from twisted.internet import reactor
    proxy = xmlrpc.Proxy(
        SERVICES_XMLRPC_URL.encode('ascii'),
        connectTimeout=5.0,
        reactor=reactor,
    )
    return proxy


def test_registrations(proxy):
    dfd = defer.succeed(None)
    for creds in ALL_USERS:
        dfd.addCallback(lambda _: rpcLogin(
            proxy, creds.nickname, creds.password))
    return dfd


def rpcLogin(proxy, nickname, password):
    """
    Login via XMLRPC, return a Deferred that fires with the Atheme
    auth token.
    """
    # https://github.com/atheme/atheme/blob/v7.2.9/doc/XMLRPC
    def ebExplain(failure):
        failure.trap(xmlrpc.Fault)
        fault = failure.value
        raise AthemeLoginFailed(
            f'While logging in as {nickname}, got {fault}'
        ) from fault

    loginDfd = proxy.callRemote('atheme.login', nickname, password)
    return loginDfd.addErrback(ebExplain)


class AthemeLoginFailed(Exception):
    pass


def test_registered_channels(proxy):
    channels = ALL_CHANS
    dfd = defer.succeed(None)
    for chan in channels:
        dfd.addCallback(lambda _: checkChannelRegistered(
            proxy, chan, CHANOP.nickname, CHANOP.password))
    return dfd


def checkChannelRegistered(proxy, channel, nickname, password):
    def cbLookupChannel(authtoken):
        host = 'xxx'  # Atheme wants a "source ip" parameter, no clue why.
        return proxy.callRemote(
            'atheme.command', authtoken, nickname, host,
            'chanserv', 'info', channel,
        )

    def ebExplain(failure):
        failure.trap(xmlrpc.Fault)
        fault = failure.value
        raise AthemeChannelRegistrationLookupFailed(
            f'Could not look up status of channel {channel}, got {fault}'
        ) from fault

    dfd = rpcLogin(proxy, nickname, password)
    dfd.addCallback(cbLookupChannel)
    dfd.addErrback(ebExplain)
    return dfd


class AthemeChannelRegistrationLookupFailed(Exception):
    pass

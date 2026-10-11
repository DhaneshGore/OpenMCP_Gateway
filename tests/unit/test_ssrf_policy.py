from __future__ import annotations

import ipaddress

import pytest

from openmcp_gateway.security.ssrf import EgressPolicy

BLOCKED = [
    "127.0.0.1",
    "127.1.2.3",
    "10.0.0.5",
    "172.16.3.4",
    "192.168.1.1",
    "169.254.169.254",  # cloud metadata
    "100.64.0.1",  # carrier-grade NAT
    "0.0.0.0",
    "224.0.0.1",  # multicast
    "198.18.0.1",  # benchmarking
    "::1",
    "fe80::1",
    "fc00::1",
    "fd00:ec2::254",  # AWS IPv6 metadata
    "::ffff:127.0.0.1",  # IPv4-mapped
    "::ffff:169.254.169.254",
    "2002:7f00:0001::",  # 6to4 embedding 127.0.0.1
    "2002:a9fe:a9fe::",  # 6to4 embedding 169.254.169.254
    "64:ff9b::a9fe:a9fe",  # NAT64 embedding 169.254.169.254
    "::",
]
ALLOWED = ["1.1.1.1", "8.8.8.8", "93.184.215.14", "2606:4700:4700::1111", "64:ff9b::808:808"]


@pytest.mark.parametrize("address", BLOCKED)
def test_non_public_addresses_blocked_by_default(address: str) -> None:
    assert not EgressPolicy().is_allowed(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", ALLOWED)
def test_public_addresses_allowed(address: str) -> None:
    assert EgressPolicy().is_allowed(ipaddress.ip_address(address))


def test_allowed_networks_permit_specific_ranges_only() -> None:
    policy = EgressPolicy(allowed_networks=(ipaddress.ip_network("10.20.0.0/16"),))
    assert policy.is_allowed(ipaddress.ip_address("10.20.1.1"))
    assert not policy.is_allowed(ipaddress.ip_address("10.21.0.1"))
    assert not policy.is_allowed(ipaddress.ip_address("169.254.169.254"))


def test_allow_private_still_blocks_multicast_and_unspecified() -> None:
    policy = EgressPolicy(allow_private=True)
    assert policy.is_allowed(ipaddress.ip_address("127.0.0.1"))
    assert not policy.is_allowed(ipaddress.ip_address("0.0.0.0"))
    assert not policy.is_allowed(ipaddress.ip_address("224.0.0.1"))

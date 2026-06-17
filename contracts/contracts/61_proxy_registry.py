"""Proxy Registry: track deployed proxy contracts."""
proxies: Mapping[address, address]

@external
def register(proxy: address, impl: address):
    self.proxies[proxy] = impl

@external
@view
def get_implementation(proxy: address) -> address:
    return self.proxies[proxy]

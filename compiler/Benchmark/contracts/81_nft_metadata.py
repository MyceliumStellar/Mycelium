"""NFT Metadata: map NFT IDs to URIs."""
uris: Mapping[uint256, String]

@external
def set_uri(id: uint256, uri: String):
    self.uris[id] = uri

@external
@view
def get_uri(id: uint256) -> String:
    return self.uris[id]

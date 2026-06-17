"""Metadata Store: store address metadata."""
metadata: Mapping[address, String]

@external
def set_metadata(data: String):
    self.metadata[msg_sender] = data

@external
@view
def get_metadata(user: address) -> String:
    return self.metadata[user]

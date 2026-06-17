"""Social Recovery: recover owner using guardians."""
owner: address
guardians: Mapping[address, bool]
guardian_count: uint256
recovery_threshold: uint256
recovery_votes: Mapping[address, uint256]

@event
class RecoveryInitiated:
    new_owner: indexed(address)

@external
def __init__(threshold: uint256):
    self.owner = msg_sender
    self.recovery_threshold = threshold
    self.guardian_count = 0

@external
def add_guardian(guardian: address):
    assert(msg_sender == self.owner, "Not owner")
    self.guardians[guardian] = True
    self.guardian_count += 1

@external
def vote_recovery(new_owner: address):
    assert(self.guardians[msg_sender], "Not guardian")
    self.recovery_votes[new_owner] += 1
    if self.recovery_votes[new_owner] >= self.recovery_threshold:
        self.owner = new_owner
        self.RecoveryInitiated(new_owner)

@external
@view
def get_owner() -> address:
    return self.owner

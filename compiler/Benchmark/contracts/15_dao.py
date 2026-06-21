"""DAO: on-chain proposal voting."""
owner: address
members: Mapping[address, bool]
member_count: uint256
proposal_count: uint256
proposal_title: Mapping[uint256, String]
votes_for: Mapping[uint256, uint256]
votes_against: Mapping[uint256, uint256]
voted: Mapping[uint256, Mapping[address, bool]]
proposal_passed: Mapping[uint256, bool]
proposal_deadline: Mapping[uint256, uint256]

@event
class ProposalCreated:
    id: indexed(uint256)
    title: String

@event
class Voted:
    id: indexed(uint256)
    voter: indexed(address)
    support: bool

@external
def __init__():
    self.owner = msg_sender
    self.members[msg_sender] = True
    self.member_count = 1

@external
def add_member(account: address):
    assert(msg_sender == self.owner, "Not owner")
    self.members[account] = True
    self.member_count += 1

@external
def create_proposal(title: String) -> uint256:
    assert(self.members[msg_sender], "Not member")
    pid: uint256 = self.proposal_count
    self.proposal_title[pid] = title
    self.votes_for[pid] = 0
    self.votes_against[pid] = 0
    self.proposal_deadline[pid] = block_timestamp + 86400
    self.proposal_count += 1
    self.ProposalCreated(pid, title)
    return pid

@external
def vote(pid: uint256, support: bool):
    assert(self.members[msg_sender], "Not member")
    assert(not self.voted[pid][msg_sender], "Already voted")
    assert(block_timestamp < self.proposal_deadline[pid], "Voting ended")
    self.voted[pid][msg_sender] = True
    if support:
        self.votes_for[pid] += 1
    else:
        self.votes_against[pid] += 1
    self.Voted(pid, msg_sender, support)

@external
def finalize(pid: uint256):
    assert(block_timestamp >= self.proposal_deadline[pid], "Voting ongoing")
    self.proposal_passed[pid] = self.votes_for[pid] > self.votes_against[pid]

@external
@view
def get_votes(pid: uint256) -> uint256:
    return self.votes_for[pid]

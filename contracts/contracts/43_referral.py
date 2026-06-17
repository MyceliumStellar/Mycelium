"""Referral: track and reward referrers."""
referrals: Mapping[address, address]
referral_count: Mapping[address, uint256]
commissions: Mapping[address, uint256]

@external
def register_referral(referrer: address):
    assert(self.referrals[msg_sender] == ZERO_ADDRESS, "Already has referrer")
    self.referrals[msg_sender] = referrer
    self.referral_count[referrer] += 1

@external
def record_purchase():
    referrer: address = self.referrals[msg_sender]
    if referrer != ZERO_ADDRESS:
        self.commissions[referrer] += msg_value / 10

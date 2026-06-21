"""
NftStaking — Stake NFTs to earn tokens with rarity boosts.

Mycelium Smart Contract for Stellar
Allows users to stake NFTs from registered collections to earn reward tokens.
Supports per-collection rates, specific NFT rarity multipliers, batch operations,
and staked NFT enumeration.
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


# ── Error Codes ──────────────────────────────────────────────────────────────

class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    COLLECTION_NOT_REGISTERED = 4
    COLLECTION_ALREADY_REGISTERED = 5
    NFT_ALREADY_STAKED = 6
    NFT_NOT_STAKED = 7
    NOT_NFT_OWNER = 8
    EMPTY_BATCH = 9
    INSUFFICIENT_REWARD_POOL = 10
    NO_REWARDS = 11


# ── Constants ────────────────────────────────────────────────────────────────

MULTIPLIER_BASE = U64(10000)  # 10000 = 1.0x multiplier


@contract
class NftStaking:
    """
    Staking contract where users lock their NFTs in exchange for ERC20 rewards.
    Features per-collection configurations and NFT-specific rarity multipliers.
    """

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    # ── Admin Operations ─────────────────────────────────────────────────

    @external
    def initialize(self, admin: Address, reward_token: Address):
        """
        One-time initialization. Sets admin and reward token.
        """
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("reward_token", reward_token)
        self.storage.set("reward_pool", U128(0))
        self.storage.set("collections", Vec())
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "reward_token": reward_token,
        })

    @external
    def register_collection(self, caller: Address, nft_contract: Address, rate_per_second: U128):
        """
        Admin-only: register a new NFT collection and set its reward rate.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        collections = self.storage.get("collections")
        
        # Check if already registered
        for i in range(collections.len()):
            if collections.get(i) == nft_contract:
                raise ContractError.COLLECTION_ALREADY_REGISTERED

        collections.append(nft_contract)
        self.storage.set("collections", collections)
        self.storage.set(f"rate:{nft_contract}", rate_per_second)
        self.storage.set(f"total_staked:{nft_contract}", U64(0))

        self.env.emit_event("collection_registered", {
            "nft_contract": nft_contract,
            "rate_per_second": rate_per_second,
        })

    @external
    def set_collection_rate(self, caller: Address, nft_contract: Address, rate_per_second: U128):
        """
        Admin-only: update the base reward rate for a registered collection.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if not self._is_collection_registered(nft_contract):
            raise ContractError.COLLECTION_NOT_REGISTERED

        self.storage.set(f"rate:{nft_contract}", rate_per_second)

        self.env.emit_event("collection_rate_updated", {
            "nft_contract": nft_contract,
            "rate_per_second": rate_per_second,
        })

    @external
    def set_rarity_multipliers(
        self,
        caller: Address,
        nft_contract: Address,
        token_ids: Vec,
        multipliers: Vec,
    ):
        """
        Admin-only: configure custom rarity multipliers for specific token IDs.
        e.g. 15000 is a 1.5x multiplier.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        if not self._is_collection_registered(nft_contract):
            raise ContractError.COLLECTION_NOT_REGISTERED

        if token_ids.len() == 0 or token_ids.len() != multipliers.len():
            raise ContractError.EMPTY_BATCH

        for i in range(token_ids.len()):
            token_id = token_ids.get(i)
            multiplier = multipliers.get(i)
            self.storage.set(f"rarity:{nft_contract}:{token_id}", multiplier)

        self.env.emit_event("rarity_multipliers_set", {
            "nft_contract": nft_contract,
            "count": token_ids.len(),
        })

    @external
    def top_up_rewards(self, caller: Address, amount: U128):
        """
        Admin-only: Deposit reward tokens into the contract reward pool.
        """
        caller.require_auth()
        self._require_initialized()
        self._require_admin(caller)

        reward_token = self.storage.get("reward_token")
        self.env.transfer(caller, self.env.current_contract(), reward_token, amount)

        pool = self.storage.get("reward_pool")
        self.storage.set("reward_pool", pool + amount)

        self.env.emit_event("rewards_topped_up", {
            "amount": amount,
            "new_pool": pool + amount,
        })

    # ── User Staking Actions ─────────────────────────────────────────────

    @external
    def stake_batch(self, user: Address, nft_contract: Address, token_ids: Vec):
        """
        Stake multiple NFTs from a single collection.
        Transfers the NFTs from the user to the contract.
        """
        user.require_auth()
        self._require_initialized()

        if not self._is_collection_registered(nft_contract):
            raise ContractError.COLLECTION_NOT_REGISTERED

        if token_ids.len() == 0:
            raise ContractError.EMPTY_BATCH

        now = self.env.ledger().timestamp()
        
        user_list = self.storage.get(f"user_nfts:{user}:{nft_contract}", Vec())
        total_staked = self.storage.get(f"total_staked:{nft_contract}")

        for i in range(token_ids.len()):
            token_id = token_ids.get(i)

            # Ensure NFT is not already staked
            if self.storage.get(f"is_staked:{nft_contract}:{token_id}", False):
                raise ContractError.NFT_ALREADY_STAKED

            # Transfer NFT to contract: calls transfer(from, to, token_id) on the NFT contract
            self.env.call(nft_contract, "transfer", [user, self.env.current_contract(), token_id])

            # Record stake info
            stake = {
                "owner": user,
                "staked_at": now,
                "last_claimed_at": now,
            }
            self.storage.set(f"stake:{nft_contract}:{token_id}", stake)
            self.storage.set(f"is_staked:{nft_contract}:{token_id}", True)

            # Record in user list
            user_list.append(token_id)
            total_staked += U64(1)

        self.storage.set(f"user_nfts:{user}:{nft_contract}", user_list)
        self.storage.set(f"total_staked:{nft_contract}", total_staked)

        self.env.emit_event("nfts_staked", {
            "user": user,
            "nft_contract": nft_contract,
            "token_ids": token_ids,
        })

    @external
    def unstake_batch(self, user: Address, nft_contract: Address, token_ids: Vec):
        """
        Unstake multiple NFTs. Claims pending rewards, removes stake state, and
        transfers NFTs back to user.
        """
        user.require_auth()
        self._require_initialized()

        if token_ids.len() == 0:
            raise ContractError.EMPTY_BATCH

        # Claim pending rewards first
        self._claim_rewards_internal(user, nft_contract, token_ids)

        user_list = self.storage.get(f"user_nfts:{user}:{nft_contract}", Vec())
        total_staked = self.storage.get(f"total_staked:{nft_contract}")

        for i in range(token_ids.len()):
            token_id = token_ids.get(i)

            if not self.storage.get(f"is_staked:{nft_contract}:{token_id}", False):
                raise ContractError.NFT_NOT_STAKED

            stake = self.storage.get(f"stake:{nft_contract}:{token_id}")
            if stake["owner"] != user:
                raise ContractError.NOT_NFT_OWNER

            # Remove stake records
            self.storage.remove(f"stake:{nft_contract}:{token_id}")
            self.storage.remove(f"is_staked:{nft_contract}:{token_id}")

            # Remove from user list
            self._remove_from_vec(user_list, token_id)
            total_staked -= U64(1)

            # Transfer NFT back to owner
            self.env.call(nft_contract, "transfer", [self.env.current_contract(), user, token_id])

        self.storage.set(f"user_nfts:{user}:{nft_contract}", user_list)
        self.storage.set(f"total_staked:{nft_contract}", total_staked)

        self.env.emit_event("nfts_unstaked", {
            "user": user,
            "nft_contract": nft_contract,
            "token_ids": token_ids,
        })

    @external
    def claim_rewards(self, user: Address, nft_contract: Address, token_ids: Vec):
        """
        Claim rewards for specific staked NFTs.
        """
        user.require_auth()
        self._require_initialized()

        if token_ids.len() == 0:
            raise ContractError.EMPTY_BATCH

        self._claim_rewards_internal(user, nft_contract, token_ids)

    # ── View Functions ───────────────────────────────────────────────────

    @view
    def get_pending_rewards(self, user: Address, nft_contract: Address, token_ids: Vec) -> U128:
        """
        Get the accrued pending rewards for a list of staked NFTs.
        """
        if token_ids.len() == 0:
            return U128(0)

        rate = self.storage.get(f"rate:{nft_contract}", U128(0))
        now = self.env.ledger().timestamp()
        total_rewards = U128(0)

        for i in range(token_ids.len()):
            token_id = token_ids.get(i)
            if self.storage.get(f"is_staked:{nft_contract}:{token_id}", False):
                stake = self.storage.get(f"stake:{nft_contract}:{token_id}")
                if stake["owner"] == user:
                    elapsed = now - stake["last_claimed_at"]
                    multiplier = self.storage.get(f"rarity:{nft_contract}:{token_id}", MULTIPLIER_BASE)
                    reward = (U128(elapsed) * rate * U128(multiplier)) / U128(MULTIPLIER_BASE)
                    total_rewards += reward

        return total_rewards

    @view
    def get_staked_nfts(self, user: Address, nft_contract: Address) -> Vec:
        """
        Enumerate all staked token IDs for a user in a collection.
        """
        return self.storage.get(f"user_nfts:{user}:{nft_contract}", Vec())

    @view
    def get_collection_info(self, nft_contract: Address) -> Map:
        """
        Get registration and count statistics for a collection.
        """
        return {
            "registered": self._is_collection_registered(nft_contract),
            "rate_per_second": self.storage.get(f"rate:{nft_contract}", U128(0)),
            "total_staked": self.storage.get(f"total_staked:{nft_contract}", U64(0)),
        }

    # ── Internal Helpers ─────────────────────────────────────────────────

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _is_collection_registered(self, nft_contract: Address) -> Bool:
        collections = self.storage.get("collections")
        for i in range(collections.len()):
            if collections.get(i) == nft_contract:
                return True
        return False

    def _remove_from_vec(self, vec: Vec, value: U64):
        """Remove a value from a Vec by shifting elements."""
        found_idx = -1
        for i in range(vec.len()):
            if vec.get(i) == value:
                found_idx = i
                break
        if found_idx != -1:
            vec.remove(U64(found_idx))

    def _claim_rewards_internal(self, user: Address, nft_contract: Address, token_ids: Vec):
        """
        Calculate and pay out rewards for specified token IDs.
        """
        now = self.env.ledger().timestamp()
        rate = self.storage.get(f"rate:{nft_contract}", U128(0))
        total_payout = U128(0)

        for i in range(token_ids.len()):
            token_id = token_ids.get(i)

            if not self.storage.get(f"is_staked:{nft_contract}:{token_id}", False):
                raise ContractError.NFT_NOT_STAKED

            stake = self.storage.get(f"stake:{nft_contract}:{token_id}")
            if stake["owner"] != user:
                raise ContractError.NOT_NFT_OWNER

            elapsed = now - stake["last_claimed_at"]
            if elapsed > 0:
                multiplier = self.storage.get(f"rarity:{nft_contract}:{token_id}", MULTIPLIER_BASE)
                reward = (U128(elapsed) * rate * U128(multiplier)) / U128(MULTIPLIER_BASE)
                total_payout += reward

                # Update last claimed
                stake["last_claimed_at"] = now
                self.storage.set(f"stake:{nft_contract}:{token_id}", stake)

        if total_payout > U128(0):
            reward_pool = self.storage.get("reward_pool")
            if total_payout > reward_pool:
                raise ContractError.INSUFFICIENT_REWARD_POOL

            self.storage.set("reward_pool", reward_pool - total_payout)
            
            # Pay out rewards
            reward_token = self.storage.get("reward_token")
            self.env.transfer(self.env.current_contract(), user, reward_token, total_payout)

            self.env.emit_event("rewards_claimed", {
                "user": user,
                "nft_contract": nft_contract,
                "amount": total_payout,
            })

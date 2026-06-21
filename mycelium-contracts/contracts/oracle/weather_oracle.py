"""
Weather Oracle — Consensus weather metrics feed with reporter stakes and parametric insurance triggers.

Mycelium Smart Contract for Stellar
"""

from mycelium import (
    contract, external, view, storage, event, auth,
    Address, U64, U128, I128, Bool, Bytes, Map, Vec, Env, Symbol
)


class ContractError:
    NOT_INITIALIZED = 1
    ALREADY_INITIALIZED = 2
    UNAUTHORIZED = 3
    INSUFFICIENT_STAKE = 4
    OUT_OF_BOUNDS = 5
    ALREADY_REPORTED = 6
    ALREADY_FINALIZED = 7
    NOT_ENOUGH_REPORTS = 8
    TRANSFER_FAILED = 9
    TRIGGER_NOT_FOUND = 10
    REENTRANT_CALL = 11


class ConditionType:
    RAINFALL_GREATER = 0
    RAINFALL_LESS = 1
    TEMP_GREATER = 2
    TEMP_LESS = 3
    WIND_GREATER = 4


@contract
class WeatherOracle:
    """Weather Oracle contract validating rainfall, temperature, and wind speed reports
    from staked reporters, generating consensus metrics, and executing parametric insurance callbacks."""

    def __init__(self, env: Env):
        self.env = env
        self.storage = env.storage()

    @external
    def initialize(
        self,
        admin: Address,
        stake_token: Address,
        min_stake: U128,
        quorum: U64,
    ):
        """Initialize configurations."""
        if self.storage.get("initialized", False):
            raise ContractError.ALREADY_INITIALIZED

        admin.require_auth()

        self.storage.set("admin", admin)
        self.storage.set("stake_token", stake_token)
        self.storage.set("min_stake", min_stake)
        self.storage.set("quorum", quorum)
        
        self.storage.set("trigger_count", U64(0))
        self.storage.set("execution_lock", False)
        self.storage.set("initialized", True)

        self.env.emit_event("initialized", {
            "admin": admin,
            "stake_token": stake_token,
            "min_stake": min_stake,
            "quorum": quorum,
        })

    # ------------------------------------------------------------------ #
    #  Reporter Staking                                                   #
    # ------------------------------------------------------------------ #

    @external
    def register_reporter(self, reporter: Address, stake_amount: U128):
        """Register as a weather reporter by staking tokens.

        Args:
            reporter: Address of the reporter.
            stake_amount: Amount of tokens to stake (must be >= min_stake).
        """
        self._require_initialized()
        reporter.require_auth()

        min_stake = self.storage.get("min_stake")
        current_stake = self.storage.get(("stake", reporter), U128(0))
        total_stake = current_stake + stake_amount

        if total_stake < min_stake:
            raise ContractError.INSUFFICIENT_STAKE

        stake_token = self.storage.get("stake_token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(stake_token, "transfer", [reporter, contract_addr, stake_amount])
        if not success:
            raise ContractError.TRANSFER_FAILED

        self.storage.set(("stake", reporter), total_stake)
        self.storage.set(("reporter_active", reporter), True)

        self.env.emit_event("reporter_registered", {
            "reporter": reporter,
            "added_stake": stake_amount,
            "total_stake": total_stake,
        })

    @external
    def withdraw_stake(self, reporter: Address, amount: U128):
        """Withdraw reporter stake. Deactivates reporter status if stake falls below minimum.

        Args:
            reporter: Address of the reporter.
            amount: Stake tokens to withdraw.
        """
        self._require_initialized()
        reporter.require_auth()

        current_stake = self.storage.get(("stake", reporter), U128(0))
        if current_stake < amount:
            raise ContractError.INSUFFICIENT_STAKE

        new_stake = current_stake - amount
        min_stake = self.storage.get("min_stake")

        if new_stake < min_stake:
            self.storage.set(("reporter_active", reporter), False)

        self.storage.set(("stake", reporter), new_stake)

        stake_token = self.storage.get("stake_token")
        contract_addr = self.env.current_contract_address()
        success = self.env.invoke_contract(stake_token, "transfer", [contract_addr, reporter, amount])
        if not success:
            raise ContractError.TRANSFER_FAILED

        self.env.emit_event("stake_withdrawn", {
            "reporter": reporter,
            "amount": amount,
            "remaining_stake": new_stake,
        })

    # ------------------------------------------------------------------ #
    #  Weather Reporting                                                  #
    # ------------------------------------------------------------------ #

    @external
    def submit_report(
        self,
        reporter: Address,
        location: Symbol,
        epoch: U64,
        rainfall: U64,
        temperature: I128,
        wind_speed: U64,
    ):
        """Submit a weather report for a specific location and epoch.

        Args:
            reporter: The registered reporter address.
            location: The location identifier (e.g. Symbol("NYC")).
            epoch: The timestamp epoch (e.g. daily block or round identifier).
            rainfall: Millimeters of rain (bound: 0 to 1000).
            temperature: Decicelsius temperature (bound: -1000 to 700, represents -100C to +70C).
            wind_speed: Kilometers per hour wind speed (bound: 0 to 500).
        """
        self._require_initialized()
        reporter.require_auth()

        # Validate reporter is active
        if not self.storage.get(("reporter_active", reporter), False):
            raise ContractError.UNAUTHORIZED

        # Validate physical limits
        if rainfall > U64(1000):
            raise ContractError.OUT_OF_BOUNDS
        if temperature < I128(-1000) or temperature > I128(700):
            raise ContractError.OUT_OF_BOUNDS
        if wind_speed > U64(500):
            raise ContractError.OUT_OF_BOUNDS

        # Check if already finalized
        consensus_key = ("consensus", location, epoch)
        consensus = self.storage.get(consensus_key, None)
        if consensus is not None and consensus["finalized"]:
            raise ContractError.ALREADY_FINALIZED

        # Check double report
        report_key = ("report", location, epoch, reporter)
        if self.storage.get(report_key, None) is not None:
            raise ContractError.ALREADY_REPORTED

        report = {
            "rainfall": rainfall,
            "temperature": temperature,
            "wind_speed": wind_speed,
            "reporter": reporter,
        }
        self.storage.set(report_key, report)

        # Update report tracking
        reporters_list_key = ("reporters", location, epoch)
        reporters_vec = self.storage.get(reporters_list_key, Vec())
        reporters_vec.append(reporter)
        self.storage.set(reporters_list_key, reporters_vec)

        self.env.emit_event("report_submitted", {
            "location": location,
            "epoch": epoch,
            "reporter": reporter,
            "rainfall": rainfall,
            "temperature": temperature,
        })

    @external
    def finalize_consensus(self, caller: Address, location: Symbol, epoch: U64):
        """Aggregate reports and finalize the weather metrics for a location and epoch.

        Args:
            caller: Any address can trigger aggregation if quorum is met.
            location: Weather location symbol.
            epoch: Target epoch.
        """
        self._require_initialized()
        caller.require_auth()
        self._require_no_reentrant()

        consensus_key = ("consensus", location, epoch)
        consensus = self.storage.get(consensus_key, None)
        if consensus is not None and consensus["finalized"]:
            raise ContractError.ALREADY_FINALIZED

        reporters_vec = self.storage.get(("reporters", location, epoch), Vec())
        report_count = len(reporters_vec)
        quorum = self.storage.get("quorum")

        if U64(report_count) < quorum:
            raise ContractError.NOT_ENOUGH_REPORTS

        # Simple average/mean aggregation (can also implement median)
        total_rainfall = U64(0)
        total_temp = I128(0)
        total_wind = U64(0)

        for i in range(report_count):
            rep_addr = reporters_vec[i]
            rep_data = self.storage.get(("report", location, epoch, rep_addr))
            total_rainfall = total_rainfall + rep_data["rainfall"]
            total_temp = total_temp + rep_data["temperature"]
            total_wind = total_wind + rep_data["wind_speed"]

        avg_rainfall = total_rainfall / U64(report_count)
        avg_temp = total_temp / I128(report_count)
        avg_wind = total_wind / U64(report_count)

        consensus_data = {
            "location": location,
            "epoch": epoch,
            "rainfall": avg_rainfall,
            "temperature": avg_temp,
            "wind_speed": avg_wind,
            "finalized": True,
            "total_reports": U64(report_count),
        }

        self.storage.set(consensus_key, consensus_data)

        self.env.emit_event("weather_finalized", {
            "location": location,
            "epoch": epoch,
            "rainfall": avg_rainfall,
            "temperature": avg_temp,
            "wind_speed": avg_wind,
        })

        # Process matching insurance triggers
        self._process_insurance_triggers(location, epoch, avg_rainfall, avg_temp, avg_wind)

    # ------------------------------------------------------------------ #
    #  Parametric Insurance Triggers                                      #
    # ------------------------------------------------------------------ #

    @external
    def register_trigger(
        self,
        caller: Address,
        location: Symbol,
        condition_type: U64,
        threshold_val: I128,
        callback_contract: Address,
        callback_method: Symbol,
    ) -> U64:
        """Register a parametric insurance trigger callback.

        Args:
            caller: Subscriber/Insurance protocol address.
            location: target location.
            condition_type: Enum ConditionType.
            threshold_val: Value limit to check.
            callback_contract: Contract to call when condition is met.
            callback_method: Method symbol of the target contract callback.
        """
        self._require_initialized()
        caller.require_auth()

        trigger_id = self.storage.get("trigger_count") + U64(1)
        self.storage.set("trigger_count", trigger_id)

        trigger = {
            "id": trigger_id,
            "location": location,
            "condition_type": condition_type,
            "threshold_val": threshold_val,
            "callback_contract": callback_contract,
            "callback_method": callback_method,
            "active": True,
        }

        self.storage.set(("trigger", trigger_id), trigger)
        
        # Add to location list
        loc_triggers = self.storage.get(("location_triggers", location), Vec())
        loc_triggers.append(trigger_id)
        self.storage.set(("location_triggers", location), loc_triggers)

        self.env.emit_event("trigger_registered", {
            "trigger_id": trigger_id,
            "location": location,
            "condition_type": condition_type,
            "threshold": threshold_val,
        })

        return trigger_id

    # ------------------------------------------------------------------ #
    #  Admin Operations                                                   #
    # ------------------------------------------------------------------ #

    @external
    def update_config(self, admin: Address, min_stake: U128, quorum: U64):
        """Update configurations. Only Admin."""
        self._require_admin(admin)
        self.storage.set("min_stake", min_stake)
        self.storage.set("quorum", quorum)
        self.env.emit_event("config_updated", {"min_stake": min_stake, "quorum": quorum})

    @external
    def slash_reporter(self, admin: Address, reporter: Address, amount: U128):
        """Slash stake of a malicious reporter. Only Admin."""
        self._require_admin(admin)
        current_stake = self.storage.get(("stake", reporter), U128(0))
        if current_stake < amount:
            amount = current_stake

        new_stake = current_stake - amount
        self.storage.set(("stake", reporter), new_stake)

        if new_stake < self.storage.get("min_stake"):
            self.storage.set(("reporter_active", reporter), False)

        # Slashed tokens stay in the contract or are burned/transferred. Here, they stay in contract treasury.
        self.env.emit_event("reporter_slashed", {
            "reporter": reporter,
            "slashed_amount": amount,
            "remaining_stake": new_stake,
        })

    @external
    def deactivate_trigger(self, admin: Address, trigger_id: U64):
        """Deactivate a trigger. Only Admin or trigger creator could be added, here Admin."""
        self._require_admin(admin)
        trigger = self.storage.get(("trigger", trigger_id), None)
        if trigger is None:
            raise ContractError.TRIGGER_NOT_FOUND

        trigger["active"] = False
        self.storage.set(("trigger", trigger_id), trigger)
        self.env.emit_event("trigger_deactivated", {"trigger_id": trigger_id})

    @external
    def transfer_admin(self, admin: Address, new_admin: Address):
        """Transfer admin role. Only Admin."""
        self._require_admin(admin)
        self.storage.set("admin", new_admin)
        self.env.emit_event("admin_transferred", {"old_admin": admin, "new_admin": new_admin})

    # ------------------------------------------------------------------ #
    #  View Functions                                                     #
    # ------------------------------------------------------------------ #

    @view
    def get_weather(self, location: Symbol, epoch: U64) -> Map:
        """Get finalized weather consensus data."""
        self._require_initialized()
        consensus = self.storage.get(("consensus", location, epoch), None)
        if consensus is None:
            raise ContractError.ALREADY_INITIALIZED
        return consensus

    @view
    def get_reporter_stake(self, reporter: Address) -> U128:
        """Get reporter stake."""
        self._require_initialized()
        return self.storage.get(("stake", reporter), U128(0))

    @view
    def is_reporter_active(self, reporter: Address) -> Bool:
        """Check if a reporter is active."""
        self._require_initialized()
        return self.storage.get(("reporter_active", reporter), False)

    @view
    def get_trigger(self, trigger_id: U64) -> Map:
        """Get trigger details."""
        self._require_initialized()
        trigger = self.storage.get(("trigger", trigger_id), None)
        if trigger is None:
            raise ContractError.TRIGGER_NOT_FOUND
        return trigger

    # ------------------------------------------------------------------ #
    #  Internal Helpers                                                   #
    # ------------------------------------------------------------------ #

    def _require_initialized(self):
        if not self.storage.get("initialized", False):
            raise ContractError.NOT_INITIALIZED

    def _require_admin(self, caller: Address):
        caller.require_auth()
        admin = self.storage.get("admin")
        if caller != admin:
            raise ContractError.UNAUTHORIZED

    def _require_no_reentrant(self):
        if self.storage.get("execution_lock", False):
            raise ContractError.REENTRANT_CALL

    def _process_insurance_triggers(
        self,
        location: Symbol,
        epoch: U64,
        rainfall: U64,
        temperature: I128,
        wind_speed: U64,
    ):
        """Scan triggers for this location and invoke callbacks for met conditions."""
        trigger_ids = self.storage.get(("location_triggers", location), Vec())
        
        self.storage.set("execution_lock", True)

        for i in range(len(trigger_ids)):
            t_id = trigger_ids[i]
            trigger = self.storage.get(("trigger", t_id))
            
            if not trigger["active"]:
                continue

            triggered = False
            cond = trigger["condition_type"]
            threshold = trigger["threshold_val"]

            if cond == ConditionType.RAINFALL_GREATER:
                triggered = rainfall > U64(threshold)
            elif cond == ConditionType.RAINFALL_LESS:
                triggered = rainfall < U64(threshold)
            elif cond == ConditionType.TEMP_GREATER:
                triggered = temperature > threshold
            elif cond == ConditionType.TEMP_LESS:
                triggered = temperature < threshold
            elif cond == ConditionType.WIND_GREATER:
                triggered = wind_speed > U64(threshold)

            if triggered:
                # Mark inactive to avoid double trigger
                trigger["active"] = False
                self.storage.set(("trigger", t_id), trigger)

                # Execute callback (catch failure to prevent blocking finalize)
                try:
                    # Signature callback(trigger_id, location, epoch, actual_value)
                    self.env.invoke_contract(
                        trigger["callback_contract"],
                        trigger["callback_method"],
                        [t_id, location, epoch, threshold]
                    )
                    self.env.emit_event("insurance_triggered", {
                        "trigger_id": t_id,
                        "location": location,
                        "epoch": epoch,
                    })
                except Exception:
                    pass

        self.storage.set("execution_lock", False)

class ZeusError(Exception):
    """Base exception for Zeus operational errors."""
    pass

class EmptyOrderbookError(ZeusError):
    """Raised when a Polymarket orderbook returns no bids or asks or 0 liquidity."""
    pass

class ObservationUnavailableError(ZeusError):
    """Raised when weather observations cannot be fetched from any source."""
    pass

class MissingCalibrationError(ZeusError):
    """Raised when a strictly required calibration constant (like ASOS->WU offset) is missing."""
    pass


class SettlementPrecisionError(ZeusError):
    """Raised when a settlement value violates the integer precision contract.

    Polymarket settles on the integer value displayed by Weather Underground.
    Any code path that writes a non-integer settlement_value to the DB is
    a contract violation that corrupts calibration training data.
    """
    pass

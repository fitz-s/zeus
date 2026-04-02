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

"""Discovery modes — parameters to CycleRunner, NOT separate code paths."""

from enum import Enum


class DiscoveryMode(Enum):
    OPENING_HUNT = "opening_hunt"              # Fresh markets < 24h old
    UPDATE_REACTION = "update_reaction"        # Post-ENS update, 24h+ markets
    DAY0_CAPTURE = "day0_capture"              # Markets < 6h to settlement
    IMMINENT_OPEN_CAPTURE = "imminent_open_capture"  # Markets 0-24h to settlement (re-opened / D+1 gap)

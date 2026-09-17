# Created: 2026-09-17
# Authority basis: operator directive 2026-09-17 — "包括预报每一步都要做到极限的提前比市场
#   拿到最新准确数据并正确计算才能领先". Measured: on battery the host entered Deep Idle sleep
#   for 372/189/398 minutes on 2026-09-14/15/16 (26 %, 13 %, 28 % of the day), and every Zeus
#   daemon stopped with it. The 2026-09-16 12Z ECMWF ENS cycle published at 19:40Z, its fetch
#   window opened at 20:05Z, and the 20:10Z cron fired 15 minutes late into a
#   "Run time of job was missed" skip; the cycle only landed at 22:33Z — 2.9 hours of possession
#   lag caused entirely by the host, not by any provider or by our code.
"""Hold a macOS power assertion for as long as a Zeus ingest/trading daemon is alive.

A daemon that must possess data ahead of the market cannot be suspended by the host between
its own scheduler ticks. macOS grants that guarantee through an IOKit power assertion, and an
assertion is owned by a PROCESS: it is created when the daemon starts and released by the
kernel the instant the daemon dies, so it can never outlive the work that justifies it. That
is the whole mechanism — no timer to renew, no external tool to keep running, no file to clean
up. (The assertions observed holding the host awake before this module were Amphetamine and two
Electron apps: they follow the human, not the daemon, and they vanished exactly on the days the
machine ran on battery.)

`PreventUserIdleSystemSleep` is the correct level: it blocks the IDLE sleep that was costing us
the cycles, and deliberately does NOT block a lid close or an explicit Sleep — an operator who
closes the machine still gets to close it.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import sys

logger = logging.getLogger("zeus.host_idle_sleep")

# IOPMAssertionCreateWithName levels (IOKit/pwr_mgt/IOPMLib.h).
_ASSERTION_TYPE = "PreventUserIdleSystemSleep"
_ASSERTION_LEVEL_ON = 255
_KCF_STRING_ENCODING_UTF8 = 0x08000100

# Module-level so the assertion id lives exactly as long as the interpreter that owns it.
_assertion_id: ctypes.c_uint32 | None = None


def hold_system_awake(reason: str) -> bool:
    """Prevent host idle sleep for the life of this process. True when the assertion is held.

    Idempotent: a second call while an assertion is already held is a no-op returning True.
    Fail-soft by contract — a daemon must start and serve even where the assertion cannot be
    taken (non-Darwin, or a kernel that refuses), because losing sleep prevention degrades
    freshness while refusing to start loses everything.
    """
    global _assertion_id
    if _assertion_id is not None:
        return True
    if sys.platform != "darwin":
        return False
    try:
        iokit = ctypes.cdll.LoadLibrary(ctypes.util.find_library("IOKit"))
        core = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
        core.CFStringCreateWithCString.restype = ctypes.c_void_p
        core.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        iokit.IOPMAssertionCreateWithName.argtypes = [
            ctypes.c_void_p,  # assertion type
            ctypes.c_uint32,  # level
            ctypes.c_void_p,  # human-readable name
            ctypes.POINTER(ctypes.c_uint32),  # out: assertion id
        ]
        kind = core.CFStringCreateWithCString(
            None, _ASSERTION_TYPE.encode("utf-8"), _KCF_STRING_ENCODING_UTF8
        )
        name = core.CFStringCreateWithCString(
            None, reason.encode("utf-8"), _KCF_STRING_ENCODING_UTF8
        )
        out = ctypes.c_uint32(0)
        status = iokit.IOPMAssertionCreateWithName(kind, _ASSERTION_LEVEL_ON, name, ctypes.byref(out))
    except Exception:
        logger.warning("host idle-sleep assertion unavailable; daemon runs without it", exc_info=True)
        return False
    if status != 0:
        logger.warning("host idle-sleep assertion refused by the kernel (IOReturn=%#x)", status)
        return False
    _assertion_id = out
    logger.info("holding host awake for the life of this process: %s", reason)
    return True

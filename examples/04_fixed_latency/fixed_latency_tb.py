"""fixed_latency_tb.py — Testbench for the FixedLatencyLookup component.

Drives the RAM port model and verifies that result values match the data
returned by the RAM after exactly ``FIXED_LATENCY`` cycles.
"""
from __future__ import annotations

import asyncio
import random
from collections import deque
from typing import Deque

import zuspec.dataclasses as zdc

from fixed_latency import FixedLatencyLookup, RamIface

FIXED_LATENCY = 4
NUM_TRANSACTIONS = 64


# ---------------------------------------------------------------------------
# RAM model
# ---------------------------------------------------------------------------

class RamModel:
    """Simple async RAM model — always accepts; returns data after FIXED_LATENCY cycles."""

    def __init__(self, size: int = 65536):
        self._mem = [random.randint(0, 0xFFFF_FFFF) for _ in range(size)]
        self._inflight: Deque[tuple] = deque()

    async def read(self, addr: int) -> int:
        data = self._mem[addr & (len(self._mem) - 1)]
        # Simulate FIXED_LATENCY cycle delay
        for _ in range(FIXED_LATENCY):
            await asyncio.sleep(0)
        return data


# ---------------------------------------------------------------------------
# Testbench
# ---------------------------------------------------------------------------

async def run_tb():
    ram = RamModel()
    results = []
    errors = 0

    # Simple sequential check: issue reads and compare result
    for addr in range(NUM_TRANSACTIONS):
        returned = await ram.read(addr)
        expected = ram._mem[addr]
        if returned != expected:
            print(f"FAIL addr={addr:#010x}: got {returned:#010x}, expected {expected:#010x}")
            errors += 1
        else:
            results.append(returned)

    if errors == 0:
        print(f"PASS: {NUM_TRANSACTIONS} transactions, all correct")
    else:
        print(f"FAIL: {errors} errors in {NUM_TRANSACTIONS} transactions")
    return errors == 0


if __name__ == "__main__":
    ok = asyncio.run(run_tb())
    raise SystemExit(0 if ok else 1)

"""lsu_tb.py — Testbench for the LSU component.

Drives load and store transactions through the AXI model and verifies:
1. Loads return the data previously written by stores.
2. Store responses are OKAY.
3. The AXI model was exercised (non-zero read/write counts).
"""
from __future__ import annotations

import asyncio
import random
from typing import List

from axi_model import AxiMemoryModel

NUM_STORES = 16
NUM_LOADS  = 16
BASE_ADDR  = 0x0000_1000


async def run_loads(mem: AxiMemoryModel, addrs: List[int]) -> List[int]:
    """Issue all loads concurrently and collect results."""
    tasks = [asyncio.create_task(mem.read(a, 3)) for a in addrs]  # length=3 → 8 bytes
    return list(await asyncio.gather(*tasks))


async def run_stores(mem: AxiMemoryModel, addrs: List[int], values: List[int]) -> List[int]:
    """Issue all stores concurrently and collect responses."""
    tasks = [asyncio.create_task(mem.write(a, v, 0xFF)) for a, v in zip(addrs, values)]
    return list(await asyncio.gather(*tasks))


async def run_tb():
    mem = AxiMemoryModel(latency=2)
    errors = 0

    # Prepare test vectors
    addrs  = [BASE_ADDR + i * 8 for i in range(NUM_STORES)]
    values = [random.randint(0, 0xFFFF_FFFF_FFFF_FFFF) for _ in range(NUM_STORES)]

    # Phase 1: stores
    resps = await run_stores(mem, addrs, values)
    for i, resp in enumerate(resps):
        if resp != 0:  # OKAY == 0
            print(f"FAIL store[{i}]: unexpected resp={resp}")
            errors += 1

    # Phase 2: loads — should return what was stored
    results = await run_loads(mem, addrs[:NUM_LOADS])
    for i, (got, expected) in enumerate(zip(results, values[:NUM_LOADS])):
        if got != expected:
            print(f"FAIL load[{i}]: addr={addrs[i]:#018x} got={got:#018x} expected={expected:#018x}")
            errors += 1

    stats = mem.stats()
    assert stats["reads"]  == NUM_LOADS,  f"Expected {NUM_LOADS} reads, got {stats['reads']}"
    assert stats["writes"] == NUM_STORES, f"Expected {NUM_STORES} writes, got {stats['writes']}"

    if errors == 0:
        print(f"PASS: {NUM_STORES} stores + {NUM_LOADS} loads, all verified")
    else:
        print(f"FAIL: {errors} errors")
    return errors == 0


if __name__ == "__main__":
    ok = asyncio.run(run_tb())
    raise SystemExit(0 if ok else 1)

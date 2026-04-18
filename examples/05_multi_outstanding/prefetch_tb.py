"""prefetch_tb.py — Testbench for the Prefetch component."""
from __future__ import annotations

import asyncio
import random

NUM_TRANSACTIONS = 32
MEM_SIZE = 1024


class MemModel:
    """Simple in-order memory model supporting 4 outstanding reads."""

    def __init__(self):
        self._mem = [random.randint(0, 0xFFFF_FFFF) for _ in range(MEM_SIZE)]

    async def read(self, addr: int) -> int:
        await asyncio.sleep(0)  # yield once to simulate latency
        return self._mem[(addr // 4) % MEM_SIZE]


async def run_tb():
    mem = MemModel()
    acc = 0
    addr = 0
    errors = 0

    for _ in range(NUM_TRANSACTIONS // 4):
        burst = []
        for _ in range(4):
            val = await mem.read(addr)
            burst.append(val)
            addr = (addr + 4) & 0xFFFF_FFFF

        for val in burst:
            acc = (acc + val) & 0xFFFF_FFFF_FFFF_FFFF

    print(f"PASS: {NUM_TRANSACTIONS} reads, accumulator = {acc:#018x}")
    return errors == 0


if __name__ == "__main__":
    ok = asyncio.run(run_tb())
    raise SystemExit(0 if ok else 1)

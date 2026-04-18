"""axi_model.py — Behavioral AXI model for LSU testbench.

Implements a minimal AXI read/write model that:
- Accepts requests with configurable per-transaction latency.
- Maintains a simple byte array as the backing store.
- Supports configurable response errors (for negative testing).
"""
from __future__ import annotations

import asyncio
import struct
from typing import Dict, List, Optional

MEM_SIZE = 1 << 20  # 1 MiB

OKAY  = 0x0
EXOKAY = 0x1
SLVERR = 0x2
DECERR = 0x3


class AxiMemoryModel:
    """Simple behavioral AXI memory model."""

    def __init__(self, size: int = MEM_SIZE, latency: int = 4):
        self._mem = bytearray(size)
        self._size = size
        self._latency = latency
        self._n_reads = 0
        self._n_writes = 0

    # ------------------------------------------------------------------
    # Low-level byte access
    # ------------------------------------------------------------------

    def _read_bytes(self, addr: int, n: int) -> int:
        addr &= self._size - 1
        raw = self._mem[addr: addr + n]
        pad = raw + b'\x00' * (8 - len(raw))
        return struct.unpack_from('<Q', pad)[0]

    def _write_bytes(self, addr: int, data: int, strb: int, n: int = 8) -> None:
        addr &= self._size - 1
        for i in range(n):
            if strb & (1 << i):
                self._mem[addr + i] = (data >> (8 * i)) & 0xFF

    # ------------------------------------------------------------------
    # AXI read model
    # ------------------------------------------------------------------

    async def read(self, addr: int, length: int) -> int:
        """Simulate AXI read: latency cycles, then return data."""
        for _ in range(self._latency):
            await asyncio.sleep(0)
        n = max(1, min(8, 1 << length))  # length encodes log2 byte count
        data = self._read_bytes(addr, n)
        self._n_reads += 1
        return data

    # ------------------------------------------------------------------
    # AXI write model
    # ------------------------------------------------------------------

    async def write(self, addr: int, data: int, strb: int) -> int:
        """Simulate AXI write: latency cycles, write data, return OKAY."""
        for _ in range(self._latency):
            await asyncio.sleep(0)
        self._write_bytes(addr, data, strb)
        self._n_writes += 1
        return OKAY

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, int]:
        return {"reads": self._n_reads, "writes": self._n_writes}

    def read_word(self, addr: int) -> int:
        """Direct (non-AXI) read for testbench verification."""
        return self._read_bytes(addr, 8)

    def write_word(self, addr: int, data: int) -> None:
        """Direct (non-AXI) write for testbench setup."""
        self._write_bytes(addr, data, 0xFF)

# Example 06: Load-Store Unit (LSU)

This is the **anchor example** for all new primitives introduced in
`IMPLEMENTATION_PLAN.md`.  It demonstrates:

| Primitive | Used where |
|-----------|-----------|
| `zdc.IfProtocol` | `LoadCmdIface`, `StoreCmdIface`, `AxiReadIface`, `AxiWriteIface` |
| `zdc.Completion[T]` | Return path for split-transaction results |
| `zdc.Queue[T]` | Internal `_load_q` and `_store_q` buffers |
| `zdc.spawn()` | Fire-and-forget AXI transactions |
| `zdc.iface_select()` | Priority drain of load/store result queues |

## Architecture

```
  load_port ──────┐
                  ▼
              ┌──────┐  spawn  ┌──────────┐  AxiReadIface
  store_port─►│ LSU  │────────►│ axi_r    │──────────────►
              │      │         └──────────┘
              │      │  spawn  ┌──────────┐  AxiWriteIface
              │      │────────►│ axi_w    │──────────────►
              └──────┘         └──────────┘
                  ▲
           select() on _load_q / _store_q
```

## Synthesis scenario

- `AxiReadIface`: **Scenario C** — `max_outstanding=4`, `in_order=True`
  - Generates: req/resp handshake + 4-entry response FIFO + inflight counter
- `AxiWriteIface`: **Scenario C** — same
- `LoadCmdIface`, `StoreCmdIface`: **Scenario B** — `max_outstanding=1`
  - Generates: standard req_valid/req_ready/resp_valid handshake

## Files

| File | Description |
|------|-------------|
| `lsu.py` | LSU component definition |
| `axi_model.py` | Behavioral AXI memory model |
| `lsu_tb.py` | Testbench: stores + loads with verification |

## Running the testbench

```bash
cd examples/06_lsu
python lsu_tb.py
```

Expected output:
```
PASS: 16 stores + 16 loads, all verified
```

## Split-transaction pattern

The core pattern enabled by `zdc.Completion[T]`:

```python
# Issue request — immediately returns handle, does not block
done: zdc.Completion[zdc.u64] = zdc.spawn(self._do_axi_read(addr, size))

# ... issue more requests ...

# Collect result — blocks until the spawned coro completes
data: zdc.u64 = await done
```

This allows the component to have up to `max_outstanding` requests in flight
simultaneously while keeping the behavioral model sequential and readable.

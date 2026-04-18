# Example 04: Fixed-Latency RAM Lookup

This example demonstrates the simplest IfProtocol configuration:
`req_always_ready=True` and `resp_always_valid=True` with a `fixed_latency`.

## What it shows

| Protocol property    | Value | Effect on SV |
|----------------------|-------|--------------|
| `req_always_ready`   | True  | No `req_ready` port generated |
| `resp_always_valid`  | True  | No `resp_valid` port generated |
| `fixed_latency`      | 4     | Data appears exactly 4 cycles after address |
| `max_outstanding`    | 1     | Scenario A: minimal signal count |

The synthesized SV for `FixedLatencyLookup` has only:
- `ram_req_valid` — output
- `ram_req_addr[31:0]` — output
- `ram_resp_data[31:0]` — input

No handshake ports are generated.

## Files

| File | Description |
|------|-------------|
| `fixed_latency.py` | Component definition |
| `fixed_latency_tb.py` | Python testbench (behavioral simulation) |

## Running the testbench

```bash
python fixed_latency_tb.py
```

## Synthesis scenario

This is **Scenario A** — the lowest-overhead IfProtocol configuration:

```
Initiator                Provider (RAM)
─────────────────────────────────────────
  addr ────────────────► addr_in
                            │ (4-cycle shift register)
  data ◄────────────────── data_out
```

The synthesis pass emits a simple 4-entry shift register for the data path
rather than a full request/response FSM.

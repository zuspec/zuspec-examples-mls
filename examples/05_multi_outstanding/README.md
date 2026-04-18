# Example 05: Multi-Outstanding Prefetch Buffer

This example demonstrates `max_outstanding=4, in_order=True` — **Scenario C**.

## What it shows

| Protocol property  | Value | Effect on SV |
|--------------------|-------|--------------|
| `max_outstanding`  | 4     | Up to 4 reads in flight |
| `in_order`         | True  | Responses arrive in request order |
| `req_always_ready` | False | Full req handshake |
| `resp_always_valid`| False | Full resp handshake |

The synthesized SV adds to Scenario B:
- An inflight counter register (4-bit)
- A 4-entry response FIFO to buffer ahead-of-time responses
- Back-pressure: stop issuing if inflight == 4

## Files

| File | Description |
|------|-------------|
| `prefetch.py` | Component definition |
| `prefetch_tb.py` | Python testbench |

## Running

```bash
python prefetch_tb.py
```

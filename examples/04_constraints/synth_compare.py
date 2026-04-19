#!/usr/bin/env python3
"""Staged synthesis comparison for the RV32I constraint-based decoder.

Runs four synthesis variants and prints a side-by-side cell-count table:

  1. Hand-written if/else RTL              (expected/rv32i_decode_manual.sv)
  2. Hand-written unique casez / parallel  (expected/rv32i_decode_unique.sv)
  3. Constraint-driven, no hints           (pure SOP, no ODC annotations)
  4. Constraint-driven, with hints         (ODC via zdc.valid() annotations)

Expected ordering (best to worst area): ODC < Constraint SOP < Manual RTL < Unique casez.

The unique-casez variant is intentionally included to show a counter-intuitive result:
flattening all nested case statements into a single casez on an 11-bit key with ?
wildcards is *worse* than the naive nested baseline.  (* parallel_case *) eliminates
the priority-MUX chain, but each case arm still requires a masked-equality comparator
(~40 comparators × 11-bit compare + AND mask), all OR'd together.  The nested case
hierarchy shares the 7-bit opcode decode, which is more efficient.

Constraint SOP wins because it emits minimised flat Boolean expressions with no
pattern-matching overhead and no priority encoding at all.

The synthesis pipeline lives entirely in ``zuspec.synth``; this script is
a thin driver that selects variants via :class:`zuspec.synth.ActionSynthConfig`.

Usage
-----
    python synth_compare.py [--write-sv] [--no-synth]

Options
-------
    --write-sv   Also save the annotated SV to expected/rv32i_decode.sv
    --no-synth   Only generate SV for the annotated variant; skip yosys

Requirements
------------
    pip install yowasp-yosys
    pip install zuspec-synth zuspec-dataclasses  (or use ivpm / editable install)
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path setup — allow running from the example directory or from repo root.
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent.resolve()
_REPO = _HERE.parent.parent

for _p in [
    _REPO / "packages/zuspec-dataclasses/src",
    _REPO / "packages/zuspec-synth/src",
    _HERE,
]:
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

# ---------------------------------------------------------------------------
# Imports — the synthesis API lives in zuspec.synth
# ---------------------------------------------------------------------------
from zuspec.synth import ActionSynthConfig, synth_action

from rv32i_decode import RV32IDecode


# ---------------------------------------------------------------------------
# Yosys synthesis helpers
# ---------------------------------------------------------------------------

_YOSYS_CMD = "yowasp-yosys"

# proc; opt; memory_map; opt; techmap; opt decomposes any ROM inference so
# both flat-SOP and case-statement inputs are compared fairly.
_SYNTH_SCRIPT_TEMPLATE = (
    "read_verilog -sv {sv_file}; "
    "hierarchy -check -top {top}; "
    "proc; opt; memory_map; opt; techmap; opt; stat"
)


def _find_yosys() -> Optional[str]:
    for cmd in (_YOSYS_CMD, "yosys"):
        try:
            subprocess.run([cmd, "--version"], capture_output=True, check=True)
            return cmd
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
    return None


def run_yosys(sv_text: str, top_module: str, yosys_cmd: str) -> Dict:
    """Synthesize *sv_text* and return a metrics dict (cells, wires, cell_types)."""
    # yowasp-yosys maps CWD into its WASM filesystem; write SV alongside CWD.
    cwd_sv = f"_synth_tmp_{top_module}.sv"
    with open(cwd_sv, "w") as f:
        f.write(sv_text)
    try:
        cmd = _SYNTH_SCRIPT_TEMPLATE.format(sv_file=cwd_sv, top=top_module)
        result = subprocess.run(
            [yosys_cmd, "-p", cmd],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    finally:
        if os.path.exists(cwd_sv):
            os.unlink(cwd_sv)
    return _parse_stat(result.stdout + result.stderr)


def _parse_stat(output: str) -> Dict:
    metrics: Dict = {}
    m = re.search(r"^\s+(\d+) cells\s*$", output, re.MULTILINE)
    if m:
        metrics["cells"] = int(m.group(1))
    m = re.search(r"^\s+(\d+) wires\s*$", output, re.MULTILINE)
    if m:
        metrics["wires"] = int(m.group(1))
    m = re.search(r"^\s+(\d+) wire bits\s*$", output, re.MULTILINE)
    if m:
        metrics["wire_bits"] = int(m.group(1))
    cell_types: Dict[str, int] = {}
    for m in re.finditer(r"^\s+(\d+)\s+\$(\w+)\s*$", output, re.MULTILINE):
        cell_types[m.group(2)] = int(m.group(1))
    if cell_types:
        metrics["cell_types"] = cell_types
    return metrics


# ---------------------------------------------------------------------------
# Comparison table
# ---------------------------------------------------------------------------

def print_staged_comparison(variants: List[Tuple[str, Dict]]) -> None:
    """Print a side-by-side table for *variants* = [(label, metrics), ...]."""
    col = 16
    sep = "=" * (24 + col * len(variants))
    print()
    print(sep)
    print("  RV32I Decode — Staged Synthesis Comparison")
    print(sep)
    header = f"  {'Metric':<20}"
    for label, _ in variants:
        header += f"  {label:>{col}}"
    print(header)
    print("-" * (24 + col * len(variants)))

    for key, label in [("cells", "Total cells"), ("wires", "Wires"), ("wire_bits", "Wire bits")]:
        row = f"  {label:<20}"
        # Use Manual RTL (first column) as the 100% reference
        ref = next((m.get(key) for lbl, m in variants if "Manual" in lbl), None)
        for _, m in variants:
            v = m.get(key, "—")
            cell = str(v)
            if ref and isinstance(v, int) and isinstance(ref, int) and ref > 0 and "cells" in key:
                cell = f"{v} ({100*v//ref}%)"
            row += f"  {cell:>{col}}"
        print(row)

    print(sep)
    print()

    # Cell-type breakdown
    all_types = sorted(set(t for _, m in variants for t in m.get("cell_types", {}).keys()))
    if all_types:
        print("  Cell-type breakdown:")
        hdr = f"  {'Type':<20}"
        for label, _ in variants:
            hdr += f"  {label:>{col}}"
        print(hdr)
        print("-" * (24 + col * len(variants)))
        for t in all_types:
            row = f"  ${t:<19}"
            for _, m in variants:
                v = m.get("cell_types", {}).get(t, 0)
                row += f"  {v:>{col}}"
            print(row)
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write-sv", action="store_true",
                        help="Write annotated SV to expected/rv32i_decode.sv")
    parser.add_argument("--no-synth", action="store_true",
                        help="Only generate SV for the annotated variant; skip yosys")
    args = parser.parse_args()

    # --- Generate the three SV variants via the zuspec.synth API --------
    print("Generating SV variants...")

    # Variant 2: constraint-driven, no hints (ODC disabled)
    sv_no_hints = synth_action(
        RV32IDecode,
        ActionSynthConfig(odc=False, top="rv32i_decode"),
    )
    print(f"  No-hints:    {len(sv_no_hints.splitlines())} lines")

    # Variant 3: constraint-driven, with hints (ODC enabled, full annotations)
    sv_hints = synth_action(
        RV32IDecode,
        ActionSynthConfig(odc=True, top="rv32i_decode"),
        output=str(_HERE / "expected" / "rv32i_decode.sv") if args.write_sv else None,
    )
    print(f"  With hints:  {len(sv_hints.splitlines())} lines")
    if args.write_sv:
        print(f"  Wrote expected/rv32i_decode.sv")

    if args.no_synth:
        print(sv_hints)
        return

    # --- Locate yosys ----------------------------------------------------
    yosys_cmd = _find_yosys()
    if yosys_cmd is None:
        print("ERROR: yowasp-yosys not found.  Install with: pip install yowasp-yosys")
        sys.exit(1)
    print(f"Using yosys: {yosys_cmd}")

    # Variant 1: hand-written if/else RTL
    manual_sv_path = _HERE / "expected" / "rv32i_decode_manual.sv"
    if not manual_sv_path.exists():
        print(f"ERROR: manual SV not found at {manual_sv_path}")
        sys.exit(1)
    manual_sv = manual_sv_path.read_text()

    # Variant 2b: hand-written unique casez / parallel_case
    unique_sv_path = _HERE / "expected" / "rv32i_decode_unique.sv"
    if not unique_sv_path.exists():
        print(f"ERROR: unique-case SV not found at {unique_sv_path}")
        sys.exit(1)
    unique_sv = unique_sv_path.read_text()

    # --- Synthesize all four ---------------------------------------------
    variants_sv = [
        ("Manual RTL",        manual_sv,    "rv32i_decode_manual"),
        ("Unique casez",      unique_sv,    "rv32i_decode_unique"),
        ("Constraint SOP",    sv_no_hints,  "rv32i_decode"),
        ("+ ODC hints",       sv_hints,     "rv32i_decode"),
    ]

    results: List[Tuple[str, Dict]] = []
    for label, sv, top in variants_sv:
        print(f"Synthesizing '{label}'...")
        m = run_yosys(sv, top, yosys_cmd)
        print(f"  cells={m.get('cells', '?')}, wires={m.get('wires', '?')}")
        results.append((label, m))

    print_staged_comparison(results)

    if any("error" in m for _, m in results):
        sys.exit(1)


if __name__ == "__main__":
    main()


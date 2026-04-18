#!/usr/bin/env python3
"""Synthesis comparison: constraint-generated decode vs. hand-written decode.

Generates a complete SystemVerilog module for the constraint-based RV32I
decoder via ConstraintCompiler, then invokes yowasp-yosys to synthesize both
the constraint version and the hand-written baseline.  Prints a side-by-side
comparison table of cell count and logic depth.

Usage
-----
    python synth_compare.py [--write-sv]

Options
-------
    --write-sv   Also save the generated SV to expected/rv32i_decode.sv

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
# Imports
# ---------------------------------------------------------------------------
import dataclasses

import zuspec.dataclasses as zdc
from zuspec.synth.sprtl.constraint_compiler import ConstraintCompiler

from rv32i_decode import RV32IDecode


# ---------------------------------------------------------------------------
# Port lists for the module declaration (must match manual SV).
# ---------------------------------------------------------------------------
_INPUT_PORTS = [("instr", 32)]

# Derived (internal) fields — emitted as wires inside the module, not ports.
_DERIVED_FIELDS = {"opcode", "funct3", "funct7b5"}

# True decode outputs — become module output ports.
_OUTPUT_PORTS: List[Tuple[str, int]] = []
for _f in dataclasses.fields(RV32IDecode):
    _meta = _f.metadata
    if _meta.get("rand") or _meta.get("randc"):
        if _f.name not in _DERIVED_FIELDS:
            ann = RV32IDecode.__annotations__.get(_f.name)
            w = 1
            if ann is not None and hasattr(ann, "__metadata__") and ann.__metadata__:
                w = getattr(ann.__metadata__[0], "width", 1)
            _OUTPUT_PORTS.append((_f.name, w))


# ---------------------------------------------------------------------------
# Module wrapper
# ---------------------------------------------------------------------------

def _port_decl(ports_in: List[Tuple[str, int]], ports_out: List[Tuple[str, int]]) -> str:
    """Return the module port list string."""
    lines: List[str] = []
    for name, w in ports_in:
        if w == 1:
            lines.append(f"    input  logic        {name}")
        else:
            lines.append(f"    input  logic [{w-1}:0]  {name}")
    for name, w in ports_out:
        if w == 1:
            lines.append(f"    output logic        {name}")
        else:
            lines.append(f"    output logic [{w-1}:0]  {name}")
    return ",\n".join(lines)


def generate_constraint_sv(write_sv: bool = False) -> str:
    """Run ConstraintCompiler and wrap output in a SV module.

    Returns the full SV text.
    """
    cc = ConstraintCompiler(RV32IDecode, prefix="")
    cc.extract()
    cc.compute_support()
    cc.validate(warn_only=True)
    cc.build_cubes()
    cc.minimize()
    body_lines = cc.emit_sv()

    # Build the module.
    sv_lines: List[str] = []
    sv_lines.append("// rv32i_decode — constraint-synthesized by ConstraintCompiler")
    sv_lines.append("// Do not edit: regenerate with synth_compare.py")
    sv_lines.append("")
    sv_lines.append("module rv32i_decode (")
    sv_lines.append(_port_decl(_INPUT_PORTS, _OUTPUT_PORTS))
    sv_lines.append(");")
    sv_lines.append("")

    # Emit ConstraintCompiler body.
    for line in body_lines:
        if line.startswith("//"):
            sv_lines.append(f"    {line}")
        else:
            sv_lines.append(f"    {line}")

    # Connect internal wires (prefixed with '_') to output ports.
    sv_lines.append("")
    sv_lines.append("    // Connect internal wires to output ports.")
    for name, w in _OUTPUT_PORTS:
        sv_lines.append(f"    assign {name} = _{name};")

    sv_lines.append("")
    sv_lines.append("endmodule")
    sv_lines.append("")

    sv_text = "\n".join(sv_lines)

    if write_sv:
        out_path = _HERE / "expected" / "rv32i_decode.sv"
        out_path.parent.mkdir(exist_ok=True)
        out_path.write_text(sv_text)
        print(f"Wrote {out_path}")

    return sv_text


# ---------------------------------------------------------------------------
# Yosys synthesis
# ---------------------------------------------------------------------------

_YOSYS_CMD = "yowasp-yosys"

# Use proc; opt; memory_map; opt; techmap; opt to ensure any ROM inference (from
# always_comb case with constant RHS) is decomposed before techmap.  This gives
# a fair apples-to-apples gate count for both flat-SOP and case-statement inputs.
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


def run_yosys(sv_text: str, top_module: str, yosys_cmd: str, sv_path: Optional[str] = None) -> Dict[str, int]:
    """Synthesize sv_text and return a dict of metrics.

    Returns keys: cells, wires, wire_bits, processes, cell_types.

    yowasp-yosys uses a WASM virtual filesystem.  We pass the SV filename on
    the command line (relative to cwd, which maps to the virtual root) and use
    the -p flag for inline commands rather than a script file (which may not be
    visible in the WASM VFS).
    """
    with tempfile.TemporaryDirectory() as tmp:
        # Write SV to cwd-relative path so yowasp can see it.
        local_sv = os.path.join(tmp, f"{top_module}.sv")
        with open(local_sv, "w") as f:
            f.write(sv_text)

        # Copy to a path relative to the current working directory so that
        # yowasp-yosys (which maps CWD into its WASM filesystem) can read it.
        cwd_sv = f"_synth_tmp_{top_module}.sv"
        with open(cwd_sv, "w") as f:
            f.write(sv_text)

        cmd = _SYNTH_SCRIPT_TEMPLATE.format(sv_file=cwd_sv, top=top_module)
        try:
            result = subprocess.run(
                [yosys_cmd, "-p", cmd],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return {"error": "timeout"}
        finally:
            if os.path.exists(cwd_sv):
                os.unlink(cwd_sv)

        output = result.stdout + result.stderr
        return _parse_stat(output)


def _parse_stat(output: str) -> Dict[str, int]:
    """Parse yosys 'stat' block for cell/wire/process counts.

    Yosys stat format (techmap flow):
        75 wires
       134 wire bits
        32 cells
        23   $_AND_
         5   $_NOT_
         4   $_OR_
    """
    metrics: Dict[str, int] = {}

    # Total cells line: "      32 cells"
    m = re.search(r"^\s+(\d+) cells\s*$", output, re.MULTILINE)
    if m:
        metrics["cells"] = int(m.group(1))

    # Wires: "      75 wires"
    m = re.search(r"^\s+(\d+) wires\s*$", output, re.MULTILINE)
    if m:
        metrics["wires"] = int(m.group(1))

    # Wire bits: "     134 wire bits"
    m = re.search(r"^\s+(\d+) wire bits\s*$", output, re.MULTILINE)
    if m:
        metrics["wire_bits"] = int(m.group(1))

    # Per-cell-type breakdown: "      23   $_AND_"
    cell_types: Dict[str, int] = {}
    for m in re.finditer(r"^\s+(\d+)\s+\$(\w+)\s*$", output, re.MULTILINE):
        cell_types[m.group(2)] = int(m.group(1))
    if cell_types:
        metrics["cell_types"] = cell_types  # type: ignore[assignment]

    return metrics


# ---------------------------------------------------------------------------
# Comparison table
# ---------------------------------------------------------------------------

def print_comparison(
    constraint_metrics: Dict[str, int],
    manual_metrics: Dict[str, int],
) -> None:
    """Print side-by-side comparison."""
    print()
    print("=" * 62)
    print("  RV32I Decode Synthesis Comparison")
    print("=" * 62)
    print(f"  {'Metric':<20}  {'Constraint':>14}  {'Manual':>14}")
    print("-" * 62)

    keys = [("cells", "Total cells"), ("wires", "Wires"), ("wire_bits", "Wire bits")]
    for key, label in keys:
        cv = constraint_metrics.get(key, "—")
        mv = manual_metrics.get(key, "—")
        if isinstance(cv, int) and isinstance(mv, int) and mv > 0:
            pct = int(100 * cv / mv)
            note = f"  ({pct}%)"
        else:
            note = ""
        print(f"  {label:<20}  {str(cv):>14}  {str(mv):>14}{note}")

    print("=" * 62)
    print()

    # Cell-type breakdown if available.
    c_types = constraint_metrics.get("cell_types", {})
    m_types = manual_metrics.get("cell_types", {})
    all_types = sorted(set(list(c_types.keys()) + list(m_types.keys())))
    if all_types:
        print("  Cell-type breakdown:")
        print(f"  {'Type':<20}  {'Constraint':>14}  {'Manual':>14}")
        print("-" * 62)
        for t in all_types:
            cv = c_types.get(t, 0)
            mv = m_types.get(t, 0)
            print(f"  ${t:<19}  {cv:>14}  {mv:>14}")
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write-sv", action="store_true", help="Write generated SV to expected/rv32i_decode.sv")
    parser.add_argument("--no-synth", action="store_true", help="Only generate SV, skip yosys synthesis")
    args = parser.parse_args()

    print("Generating constraint-based SV...")
    constraint_sv = generate_constraint_sv(write_sv=args.write_sv)
    print(f"  {len(constraint_sv.splitlines())} lines generated")

    if args.no_synth:
        print(constraint_sv)
        return

    yosys_cmd = _find_yosys()
    if yosys_cmd is None:
        print("ERROR: yowasp-yosys not found.  Install with: pip install yowasp-yosys")
        sys.exit(1)

    print(f"Using yosys: {yosys_cmd}")

    # Load manual SV.
    manual_sv_path = _HERE / "expected" / "rv32i_decode_manual.sv"
    if not manual_sv_path.exists():
        print(f"ERROR: manual SV not found at {manual_sv_path}")
        sys.exit(1)
    manual_sv = manual_sv_path.read_text()

    print("Synthesizing constraint version...")
    c_metrics = run_yosys(constraint_sv, "rv32i_decode", yosys_cmd)
    print(f"  cells={c_metrics.get('cells', '?')}, wires={c_metrics.get('wires', '?')}")

    print("Synthesizing manual version...")
    m_metrics = run_yosys(manual_sv, "rv32i_decode_manual", yosys_cmd)
    print(f"  cells={m_metrics.get('cells', '?')}, wires={m_metrics.get('wires', '?')}")

    print_comparison(c_metrics, m_metrics)

    # Exit with failure if synthesis produced errors.
    if "error" in c_metrics or "error" in m_metrics:
        sys.exit(1)


if __name__ == "__main__":
    main()

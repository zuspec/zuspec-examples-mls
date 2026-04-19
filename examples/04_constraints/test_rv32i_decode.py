"""Simulation tests for RV32IDecode — constraint-block evaluation.

Each test encodes a real RV32I instruction, evaluates the constraint blocks
against the encoded bit pattern, and verifies the decode outputs.

The evaluator is pure Python — it finds the constraint block whose conditions
all match the input word, then checks the expected assignments.  This is
equivalent to simulating the synthesized combinational logic without
needing an RTL simulator.

Run with:
    pytest test_rv32i_decode.py -v
"""
import sys
import os
from pathlib import Path
from typing import Dict, Optional

import pytest

# ---------------------------------------------------------------------------
# Path setup
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
from rv32i_decode import (
    RV32IDecode,
    ALU_ADD, ALU_SUB, ALU_SLL, ALU_SLT, ALU_SLTU,
    ALU_XOR, ALU_SRL, ALU_SRA, ALU_OR, ALU_AND, ALU_PASS,
    IMM_NONE, IMM_I, IMM_S, IMM_B, IMM_U, IMM_J,
)
from zuspec.synth.sprtl.constraint_compiler import ConstraintCompiler
from zuspec.synth.ir.constraint_ir import BitRange, ConstraintBlock


# ---------------------------------------------------------------------------
# Evaluator — applies constraint blocks to a 32-bit instruction word
# ---------------------------------------------------------------------------

class ConstraintEvaluator:
    """Evaluate RV32IDecode constraint blocks against a concrete instruction word.

    Usage::

        ev = ConstraintEvaluator(RV32IDecode)
        result = ev.decode(0x00000033)   # ADD
        assert result['alu_op'] == ALU_ADD
    """

    def __init__(self, cls):
        cc = ConstraintCompiler(cls, prefix="")
        cc.extract()
        self._blocks: list[ConstraintBlock] = cc.cset.constraints
        self._input_field = cc.cset.input_field

    @staticmethod
    def _extract_bits(word: int, br: BitRange) -> int:
        mask = (1 << br.width()) - 1
        return (word >> br.lsb) & mask

    def _block_fires(self, word: int, block: ConstraintBlock) -> bool:
        """Return True if all conditions of block match the instruction word."""
        for br, expected in block.conditions.items():
            if self._extract_bits(word, br) != expected:
                return False
        return True

    def decode(self, instr: int) -> Optional[Dict[str, int]]:
        """Return the assignments from the first matching block, or None."""
        for block in self._blocks:
            if self._block_fires(instr, block):
                return dict(block.assignments)
        return None


# ---------------------------------------------------------------------------
# Module-level evaluator (shared across all tests)
# ---------------------------------------------------------------------------
_EV = ConstraintEvaluator(RV32IDecode)


# ---------------------------------------------------------------------------
# Instruction encoding helpers
# ---------------------------------------------------------------------------

def _r_type(funct7: int, rs2: int, rs1: int, funct3: int, rd: int, opcode: int) -> int:
    return ((funct7 & 0x7F) << 25 | (rs2 & 0x1F) << 20 | (rs1 & 0x1F) << 15
            | (funct3 & 0x7) << 12 | (rd & 0x1F) << 7 | (opcode & 0x7F))


def _i_type(imm12: int, rs1: int, funct3: int, rd: int, opcode: int) -> int:
    return ((imm12 & 0xFFF) << 20 | (rs1 & 0x1F) << 15
            | (funct3 & 0x7) << 12 | (rd & 0x1F) << 7 | (opcode & 0x7F))


def _s_type(imm12: int, rs2: int, rs1: int, funct3: int, opcode: int) -> int:
    hi = (imm12 >> 5) & 0x7F
    lo = imm12 & 0x1F
    return (hi << 25 | (rs2 & 0x1F) << 20 | (rs1 & 0x1F) << 15
            | (funct3 & 0x7) << 12 | lo << 7 | (opcode & 0x7F))


def _b_type(imm13: int, rs2: int, rs1: int, funct3: int, opcode: int) -> int:
    b12 = (imm13 >> 12) & 1
    b11 = (imm13 >> 11) & 1
    b10_5 = (imm13 >> 5) & 0x3F
    b4_1 = (imm13 >> 1) & 0xF
    return (b12 << 31 | b10_5 << 25 | (rs2 & 0x1F) << 20 | (rs1 & 0x1F) << 15
            | (funct3 & 0x7) << 12 | b4_1 << 8 | b11 << 7 | (opcode & 0x7F))


def _u_type(imm20: int, rd: int, opcode: int) -> int:
    return ((imm20 & 0xFFFFF) << 12 | (rd & 0x1F) << 7 | (opcode & 0x7F))


def _j_type(imm21: int, rd: int, opcode: int) -> int:
    b20   = (imm21 >> 20) & 1
    b10_1 = (imm21 >> 1)  & 0x3FF
    b11   = (imm21 >> 11) & 1
    b19_12 = (imm21 >> 12) & 0xFF
    return (b20 << 31 | b19_12 << 12 | b11 << 20 | b10_1 << 21
            | (rd & 0x1F) << 7 | (opcode & 0x7F))


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestRTypeInstructions:
    """R-type: opcode=0x33, rd/rs1/rs2 all used, no immediate."""

    @pytest.mark.parametrize("name,funct7,funct3,alu", [
        ("ADD",  0x00, 0, ALU_ADD),
        ("SUB",  0x20, 0, ALU_SUB),
        ("SLL",  0x00, 1, ALU_SLL),
        ("SLT",  0x00, 2, ALU_SLT),
        ("SLTU", 0x00, 3, ALU_SLTU),
        ("XOR",  0x00, 4, ALU_XOR),
        ("SRL",  0x00, 5, ALU_SRL),
        ("SRA",  0x20, 5, ALU_SRA),
        ("OR",   0x00, 6, ALU_OR),
        ("AND",  0x00, 7, ALU_AND),
    ])
    def test_r_type(self, name, funct7, funct3, alu):
        instr = _r_type(funct7, rs2=1, rs1=2, funct3=funct3, rd=3, opcode=0x33)
        result = _EV.decode(instr)
        assert result is not None, f"{name}: no block matched instr={instr:#010x}"
        assert result["alu_op"]    == alu,      f"{name}: alu_op"
        assert result["imm_sel"]   == IMM_NONE, f"{name}: imm_sel"
        assert result["use_rs1"]   == 1,        f"{name}: use_rs1"
        assert result["use_rs2"]   == 1,        f"{name}: use_rs2"
        assert result["use_rd"]    == 1,        f"{name}: use_rd"
        assert result["is_load"]   == 0,        f"{name}: is_load"
        assert result["is_store"]  == 0,        f"{name}: is_store"
        assert result["is_branch"] == 0,        f"{name}: is_branch"
        assert result["is_jal"]    == 0,        f"{name}: is_jal"
        assert result["is_jalr"]   == 0,        f"{name}: is_jalr"


class TestITypeALUInstructions:
    """I-type ALU: opcode=0x13, rs1 + immediate → rd."""

    @pytest.mark.parametrize("name,funct7b5,funct3,alu", [
        ("ADDI",  0, 0, ALU_ADD),
        ("SLTI",  0, 2, ALU_SLT),
        ("SLTIU", 0, 3, ALU_SLTU),
        ("XORI",  0, 4, ALU_XOR),
        ("ORI",   0, 6, ALU_OR),
        ("ANDI",  0, 7, ALU_AND),
        ("SLLI",  0, 1, ALU_SLL),
        ("SRLI",  0, 5, ALU_SRL),
        ("SRAI",  1, 5, ALU_SRA),
    ])
    def test_i_alu(self, name, funct7b5, funct3, alu):
        # Shift immediates use funct7b5 in imm[10] (bit 30 of instr).
        imm = funct7b5 << 10
        instr = _i_type(imm, rs1=1, funct3=funct3, rd=2, opcode=0x13)
        result = _EV.decode(instr)
        assert result is not None, f"{name}: no block matched instr={instr:#010x}"
        assert result["alu_op"]    == alu,   f"{name}: alu_op"
        assert result["imm_sel"]   == IMM_I, f"{name}: imm_sel"
        assert result["use_rs1"]   == 1,     f"{name}: use_rs1"
        assert result["use_rs2"]   == 0,     f"{name}: use_rs2"
        assert result["use_rd"]    == 1,     f"{name}: use_rd"
        assert result["is_load"]   == 0,     f"{name}: is_load"
        assert result["is_store"]  == 0,     f"{name}: is_store"
        assert result["is_branch"] == 0,     f"{name}: is_branch"


class TestLoadInstructions:
    """Load: opcode=0x03."""

    @pytest.mark.parametrize("name,funct3,mem_width,mem_signed", [
        ("LB",  0, 0, 1),
        ("LH",  1, 1, 1),
        ("LW",  2, 2, 0),
        ("LBU", 4, 0, 0),
        ("LHU", 5, 1, 0),
    ])
    def test_load(self, name, funct3, mem_width, mem_signed):
        instr = _i_type(0, rs1=1, funct3=funct3, rd=2, opcode=0x03)
        result = _EV.decode(instr)
        assert result is not None, f"{name}: no block matched instr={instr:#010x}"
        assert result["imm_sel"]    == IMM_I,     f"{name}: imm_sel"
        assert result["use_rs1"]    == 1,         f"{name}: use_rs1"
        assert result["use_rs2"]    == 0,         f"{name}: use_rs2"
        assert result["use_rd"]     == 1,         f"{name}: use_rd"
        assert result["is_load"]    == 1,         f"{name}: is_load"
        assert result["is_store"]   == 0,         f"{name}: is_store"
        assert result["mem_width"]  == mem_width, f"{name}: mem_width"
        assert result["mem_signed"] == mem_signed, f"{name}: mem_signed"


class TestStoreInstructions:
    """Store: opcode=0x23."""

    @pytest.mark.parametrize("name,funct3,mem_width", [
        ("SB", 0, 0),
        ("SH", 1, 1),
        ("SW", 2, 2),
    ])
    def test_store(self, name, funct3, mem_width):
        instr = _s_type(0, rs2=2, rs1=1, funct3=funct3, opcode=0x23)
        result = _EV.decode(instr)
        assert result is not None, f"{name}: no block matched instr={instr:#010x}"
        assert result["imm_sel"]   == IMM_S,     f"{name}: imm_sel"
        assert result["use_rs1"]   == 1,         f"{name}: use_rs1"
        assert result["use_rs2"]   == 1,         f"{name}: use_rs2"
        assert result["use_rd"]    == 0,         f"{name}: use_rd"
        assert result["is_load"]   == 0,         f"{name}: is_load"
        assert result["is_store"]  == 1,         f"{name}: is_store"
        assert result["mem_width"] == mem_width, f"{name}: mem_width"


class TestBranchInstructions:
    """Branch: opcode=0x63."""

    @pytest.mark.parametrize("name,funct3,alu", [
        ("BEQ",  0, ALU_XOR),   # XOR → test result == 0
        ("BNE",  1, ALU_XOR),   # XOR → test result != 0
        ("BLT",  4, ALU_SLT),
        ("BGE",  5, ALU_SLT),
        ("BLTU", 6, ALU_SLTU),
        ("BGEU", 7, ALU_SLTU),
    ])
    def test_branch(self, name, funct3, alu):
        instr = _b_type(0, rs2=2, rs1=1, funct3=funct3, opcode=0x63)
        result = _EV.decode(instr)
        assert result is not None, f"{name}: no block matched instr={instr:#010x}"
        assert result["alu_op"]    == alu,    f"{name}: alu_op"
        assert result["imm_sel"]   == IMM_B,  f"{name}: imm_sel"
        assert result["use_rs1"]   == 1,      f"{name}: use_rs1"
        assert result["use_rs2"]   == 1,      f"{name}: use_rs2"
        assert result["use_rd"]    == 0,      f"{name}: use_rd"
        assert result["is_load"]   == 0,      f"{name}: is_load"
        assert result["is_store"]  == 0,      f"{name}: is_store"
        assert result["is_branch"] == 1,      f"{name}: is_branch"
        assert result["is_jal"]    == 0,      f"{name}: is_jal"
        assert result["is_jalr"]   == 0,      f"{name}: is_jalr"


class TestUIJInstructions:
    """Upper-immediate and jump instructions."""

    def test_lui(self):
        instr = _u_type(0x12345, rd=1, opcode=0x37)
        result = _EV.decode(instr)
        assert result is not None, f"LUI: no block matched instr={instr:#010x}"
        assert result["alu_op"]    == ALU_PASS, "LUI: alu_op"
        assert result["imm_sel"]   == IMM_U,    "LUI: imm_sel"
        assert result["use_rs1"]   == 0,        "LUI: use_rs1"
        assert result["use_rs2"]   == 0,        "LUI: use_rs2"
        assert result["use_rd"]    == 1,        "LUI: use_rd"
        assert result["is_load"]   == 0,        "LUI: is_load"
        assert result["is_store"]  == 0,        "LUI: is_store"
        assert result["is_branch"] == 0,        "LUI: is_branch"
        assert result["is_jal"]    == 0,        "LUI: is_jal"
        assert result["is_jalr"]   == 0,        "LUI: is_jalr"

    def test_auipc(self):
        instr = _u_type(0x12345, rd=1, opcode=0x17)
        result = _EV.decode(instr)
        assert result is not None, f"AUIPC: no block matched instr={instr:#010x}"
        assert result["alu_op"]    == ALU_ADD, "AUIPC: alu_op"
        assert result["imm_sel"]   == IMM_U,   "AUIPC: imm_sel"
        assert result["use_rs1"]   == 0,       "AUIPC: use_rs1"
        assert result["use_rs2"]   == 0,       "AUIPC: use_rs2"
        assert result["use_rd"]    == 1,       "AUIPC: use_rd"
        assert result["is_jal"]    == 0,       "AUIPC: is_jal"
        assert result["is_jalr"]   == 0,       "AUIPC: is_jalr"

    def test_jal(self):
        instr = _j_type(0x100, rd=1, opcode=0x6F)
        result = _EV.decode(instr)
        assert result is not None, f"JAL: no block matched instr={instr:#010x}"
        assert result["alu_op"]    == ALU_ADD, "JAL: alu_op"
        assert result["imm_sel"]   == IMM_J,   "JAL: imm_sel"
        assert result["use_rs1"]   == 0,       "JAL: use_rs1"
        assert result["use_rs2"]   == 0,       "JAL: use_rs2"
        assert result["use_rd"]    == 1,       "JAL: use_rd"
        assert result["is_branch"] == 0,       "JAL: is_branch"
        assert result["is_jal"]    == 1,       "JAL: is_jal"
        assert result["is_jalr"]   == 0,       "JAL: is_jalr"

    def test_jalr(self):
        instr = _i_type(0, rs1=1, funct3=0, rd=2, opcode=0x67)
        result = _EV.decode(instr)
        assert result is not None, f"JALR: no block matched instr={instr:#010x}"
        assert result["alu_op"]    == ALU_ADD, "JALR: alu_op"
        assert result["imm_sel"]   == IMM_I,   "JALR: imm_sel"
        assert result["use_rs1"]   == 1,       "JALR: use_rs1"
        assert result["use_rs2"]   == 0,       "JALR: use_rs2"
        assert result["use_rd"]    == 1,       "JALR: use_rd"
        assert result["is_branch"] == 0,       "JALR: is_branch"
        assert result["is_jal"]    == 0,       "JALR: is_jal"
        assert result["is_jalr"]   == 1,       "JALR: is_jalr"


class TestSystemInstructions:
    """System instructions: FENCE, ECALL, EBREAK."""

    def test_fence(self):
        instr = _i_type(0, rs1=0, funct3=0, rd=0, opcode=0x0F)
        result = _EV.decode(instr)
        assert result is not None, f"FENCE: no block matched instr={instr:#010x}"
        assert result["alu_op"]    == ALU_ADD,   "FENCE: alu_op"
        assert result["imm_sel"]   == IMM_NONE,  "FENCE: imm_sel"
        assert result["use_rs1"]   == 0,         "FENCE: use_rs1"
        assert result["use_rs2"]   == 0,         "FENCE: use_rs2"
        assert result["use_rd"]    == 0,         "FENCE: use_rd"
        assert result["is_load"]   == 0,         "FENCE: is_load"
        assert result["is_store"]  == 0,         "FENCE: is_store"
        assert result["is_branch"] == 0,         "FENCE: is_branch"
        assert result["is_jal"]    == 0,         "FENCE: is_jal"
        assert result["is_jalr"]   == 0,         "FENCE: is_jalr"

    def test_ecall(self):
        # ECALL: all-zeros except opcode=0x73
        instr = _i_type(0, rs1=0, funct3=0, rd=0, opcode=0x73)
        result = _EV.decode(instr)
        assert result is not None, f"ECALL: no block matched instr={instr:#010x}"
        assert result["alu_op"]  == ALU_ADD,  "ECALL: alu_op"
        assert result["imm_sel"] == IMM_NONE, "ECALL: imm_sel"
        assert result["use_rd"]  == 0,        "ECALL: use_rd"

    def test_ebreak(self):
        # EBREAK: imm=1 → bit 20 set → instr[30]=(1>>10)&1=0 for small values.
        # EBREAK encoding: imm12=0x001 (bit 0 set), funct3=0, opcode=0x73
        # funct7b5 = instr[30] = imm12[10] = (1 >> 10) & 1 = 0 — that's ECALL!
        # Correct: EBREAK has imm=1 → funct7b5 = (1 >> 10) & 1 = 0 → same as ECALL.
        # Actually per ISA: EBREAK = funct7=0100000, rs2=0x01.
        # Let's use the raw encoding: instr = 0x00100073
        instr = 0x00100073  # canonical EBREAK
        result = _EV.decode(instr)
        assert result is not None, f"EBREAK: no block matched instr={instr:#010x}"
        assert result["alu_op"]  == ALU_ADD,  "EBREAK: alu_op"
        assert result["imm_sel"] == IMM_NONE, "EBREAK: imm_sel"
        assert result["use_rd"]  == 0,        "EBREAK: use_rd"


class TestMutualExclusion:
    """Verify that exactly one constraint block fires per valid instruction."""

    @pytest.mark.parametrize("name,instr", [
        ("ADD",  _r_type(0x00, 1, 2, 0, 3, 0x33)),
        ("SUB",  _r_type(0x20, 1, 2, 0, 3, 0x33)),
        ("AND",  _r_type(0x00, 1, 2, 7, 3, 0x33)),
        ("ADDI", _i_type(0,    1, 0, 2, 0x13)),
        ("LW",   _i_type(0,    1, 2, 2, 0x03)),
        ("SW",   _s_type(0, 2, 1, 2, 0x23)),
        ("BEQ",  _b_type(0, 2, 1, 0, 0x63)),
        ("JAL",  _j_type(0x100, 1, 0x6F)),
        ("LUI",  _u_type(1, 1, 0x37)),
    ])
    def test_exactly_one_block_fires(self, name, instr):
        cc = ConstraintCompiler(RV32IDecode, prefix="")
        cc.extract()
        ev = ConstraintEvaluator(RV32IDecode)
        fired = [
            b.name for b in cc.cset.constraints
            if ev._block_fires(instr, b)
        ]
        assert len(fired) == 1, (
            f"{name}: expected exactly 1 block, got {len(fired)}: {fired}"
        )


class TestBlockCount:
    """Sanity-check that the expected number of constraint blocks were extracted."""

    def test_constraint_block_count(self):
        cc = ConstraintCompiler(RV32IDecode, prefix="")
        cc.extract()
        n = len(cc.cset.constraints)
        # 10 R-type + 9 I-ALU + 5 Load + 3 Store + 6 Branch + 4 UI/Jump + 3 System = 40
        assert n == 40, f"Expected 40 constraint blocks, got {n}"

    def test_support_bits(self):
        cc = ConstraintCompiler(RV32IDecode, prefix="")
        cc.extract()
        cc.compute_support()
        bits = cc.cset.support_bits
        # Support: [6:0] opcode, [14:12] funct3, [30] funct7b5
        assert len(bits) == 3, f"Expected 3 support ranges, got {len(bits)}: {bits}"
        widths = sorted(b.width() for b in bits)
        assert widths == [1, 3, 7], f"Expected [1, 3, 7] widths, got {widths}"

    def test_derived_field_map(self):
        cc = ConstraintCompiler(RV32IDecode, prefix="")
        cc.extract()
        derived = cc._derived_to_bitrange
        assert "_opcode"   in derived, "_opcode not in derived_to_bitrange"
        assert "_funct3"   in derived, "_funct3 not in derived_to_bitrange"
        assert "_funct7b5" in derived, "_funct7b5 not in derived_to_bitrange"
        assert derived["_opcode"].lsb   == 0  and derived["_opcode"].msb   == 6
        assert derived["_funct3"].lsb   == 12 and derived["_funct3"].msb   == 14
        assert derived["_funct7b5"].lsb == 30 and derived["_funct7b5"].msb == 30

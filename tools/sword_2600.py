#!/usr/bin/env python3
# coding: utf-8
"""sword_2600 -- cartridge intake and 6502 static scan for the revival pipeline.

This is the first engineering step of "resurrect what people already own", and it is
deliberately the step that touches nothing copyrighted: the tool reads the owner's own
cartridge image, hashes it, and reports **what the hardware has to do** for that game to
run. It ships no game bytes, writes no modified image, and has no decryption path.

Why the Atari 2600 first. The machine is the best case in the whole revival problem:

  * the CPU is a 6507 at **1.19 MHz**, and a frame is **76 cycles per scanline x 262
    scanlines** (NTSC) with **228 colour clocks per line** = three colour clocks per CPU
    cycle. Those numbers are not an estimate; they are the machine;
  * there are **128 bytes of RAM** and no floating point, no clock, no OS. Every 2600 game
    is already an integer, frame-locked simulation -- which is exactly what a Shard is;
  * the display is *raced*, not buffered: the program writes TIA registers while the beam
    is drawing, so the "renderer" is a sequence of register writes, not an asset.

That last point is the interesting one for a shard: the game's *presentation* is a
**program**, and a program can be reimplemented. The cartridge in the owner's hand stays in
the owner's hand.

What this file does NOT do (and the report says so):

  * it does not decode undocumented/illegal opcodes -- it counts them and stops;
  * it does not claim a byte is code because it decoded: only bytes reached by the
    recursive scan from the RESET vector are code, everything else is reported as data;
  * it does not emulate the TIA. A conversion report is not a running game;
  * it does not guess a bank-switch scheme. A size can be several schemes (an 8K cartridge
    can be F8, FE or 3F), so each candidate is probed and the scheme that actually saw a
    switch access is reported -- with its evidence, and with "ambiguous" or "no switch
    found" as first-class outcomes rather than a silent pick. E0, E7, F4SC, 3E and DPC are
    named as unmodelled instead of being approximated.

Usage
-----
  python tools/sword_2600.py --inspect <rom>                     # human report
  python tools/sword_2600.py --inspect <rom> --json              # machine report
  python tools/sword_2600.py --inspect <rom> --mapper F8          # force a scheme
  python tools/sword_2600.py --selftest                          # decode/scan/mapper controls

Exit codes: 0 report produced, 1 the image was refused, 2 the arguments were unusable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# The machine's constants (NTSC). Cited so they can be argued with.
# --------------------------------------------------------------------------- #

CPU_HZ = 1_192_800              # 6507, NTSC
CYCLES_PER_LINE = 76            # 6502 cycles; 228 colour clocks / 3
LINES_NTSC = 262
COLOUR_CLOCKS_PER_LINE = 228
RAM_BYTES = 128

#: Cartridge sizes this intake accepts, and the bank-switch scheme each size implies.
SIZE_CLASS = {
    2048: "2K (no banking)",
    4096: "4K (no banking)",
    8192: "8K (2 x 4K candidate banks)",
    16384: "16K (4 x 4K candidate banks)",
    32768: "32K (8 x 4K candidate banks)",
    65536: "64K (banked -- no modelled scheme covers it)",
}

# --------------------------------------------------------------------------- #
# Mappers: how a cartridge larger than 4K selects which window the CPU sees.
#
# Sizes alone cannot identify a scheme: an 8K cartridge can be F8, FE, E0 or 3F.
# So the scheme is not inferred from the size -- it is MEASURED, by probing each
# candidate's switch addresses in reachable code, and the report names the scheme it
# actually saw. A scheme nothing touches is not a scheme; a scheme two of them touch is
# reported as ambiguous rather than silently picked.
# --------------------------------------------------------------------------- #


class MapperSpec:
    """One bank-switching scheme, modelled by the addresses that select a bank."""

    __slots__ = ("name", "bank_size", "bank_count", "base", "hotspots",
                 "value_hotspot", "power_on_bank", "note")

    def __init__(self, name: str, bank_size: int, bank_count: int, base: int,
                 hotspots: Optional[Dict[int, int]] = None,
                 value_hotspot: Optional[int] = None,
                 power_on_bank: Optional[int] = None, note: str = "") -> None:
        self.name = name
        self.bank_size = bank_size
        self.bank_count = bank_count
        self.base = base
        self.hotspots = dict(hotspots or {})
        self.value_hotspot = value_hotspot
        #: At power-up a banked cartridge selects its LAST bank, which is why the
        #: vectors at the end of the file always belong to the entry bank.
        self.power_on_bank = bank_count - 1 if power_on_bank is None else power_on_bank
        self.note = note

    def as_dict(self) -> Dict:
        return {
            "name": self.name,
            "bank_size": self.bank_size,
            "bank_count": self.bank_count,
            "window": "%#06x-%#06x" % (self.base, self.base + self.bank_size - 1),
            "hotspots": ["%#06x" % address for address in sorted(self.hotspots)],
            "value_hotspot": None if self.value_hotspot is None else "%#06x" % self.value_hotspot,
            "power_on_bank": self.power_on_bank,
            "note": self.note,
        }


#: Schemes this intake models. Each hotspot entry is (address an access triggers on,
#: bank that access selects).
MAPPERS: Dict[str, MapperSpec] = {
    "F8": MapperSpec("F8", 4096, 2, 0xF000, {0x1FF8: 0, 0x1FF9: 1},
                     note="2 x 4K; any access to $1FF8/$1FF9 is the switch, read or written"),
    "F6": MapperSpec("F6", 4096, 4, 0xF000,
                     {address: address - 0x1FF6 for address in range(0x1FF6, 0x1FFA)},
                     note="4 x 4K; $1FF6-$1FF9 select banks 0-3"),
    "F4": MapperSpec("F4", 4096, 8, 0xF000,
                     {address: address - 0x1FF4 for address in range(0x1FF4, 0x1FFC)},
                     note="8 x 4K; $1FF4-$1FFB select banks 0-7"),
    "FE": MapperSpec("FE", 4096, 2, 0xF000, {0x01FE: 0, 0x01FF: 1},
                     note="2 x 4K; the switch address is in TIA space at $01FE/$01FF"),
    "3F": MapperSpec("3F", 2048, 4, 0xF800, value_hotspot=0x003F,
                     note="2K banks; the VALUE written to $003F selects the bank, so a switch "
                          "whose value the static scan cannot resolve is reported unresolved"),
}

#: Schemes that exist but are NOT modelled. They are named rather than guessed, because a
#: wrong model produces a confident wrong report -- the failure this track exists to avoid.
UNMODELLED = {
    8192: ["E0", "FE with a non-standard hotspot"],
    16384: ["E7", "F6SC"],
    32768: ["F4SC", "3E", "DPC"],
    65536: ["F4SC", "E7", "DPC"],
}

#: Which schemes a size CAN be, for probing. Empty list = nothing modelled fits.
CANDIDATES_BY_SIZE: Dict[int, List[str]] = {
    2048: ["none"],
    4096: ["none"],
    8192: ["F8", "FE", "3F"],
    16384: ["F6", "3F"],
    32768: ["F4", "3F"],
    65536: [],
}


def resolve_spec(name: str, size: int) -> Optional[MapperSpec]:
    """The scheme, or None when that scheme cannot describe this image size.

    Deliberately non-raising: a probe needs to be told "this candidate does not fit",
    which is a normal outcome of measurement, not an error to swallow.
    """
    if name == "none":
        if size <= 4096:
            return MapperSpec("none", size, 1, 0xF800 if size == 2048 else 0xF000,
                              note="a single window: 2K sits at $F800, everything larger at "
                                   "$F000")
        # A larger image cannot be one window: the 6507's address space ends at $FFFF. So
        # only the power-on 4K window is visible, and the remaining banks are reported
        # UNREACHED rather than folded into a window that does not exist.
        return MapperSpec("none", 4096, size // 4096, 0xF000,
                          note="no switch modelled: only the power-on 4K window is visible, so "
                               "the other banks are reported unreached rather than mapped")
    spec = MAPPERS.get(name)
    if spec is None:
        return None
    if name == "3F":
        # 3F covers 8K-32K with 2K banks, so the bank count follows the size.
        if size % spec.bank_size or not 8192 <= size <= 32768:
            return None
        return MapperSpec("3F", spec.bank_size, size // spec.bank_size, spec.base,
                          value_hotspot=spec.value_hotspot, note=spec.note)
    if spec.bank_size * spec.bank_count != size:
        return None
    return spec


def build_mapper(name: str, size: int) -> MapperSpec:
    """Materialise a scheme for a given image size, refusing a size/scheme mismatch."""
    spec = resolve_spec(name, size)
    if spec is not None:
        return spec
    if name not in MAPPERS and name != "none":
        raise Refusal("%r is not a scheme this intake models (modelled: %s)"
                      % (name, ", ".join(sorted(MAPPERS))))
    raise Refusal("%s cannot describe a %d-byte image (modelled geometries: %s)"
                  % (name, size, ", ".join("%s %d x %d" % (scheme.name, scheme.bank_count,
                                                            scheme.bank_size)
                                           for scheme in MAPPERS.values())))

# --------------------------------------------------------------------------- #
# 6502 decode: documented opcodes only, derived from compact groups so the table
# cannot drift from the mnemonic it belongs to.
# --------------------------------------------------------------------------- #

#: (mnemonic, "mode opcode|mode opcode|...") for every documented instruction that is
#: not covered by the eight regular ALU families below.
_GROUPS: Tuple[Tuple[str, str], ...] = (
    ("BRK", "imp 00"), ("RTI", "imp 40"), ("RTS", "imp 60"), ("NOP", "imp EA"),
    ("JMP", "abs 4C|ind 6C"), ("JSR", "abs 20"),
    ("BPL", "rel 10"), ("BMI", "rel 30"), ("BVC", "rel 50"), ("BVS", "rel 70"),
    ("BCC", "rel 90"), ("BCS", "rel B0"), ("BNE", "rel D0"), ("BEQ", "rel F0"),
    ("CLC", "imp 18"), ("SEC", "imp 38"), ("CLI", "imp 58"), ("SEI", "imp 78"),
    ("CLV", "imp B8"), ("CLD", "imp D8"), ("SED", "imp F8"),
    ("PHA", "imp 48"), ("PLA", "imp 68"), ("PHP", "imp 08"), ("PLP", "imp 28"),
    ("TAX", "imp AA"), ("TXA", "imp 8A"), ("TAY", "imp A8"), ("TYA", "imp 98"),
    ("TSX", "imp BA"), ("TXS", "imp 9A"),
    ("INX", "imp E8"), ("DEX", "imp CA"), ("INY", "imp C8"), ("DEY", "imp 88"),
    ("ASL", "acc 0A|zp 06|zpx 16|abs 0E|abx 1E"),
    ("ROL", "acc 2A|zp 26|zpx 36|abs 2E|abx 3E"),
    ("LSR", "acc 4A|zp 46|zpx 56|abs 4E|abx 5E"),
    ("ROR", "acc 6A|zp 66|zpx 76|abs 6E|abx 7E"),
    ("INC", "zp E6|zpx F6|abs EE|abx FE"),
    ("DEC", "zp C6|zpx D6|abs CE|abx DE"),
    ("BIT", "zp 24|abs 2C"),
    ("STX", "zp 86|zpy 96|abs 8E"),
    ("STY", "zp 84|zpx 94|abs 8C"),
    ("LDX", "imm A2|zp A6|zpy B6|abs AE|aby BE"),
    ("LDY", "imm A0|zp A4|zpx B4|abs AC|abx BC"),
    ("CPX", "imm E0|zp E4|abs EC"),
    ("CPY", "imm C0|zp C4|abs CC"),
)

#: The eight regular families: opcode = family<<5 | mode<<2 | 0b01.
_ALU = ("ORA", "AND", "EOR", "ADC", "STA", "LDA", "CMP", "SBC")
_ALU_MODES = ("izx", "zp", "imm", "abs", "izy", "zpx", "aby", "abx")

#: Operand length per addressing mode.
MODE_LEN = {"imp": 0, "acc": 0, "imm": 1, "zp": 1, "zpx": 1, "zpy": 1,
            "izx": 1, "izy": 1, "rel": 1, "abs": 2, "abx": 2, "aby": 2, "ind": 2}

#: Opcodes the regular rule would produce but which are not documented instructions.
_ALU_EXCEPTIONS = {0x89: "STA imm is not a documented instruction"}


def _build_table() -> Dict[int, Tuple[str, str]]:
    table: Dict[int, Tuple[str, str]] = {}
    for family_index, mnemonic in enumerate(_ALU):
        for mode_index, mode in enumerate(_ALU_MODES):
            opcode = (family_index << 5) | (mode_index << 2) | 0b01
            if opcode in _ALU_EXCEPTIONS:
                continue
            if mnemonic == "STA" and mode == "imm":
                continue
            table[opcode] = (mnemonic, mode)
    for mnemonic, spec in _GROUPS:
        for entry in spec.split("|"):
            mode, hex_value = entry.split()
            table[int(hex_value, 16)] = (mnemonic, mode)
    return table


OPCODES = _build_table()

# --------------------------------------------------------------------------- #
# Hardware map: what an address means, which is the whole point of the report
# --------------------------------------------------------------------------- #

TIA_WRITE = {
    0x00: "VSYNC", 0x01: "VBLANK", 0x02: "WSYNC", 0x03: "RSYNC",
    0x04: "NUSIZ0", 0x05: "NUSIZ1", 0x06: "COLUP0", 0x07: "COLUP1",
    0x08: "COLUPF", 0x09: "COLUBK", 0x0A: "CTRLPF", 0x0B: "REFP0",
    0x0C: "REFP1", 0x0D: "PF0", 0x0E: "PF1", 0x0F: "PF2",
    0x10: "RESP0", 0x11: "RESP1", 0x12: "RESM0", 0x13: "RESM1",
    0x14: "RESBL", 0x15: "AUDC0", 0x16: "AUDC1", 0x17: "AUDF0",
    0x18: "AUDF1", 0x19: "AUDV0", 0x1A: "AUDV1", 0x1B: "GRP0",
    0x1C: "GRP1", 0x1D: "ENAM0", 0x1E: "ENAM1", 0x1F: "ENABL",
    0x20: "HMP0", 0x21: "HMP1", 0x22: "HMM0", 0x23: "HMM1",
    0x24: "HMBL", 0x25: "VDELP0", 0x26: "VDELP1", 0x27: "VDELBL",
    0x28: "RESMP0", 0x29: "RESMP1", 0x2A: "HMOVE", 0x2B: "HMCLR",
    0x2C: "CXCLR",
}
TIA_READ = {0x0C: "INPT4", 0x0D: "INPT5", 0x30: "CXM0P", 0x31: "CXM1P",
            0x32: "CXP0FB", 0x33: "CXP1FB", 0x34: "CXM0FB", 0x35: "CXM1FB",
            0x36: "CXBLPF", 0x37: "CXPPMM"}
RIOT = {0x280: "SWCHA", 0x281: "SWCHB", 0x282: "SWACNT", 0x283: "SWBCNT",
        0x284: "INTIM", 0x285: "TIMINT", 0x294: "TIM1T", 0x295: "TIM8T",
        0x296: "TIM64T", 0x297: "T1024T"}


def classify_address(address: int, write: bool) -> str:
    """Name the hardware an access is aimed at, or say it is RAM/ROM."""
    if address < 0x40:
        name = (TIA_WRITE if write else TIA_READ).get(address)
        return "TIA.%s" % name if name else "TIA[%#04x]" % address
    if 0x40 <= address < 0x80:
        return "RAM mirror"
    if 0x80 <= address < 0x100:
        return "RAM"
    if 0x280 <= address <= 0x2FF:
        name = RIOT.get(address)
        return "RIOT.%s" % name if name else "RIOT[%#03x]" % address
    return "ROM/other"


# --------------------------------------------------------------------------- #
# Intake
# --------------------------------------------------------------------------- #

class Refusal(Exception):
    """The image cannot be inspected as a cartridge; not a verdict about the game."""


def load_cartridge(path: Path) -> Tuple[bytes, Dict]:
    data = path.read_bytes()
    if not data:
        raise Refusal("%s is empty" % path)
    if len(data) not in SIZE_CLASS:
        raise Refusal("%s is %d bytes, which is not a cartridge size this intake knows "
                      "(%s); refusing rather than guessing a mapping"
                      % (path, len(data), ", ".join(str(k) for k in sorted(SIZE_CLASS))))
    if len(data) < 16:
        raise Refusal("%s is too small to hold vectors" % path)
    info = {
        "path": str(path),
        "size_bytes": len(data),
        "size_class": SIZE_CLASS[len(data)],
        "sha256": hashlib.sha256(data).hexdigest(),
        "vectors": {
            "nmi": int.from_bytes(data[-6:-4], "little"),
            "reset": int.from_bytes(data[-4:-2], "little"),
            "irq": int.from_bytes(data[-2:], "little"),
        },
        "bank_count": max(1, len(data) // 4096),
    }
    return data, info


# --------------------------------------------------------------------------- #
# Recursive scan: only reachable bytes are called code
# --------------------------------------------------------------------------- #

BRANCHES = {0x10, 0x30, 0x50, 0x70, 0x90, 0xB0, 0xD0, 0xF0}


def scan(data: bytes, entry: int, base: int = 0, limit: int = 8192,
         code_out: Optional[set] = None,
         mapper: Optional[MapperSpec] = None) -> Dict:
    """Decode from entry, following control flow; report what was reachable.

    Bytes are never called code because they decoded -- only because the scan reached
    them. Everything unreached is reported as data, which is the honest answer for a
    ROM that interleaves jump tables with routines (most of them do).

    ``code_out`` lets a caller share one code set across several entries into the same
    bank, which is how the banked scan avoids decoding a routine twice. ``mapper`` turns
    an access to a switch address into a recorded switch event instead of an ordinary
    read -- the access still counts, because on this hardware it *is* an access.
    """
    end = base + len(data)
    code: set = code_out if code_out is not None else set()
    start_code = len(code)
    queue: List[int] = [entry]
    histogram: Dict[str, int] = {}
    reads: Dict[str, int] = {}
    writes: Dict[str, int] = {}
    switch_events: List[Dict] = []
    unresolved: List[Dict] = []
    #: Where the walk met a byte that is not a documented instruction. The COUNT is the
    #: alarm; the ADDRESSES are the diagnosis, because on a real cartridge this usually
    #: means the walk wandered into data and everything after it is mislabelled.
    illegal_sites: List[str] = []
    #: Targets an indirect jump actually resolved, so a followed jump table is visible
    #: rather than inferred from a sudden jump in the instruction count.
    indirect: List[str] = []
    illegal = 0
    decoded = 0
    truncated = False
    while queue and decoded < limit:
        pc = queue.pop()
        last_imm: Optional[int] = None
        while base <= pc < end and decoded < limit:
            offset = pc - base
            opcode = data[offset]
            if offset in code:
                break
            entry_table = OPCODES.get(opcode)
            if entry_table is None:
                illegal += 1
                illegal_sites.append({"pc": "%#06x" % pc,
                                      "site": "%#06x (opcode %#04x, undocumented)"
                                              % (pc, opcode)})
                break
            mnemonic, mode = entry_table
            length = 1 + MODE_LEN[mode]
            if offset + length > len(data):
                illegal += 1
                illegal_sites.append({"pc": "%#06x" % pc,
                                      "site": "%#06x (runs past the end)" % pc})
                break
            for index in range(length):
                code.add(offset + index)
            decoded += 1
            histogram[mnemonic] = histogram.get(mnemonic, 0) + 1
            operand = data[offset + 1] if length > 1 else 0
            absolute = int.from_bytes(data[offset + 1:offset + 3], "little") if length == 3 else None
            target = None
            address: Optional[int] = None
            is_write = False

            if mnemonic in ("LDA", "LDX", "LDY", "ADC", "SBC", "AND", "ORA", "EOR",
                            "CMP", "CPX", "CPY", "BIT"):
                address = absolute if absolute is not None else operand
                category = classify_address(address, write=False)
                reads[category] = reads.get(category, 0) + 1
                if mode == "imm":
                    last_imm = operand
            elif mnemonic in ("STA", "STX", "STY", "INC", "DEC", "ASL", "LSR", "ROL", "ROR"):
                address = absolute if absolute is not None else operand
                category = classify_address(address, write=True)
                writes[category] = writes.get(category, 0) + 1
                is_write = True
            elif mode == "rel":
                target = pc + 2 + (operand - 256 if operand > 127 else operand)
            elif mnemonic == "JMP" and mode == "abs":
                target = absolute
            elif mnemonic == "JMP" and mode == "ind":
                # A jump table. Following it is the difference between scanning a game and
                # stopping at its first dispatch, which most 2600 games have.
                pointer = absolute
                if pointer is not None and base <= pointer and pointer + 1 < end:
                    target = data[pointer - base] | (data[pointer - base + 1] << 8)
                    indirect.append("%#06x -> %#06x" % (pointer, target))
                else:
                    unresolved.append({
                        "pc": "%#06x" % pc,
                        "address": None if pointer is None else "%#06x" % pointer,
                        "why": "indirect jump through a pointer outside this window; the "
                               "dispatch target is unknown, not absent",
                    })
            elif mnemonic == "JSR":
                target = absolute
                queue.append(pc + 3)

            if mapper is not None and address is not None:
                selected = mapper.hotspots.get(address)
                value_based = (mapper.value_hotspot is not None
                               and address == mapper.value_hotspot and is_write)
                if value_based:
                    if last_imm is None:
                        selected = None
                        unresolved.append({
                            "pc": "%#06x" % pc,
                            "address": "%#06x" % address,
                            "why": "this scheme selects a bank by the VALUE written, and the "
                                   "static scan could not resolve that value to an immediate; "
                                   "the bank it reached is unknown rather than zero",
                        })
                    else:
                        selected = last_imm % mapper.bank_count
                if selected is not None:
                    switch_events.append({
                        "pc": "%#06x" % pc,
                        "address": "%#06x" % address,
                        "value": None if not value_based else last_imm,
                        "resume": pc + length,
                        "to": selected,
                    })
                    # The fetch after a switch comes from the NEW bank, so this linear
                    # walk ends here; the banked scan resumes at `resume`. Continuing in
                    # the old bank would decode the wrong bytes as this bank's code.
                    break

            if target is not None and base <= target < end:
                queue.append(target)
            if mnemonic in ("RTS", "RTI"):
                break
            if mnemonic == "JMP":
                break
            pc += length
    # Anything still queued was queued and never walked: a switch ended this linear walk
    # before its JSR return sites were reached. Dropping them silently loses code that the
    # report would then call data, so they go back to the caller, which knows the bank.
    pending = sorted({address for address in queue if base <= address < end})
    if decoded >= limit and (queue or pending):
        truncated = True
    return {
        "entry": entry,
        "instructions": decoded,
        "code_bytes": len(code) - start_code,
        "data_bytes": len(data) - (len(code) - start_code),
        "illegal_or_unreached_stop": illegal,
        "illegal_sites": illegal_sites,
        "indirect_jumps": indirect,
        "pending": pending,
        "truncated": truncated,
        "limit": limit,
        "histogram": dict(sorted(histogram.items(), key=lambda kv: -kv[1])),
        "reads": dict(sorted(reads.items())),
        "writes": dict(sorted(writes.items())),
        "switch_events": switch_events,
        "unresolved": unresolved,
        "reachable": sorted(code),
    }


def scan_banked(data: bytes, mapper: MapperSpec, entry: int, limit: int = 8192) -> Dict:
    """Scan every bank of a banked cartridge, following the switches it actually makes.

    The worklist is (bank, offset) rather than an address, because on this hardware the
    same address means different code depending on which bank is selected. A switch
    resumes at the instruction AFTER the switching access, in the NEW bank -- which is
    what the hardware does, and the reason a static scan of one bank cannot see the rest.
    """
    window = mapper.bank_size
    if window * mapper.bank_count != len(data):
        raise Refusal("%s describes %d x %d = %d bytes but the image is %d bytes"
                      % (mapper.name, mapper.bank_count, window,
                         window * mapper.bank_count, len(data)))
    if not mapper.base <= entry < mapper.base + window:
        raise Refusal("RESET vector %#06x is outside the %#06x-%#06x window; the dump is "
                      "probably header-shifted or cut"
                      % (entry, mapper.base, mapper.base + window - 1))

    code_sets = [set() for _ in range(mapper.bank_count)]
    instructions = [0] * mapper.bank_count
    illegal = [0] * mapper.bank_count
    truncated_banks: List[int] = []
    illegal_sites: List[Dict] = []
    indirect_jumps: List[Dict] = []
    deferred: List[Dict] = []
    histogram_total: Dict[str, int] = {}
    reads_total: Dict[str, int] = {}
    writes_total: Dict[str, int] = {}
    switches: List[Dict] = []
    unresolved: List[Dict] = []
    reached = {mapper.power_on_bank}
    queue: List[Tuple[int, int]] = [(mapper.power_on_bank, entry - mapper.base)]
    seen: set = set()

    while queue:
        bank, offset = queue.pop()
        if not 0 <= bank < mapper.bank_count or not 0 <= offset < window:
            continue
        if (bank, offset) in seen:
            continue
        seen.add((bank, offset))
        chunk = data[bank * window:(bank + 1) * window]
        report = scan(chunk, mapper.base + offset, base=mapper.base, limit=limit,
                      code_out=code_sets[bank], mapper=mapper)
        instructions[bank] += report["instructions"]
        illegal[bank] += report["illegal_or_unreached_stop"]
        if report["truncated"]:
            truncated_banks.append(bank)
        illegal_sites.extend(dict(site, bank=bank) for site in report["illegal_sites"])
        indirect_jumps.extend(report["indirect_jumps"])
        # A return site queued before a bank switch belongs to THIS bank. Handing it back
        # to this bank is what stops a JSR-then-switch routine from erasing its caller.
        for address in report["pending"]:
            deferred.append({"bank": bank, "address": "%#06x" % address})
            queue.append((bank, address - mapper.base))
        for name, count in report["histogram"].items():
            histogram_total[name] = histogram_total.get(name, 0) + count
        for name, count in report["reads"].items():
            reads_total[name] = reads_total.get(name, 0) + count
        for name, count in report["writes"].items():
            writes_total[name] = writes_total.get(name, 0) + count
        for event in report["switch_events"]:
            target = event["to"]
            switches.append(dict(event, from_bank=bank, to_bank=target))
            reached.add(target)
            queue.append((target, event["resume"] - mapper.base))
        unresolved.extend(report["unresolved"])

    banks: List[Dict] = []
    for bank in range(mapper.bank_count):
        code_bytes = len(code_sets[bank])
        banks.append({
            "bank": bank,
            "slot": "%#06x-%#06x" % (mapper.base, mapper.base + window - 1),
            "reached": bank in reached,
            "entry": bank == mapper.power_on_bank,
            "instructions": instructions[bank],
            "code_bytes": code_bytes,
            "data_bytes": window - code_bytes,
            "illegal_or_unreached_stop": illegal[bank],
            "truncated": bank in truncated_banks,
        })

    return {
        "mapper": mapper.as_dict(),
        "banks": banks,
        "bank_count": mapper.bank_count,
        "reached_banks": sorted(bank for bank in range(mapper.bank_count) if bank in reached),
        "unreached_banks": sorted(bank for bank in range(mapper.bank_count) if bank not in reached),
        "switches": switches,
        "unresolved": unresolved,
        "truncated_banks": truncated_banks,
        "illegal_sites": illegal_sites,
        "indirect_jumps": indirect_jumps,
        "deferred_returns": deferred,
        "limit": limit,
        "aggregate": {
            "instructions": sum(instructions),
            "code_bytes": sum(len(entry) for entry in code_sets),
            "data_bytes": sum(len(entry) for entry in code_sets) * -1 + window * mapper.bank_count,
            "illegal_or_unreached_stop": sum(illegal),
            "histogram": dict(sorted(histogram_total.items(), key=lambda kv: -kv[1])),
            "reads": dict(sorted(reads_total.items())),
            "writes": dict(sorted(writes_total.items())),
        },
    }


def probe_scheme(data: bytes, size: int, name: str,
                 entry: int) -> Tuple[Optional[Dict], Optional[str]]:
    """Run one candidate scheme; return (scan, why it was skipped).

    A scheme is skipped for one of two reasons, and they are kept apart: the size does not
    fit the scheme, or the RESET vector is outside that scheme's window. If EVERY candidate
    skips on the vector, the vector is the problem and the image is refused.
    """
    mapper = resolve_spec(name, size)
    if mapper is None:
        return None, "size does not fit %s" % name
    if not mapper.base <= entry < mapper.base + mapper.bank_size:
        return None, "RESET vector outside %s's %#06x-%#06x window" % (
            name, mapper.base, mapper.base + mapper.bank_size - 1)
    return scan_banked(data, mapper, entry), None


def choose_mapper(data: bytes, entry: int) -> Dict:
    """Pick a scheme by MEASUREMENT, and say how confident that choice is.

    A size can be several schemes (an 8K cartridge can be F8 or FE), so the scheme is not
    inferred: each candidate is run, and the number of switch accesses it actually saw is
    the evidence. Exactly one scheme with evidence is a measurement; two is reported as
    ambiguous; none means no switch was found, which is a finding about the code and not
    a licence to treat the rest of the image as empty.
    """
    size = len(data)
    candidates = CANDIDATES_BY_SIZE.get(size, [])
    if not candidates:
        raise Refusal("%d bytes is a banked cartridge and none of the modelled schemes covers "
                      "%d bytes (%s). Reporting it as one %d-byte window would decode "
                      "bank-switch accesses as ordinary stores and produce a confident wrong "
                      "report, so nothing is reported"
                      % (size, size, ", ".join(sorted(MAPPERS)), size))
    scored: List[Tuple[int, str, Dict]] = []
    entry_refusals: List[str] = []
    for name in candidates:
        scanned, why = probe_scheme(data, size, name, entry)
        if scanned is None:
            if why is not None and "RESET vector" in why:
                entry_refusals.append(why)
            continue
        scored.append((len(scanned["switches"]), name, scanned))
    if not scored and entry_refusals:
        raise Refusal("RESET vector %#06x is outside the window of every scheme this intake "
                      "can model for %d bytes (%s); the dump is probably header-shifted or cut"
                      % (entry, size, "; ".join(entry_refusals)))
    with_evidence = [row for row in scored if row[0] > 0]
    unmodelled = UNMODELLED.get(size, [])

    if len(with_evidence) == 1:
        count, name, scanned = with_evidence[0]
        return {"scanned": scanned, "source": "measured", "name": name,
                "evidence": count, "ambiguous": {}, "unmodelled": unmodelled}
    if not with_evidence:
        scanned, why = probe_scheme(data, size, "none", entry)
        if scanned is None:
            raise Refusal("the image could not be read as one %d-byte window: %s" % (size, why))
        return {"scanned": scanned, "source": "no switch found", "name": "none",
                "evidence": 0, "ambiguous": {}, "unmodelled": unmodelled}
    with_evidence.sort(key=lambda row: -row[0])
    count, name, scanned = with_evidence[0]
    return {"scanned": scanned, "source": "ambiguous", "name": name, "evidence": count,
            "ambiguous": {row[1]: row[0] for row in with_evidence}, "unmodelled": unmodelled}


def profile(data: bytes, report: Dict) -> Dict:
    """Turn the scan into advice the conversion stage can act on."""
    writes = report["writes"]
    reads = report["reads"]
    wsync = writes.get("TIA.WSYNC", 0)
    vsync = writes.get("TIA.VSYNC", 0)
    graphics = sum(count for name, count in writes.items()
                   if name in ("TIA.GRP0", "TIA.GRP1", "TIA.PF0", "TIA.PF1", "TIA.PF2",
                               "TIA.COLUP0", "TIA.COLUP1", "TIA.COLUPF", "TIA.COLUBK",
                               "TIA.RESP0", "TIA.RESP1", "TIA.ENAM0", "TIA.ENAM1",
                               "TIA.ENABL", "TIA.NUSIZ0", "TIA.NUSIZ1", "TIA.CTRLPF"))
    timer = sum(count for name, count in writes.items()
                if name in ("RIOT.TIM64T", "RIOT.TIM8T", "RIOT.T1T", "RIOT.T1024T"))
    collisions = sum(count for name, count in reads.items()
                     if name.startswith("TIA.CX"))
    input_reads = sum(count for name, count in reads.items()
                      if name in ("RIOT.SWCHA", "RIOT.SWCHB", "TIA.INPT4", "TIA.INPT5"))
    advice: List[str] = []
    if wsync >= 64:
        advice.append("beam-raced display: %d WSYNC writes in reachable code, so the "
                      "presentation is a per-scanline program -- capture it as a raster "
                      "recipe, never as a framebuffer" % wsync)
    if graphics >= 16:
        advice.append("%d graphics-register writes: the renderer is code, which is why a "
                      "conversion must reimplement the kernel rather than copy an image" % graphics)
    if timer:
        advice.append("%d RIOT timer writes: frame pacing is timer-driven; the sim's tick "
                      "length must match the timer reload or the game speed will drift" % timer)
    if collisions:
        advice.append("%d collision-register reads: collision is resolved by the TIA, not "
                      "by game code, so a shard must reproduce the hardware's tolerance "
                      "rather than invent a hitbox" % collisions)
    if input_reads:
        advice.append("%d input reads (SWCHA/INPT): the controller mapping is observable and "
                      "must be measured, not assumed" % input_reads)
    if not advice:
        touched = wsync + graphics + timer + collisions + input_reads
        if touched:
            advice.append("sparse hardware use (%d writes/reads, %d instructions reached): "
                          "a partial scan of a stub, or a game whose work happens in a "
                          "routine the static scan cannot follow -- not evidence of a simple "
                          "game" % (touched, report["instructions"]))
        else:
            advice.append("no display or input register reached from the entry point after %d "
                          "instructions; the mapping or the entry is probably wrong, and the "
                          "report should be treated as uninformative rather than as 'simple'"
                          % report["instructions"])
    return {
        "wsync_writes": wsync,
        "vsync_writes": vsync,
        "graphics_register_writes": graphics,
        "timer_writes": timer,
        "collision_reads": collisions,
        "input_reads": input_reads,
        "advice": advice,
        "frame_budget": {
            "cycles_per_frame": CYCLES_PER_LINE * LINES_NTSC,
            "colour_clocks_per_frame": COLOUR_CLOCKS_PER_LINE * LINES_NTSC,
            "cpu_hz": CPU_HZ,
            "ram_bytes": RAM_BYTES,
        },
    }


def map_base(size: int) -> int:
    """Where the image sits in the 6507's address space.

    2K carts occupy $F800-$FFFF; every larger cartridge occupies the $F000 window and
    switches banks inside it. Which scheme it switches with is measured, not assumed.
    """
    return 0xF800 if size == 2048 else 0xF000


def verdict(result: Dict) -> List[str]:
    """What the bank map does and does not establish.

    This is the line that keeps a scan from being read as a conservation record: a bank
    nothing reached is reported as unreached, never as empty.
    """
    lines: List[str] = []
    mapper = result["mapper"]
    unreached = result["unreached_banks"]
    if mapper["source"] == "ambiguous":
        lines.append("two switch schemes were touched (%s); the bank map below may be the "
                     "wrong one, so treat every 'unreached' bank as unknown rather than empty"
                     % ", ".join("%s x%d" % (name, count)
                                 for name, count in sorted(mapper["ambiguous_candidates"].items())))
    if mapper["source"] == "no switch found":
        alternatives = mapper["unmodelled_alternatives"]
        if result["bank_count"] > 1:
            lines.append("no switch was found in reachable code, so only the power-on 4K window "
                         "is mapped and the other %d bank(s) are UNMEASURED; a cartridge banked "
                         "by a scheme this intake does not model (%s) looks exactly like this"
                         % (result["bank_count"] - 1,
                            ", ".join(alternatives) if alternatives else "none known"))
        else:
            lines.append("no switch was found in reachable code and the image fits one window, "
                         "which is what a 2K or 4K cartridge looks like")
    if unreached:
        lines.append("bank(s) %s were NOT reached from the entry point; the bytes there are "
                     "reported as data because nothing reached them, not because they are empty"
                     % ", ".join(str(bank) for bank in unreached))
    if result.get("truncated_banks"):
        lines.append("bank(s) %s hit the %d-instruction scan cap, so this report is TRUNCATED "
                     "and says nothing about what follows the cut -- raise the limit before "
                     "trusting the data-byte counts"
                     % (", ".join(str(bank) for bank in result["truncated_banks"]),
                        result.get("scan_limit", 0)))
    if result.get("illegal_sites"):
        first = result["illegal_sites"][0]
        lines.append("%d walk(s) met a byte that is not a documented instruction, first at %s "
                     "in bank %d; that usually means the walk wandered into data, so code "
                     "after it is UNMEASURED"
                     % (len(result["illegal_sites"]), first["site"], first["bank"]))
    if not lines:
        lines.append("every bank was reached, so the switch map is complete for %s"
                     % mapper["name"])
    return lines


def inspect(path: Path, mapper_name: str = "auto") -> Dict:
    data, info = load_cartridge(path)
    entry = info["vectors"]["reset"]

    if mapper_name == "auto":
        chosen = choose_mapper(data, entry)
        scanned = chosen["scanned"]
        selection = {"name": chosen["name"], "source": chosen["source"],
                     "switches_found": chosen["evidence"],
                     "ambiguous_candidates": chosen["ambiguous"],
                     "unmodelled_alternatives": chosen["unmodelled"]}
    else:
        spec = build_mapper(mapper_name, len(data))
        scanned = scan_banked(data, spec, entry)
        selection = {"name": spec.name, "source": "forced",
                     "switches_found": len(scanned["switches"]),
                     "ambiguous_candidates": {},
                     "unmodelled_alternatives": UNMODELLED.get(len(data), [])}

    result = dict(info)
    result["mapper"] = selection
    result["mapper"].update(scanned["mapper"])
    result["banks"] = scanned["banks"]
    result["bank_count"] = scanned["bank_count"]
    result["reached_banks"] = scanned["reached_banks"]
    result["unreached_banks"] = scanned["unreached_banks"]
    result["switches"] = scanned["switches"]
    result["unresolved"] = scanned["unresolved"]
    result["truncated_banks"] = scanned["truncated_banks"]
    result["illegal_sites"] = scanned["illegal_sites"]
    result["indirect_jumps"] = scanned["indirect_jumps"]
    result["deferred_returns"] = scanned["deferred_returns"]
    result["scan_limit"] = scanned.get("limit", 0)
    aggregate = scanned["aggregate"]
    result["scan"] = {
        "instructions": aggregate["instructions"],
        "code_bytes": aggregate["code_bytes"],
        "data_bytes": aggregate["data_bytes"],
        "illegal_or_unreached_stop": aggregate["illegal_or_unreached_stop"],
        "histogram": aggregate["histogram"],
        "reads": aggregate["reads"],
        "writes": aggregate["writes"],
    }
    result["hardware"] = profile(data, {"writes": aggregate["writes"],
                                        "reads": aggregate["reads"],
                                        "instructions": aggregate["instructions"]})
    result["verdict"] = verdict(result)
    result["conversion"] = {
        "class": "R1",
        "why": "the tool reads the owner's cartridge and ships no game bytes; the shard "
               "carries the reimplemented rules and a hash lock to the owner's own image",
        "next_stages": ["raster-kernel capture", "integer sim extraction",
                        "divergence measurement"],
        "shipped_original_bytes": False,
    }
    return result


# --------------------------------------------------------------------------- #
# Self test: two directions, as every oracle in this tree must have
# --------------------------------------------------------------------------- #

def _cartridge(program: bytes, size: int = 4096) -> bytes:
    """A synthetic cartridge: program at 0xF000, RESET pointing at it."""
    image = bytearray(size)
    image[0:len(program)] = program
    reset = 0xF000 + 0
    image[-4:-2] = reset.to_bytes(2, "little")
    image[-2:] = reset.to_bytes(2, "little")
    image[-6:-4] = reset.to_bytes(2, "little")
    return bytes(image)


def _banked_image(banks: List[bytes], base: int, bank_size: int) -> bytes:
    """A synthetic banked cartridge: one program per bank, entry at the power-on bank."""
    image = bytearray(bank_size * len(banks))
    for index, program in enumerate(banks):
        image[index * bank_size:index * bank_size + len(program)] = program
    entry = base.to_bytes(2, "little")
    image[-6:-4] = entry
    image[-4:-2] = entry
    image[-2:] = entry
    return bytes(image)


def selftest() -> int:
    failures: List[str] = []
    tally = {"decode": 0, "scan": 0, "mapper": 0}

    def check(name: str, condition: bool, detail: str = "", category: str = "scan") -> None:
        tally[category] = tally.get(category, 0) + 1
        if not condition:
            failures.append("%s%s" % (name, ": " + detail if detail else ""))

    # Decode controls across the opcode space, including the awkward ones.
    known = {
        0xA9: ("LDA", "imm"), 0xA5: ("LDA", "zp"), 0xAD: ("LDA", "abs"),
        0xB1: ("LDA", "izy"), 0xA1: ("LDA", "izx"), 0xBD: ("LDA", "abx"),
        0x85: ("STA", "zp"), 0x8D: ("STA", "abs"), 0x9D: ("STA", "abx"),
        0x20: ("JSR", "abs"), 0x4C: ("JMP", "abs"), 0x6C: ("JMP", "ind"),
        0x60: ("RTS", "imp"), 0x40: ("RTI", "imp"), 0x00: ("BRK", "imp"),
        0xD0: ("BNE", "rel"), 0xF0: ("BEQ", "rel"), 0xEA: ("NOP", "imp"),
        0x86: ("STX", "zp"), 0x8E: ("STX", "abs"), 0x96: ("STX", "zpy"),
        0xA2: ("LDX", "imm"), 0xBE: ("LDX", "aby"), 0xE6: ("INC", "zp"),
        0xC6: ("DEC", "zp"), 0x0A: ("ASL", "acc"), 0x6E: ("ROR", "abs"),
        0x02: ("WEDGE", ""),
    }
    for opcode, expected in known.items():
        if expected[0] == "WEDGE":
            continue
        check("decode %02X" % opcode, OPCODES.get(opcode) == expected,
              "got %r" % (OPCODES.get(opcode),), category="decode")
    check("illegal 02 is not decoded", 0x02 not in OPCODES)
    check("illegal 89 is not decoded", 0x89 not in OPCODES)
    check("every documented opcode has a length",
          all(1 + MODE_LEN[mode] in (1, 2, 3) for _, mode in OPCODES.values()))

    # A tiny program: clear VSYNC, start a frame, write WSYNC, store A to RAM, loop.
    program = bytes([
        0xA9, 0x00,        # LDA #$00
        0x85, 0x02,        # STA WSYNC
        0x85, 0x80,        # STA $80   (RAM)
        0xA9, 0x02,        # LDA #$02
        0x85, 0x01,        # STA VBLANK
        0x4C, 0x00, 0xF0,  # JMP $F000
    ])
    image = _cartridge(program)
    report = scan(image, 0xF000, base=0xF000)
    # The program above is exactly six instructions; more would mean the scan invented
    # instructions out of the padding, fewer would mean it stopped early.
    check("scan reaches every instruction", report["instructions"] == 6,
          "decoded %d" % report["instructions"])
    check("WSYNC recognised", report["writes"].get("TIA.WSYNC", 0) >= 1,
          str(report["writes"]))
    check("VBLANK recognised", report["writes"].get("TIA.VBLANK", 0) >= 1)
    check("RAM write recognised", report["writes"].get("RAM", 0) >= 1, str(report["writes"]))
    check("data is not called code", report["data_bytes"] > 0)

    # Negative control: a cartridge whose RESET points outside the image must be refused.
    broken = bytearray(image)
    broken[-4:-2] = (0x1234).to_bytes(2, "little")
    check("out-of-range RESET is refused",
          _refuses(bytes(broken)), "scan accepted a bad vector")

    # Negative control: an odd-sized image must be refused rather than guessed.
    check("odd size is refused", _refuses(bytes(program) + b"\x00" * 100))

    # An all-illegal image must not invent instructions.
    noise = _cartridge(bytes([0x02] * 64))
    noise_report = scan(noise, 0xF000, base=0xF000)
    check("illegal stream decodes nothing", noise_report["instructions"] == 0,
          "decoded %d" % noise_report["instructions"])

    # ---- mapper controls -------------------------------------------------- #
    # F8: bank 1 (power-on) reaches a switch at $1FF8, and bank 0's code exists only
    # on the far side of it. If the switch is not followed, bank 0 cannot be seen.
    flesh_after = bytes([0xA9, 0x42, 0x85, 0x80, 0x4C, 0x0B, 0xF0])
    f8_bank0 = bytearray(4096)
    f8_bank0[7:7 + len(flesh_after)] = flesh_after
    f8_bank1 = bytes([0xA9, 0x00, 0x85, 0x02, 0xAD, 0xF8, 0x1F,
                      0x4C, 0x07, 0xF0])
    f8 = _banked_image([bytes(f8_bank0), f8_bank1], 0xF000, 4096)
    f8_report = scan_banked(f8, build_mapper("F8", len(f8)), 0xF000)
    check("F8 follows the switch into bank 0", f8_report["reached_banks"] == [0, 1],
          str(f8_report["reached_banks"]), category="mapper")
    check("F8 records the switch", len(f8_report["switches"]) == 1,
          str(f8_report["switches"]), category="mapper")
    check("F8 bank 0 is decoded only past the switch",
          f8_report["banks"][0]["instructions"] == 3,
          "bank 0: %d" % f8_report["banks"][0]["instructions"], category="mapper")
    check("F8 entry bank is the power-on bank", f8_report["banks"][1]["entry"] is True,
          "", category="mapper")

    # FE: the same shape, but the hotspot is in TIA space. The scheme must be MEASURED,
    # not inferred from the size, because an 8K image can be F8, FE or 3F.
    fe_bank1 = bytes([0xA9, 0x00, 0x85, 0x02, 0xAD, 0xFE, 0x01,
                      0x4C, 0x07, 0xF0])
    fe = _banked_image([bytes(f8_bank0), fe_bank1], 0xF000, 4096)
    check("FE is selected by measurement",
          choose_mapper(fe, 0xF000)["name"] == "FE",
          str(choose_mapper(fe, 0xF000)["name"]), category="mapper")

    # Negative control: a switch at an address no modelled scheme uses. Forced F8 must
    # report bank 0 as NOT REACHED while still reporting the entry bank's code -- the
    # failure mode this control exists for is a zero-code image read as an empty game.
    wrong_bank1 = bytes([0xA9, 0x00, 0x85, 0x02, 0xAD, 0xF0, 0x1F,
                         0x4C, 0x07, 0xF0])
    wrong = _banked_image([bytes(f8_bank0), wrong_bank1], 0xF000, 4096)
    wrong_report = scan_banked(wrong, build_mapper("F8", len(wrong)), 0xF000)
    check("wrong hot-spot leaves bank 0 unreached",
          wrong_report["unreached_banks"] == [0], str(wrong_report["unreached_banks"]),
          category="mapper")
    check("wrong hot-spot still reports the entry bank",
          wrong_report["banks"][1]["instructions"] == 4,
          "entry bank: %d" % wrong_report["banks"][1]["instructions"], category="mapper")
    check("an unreached bank is never called empty",
          any("NOT reached" in line for line in
              verdict({"mapper": {"source": "measured", "ambiguous": {}, "unmodelled": []},
                       "unreached_banks": [0], "size_bytes": 8192, "name": "F8"})),
          "", category="mapper")

    # Auto on that image: no candidate scheme saw a switch, which must be reported as a
    # finding about the code, with the unmodelled schemes named.
    unswitched = choose_mapper(wrong, 0xF000)
    check("no switch found is reported, not guessed",
          unswitched["source"] == "no switch found", unswitched["source"], category="mapper")
    check("no switch found names the unmodelled schemes",
          bool(unswitched["unmodelled"]), str(unswitched["unmodelled"]), category="mapper")

    # Ambiguity: two schemes both see a switch. The report must say so rather than pick.
    both_bank0 = bytearray(4096)
    both_bank0[7:7 + len(flesh_after)] = flesh_after
    both_bank0[10:10 + len(flesh_after)] = flesh_after
    both_bank1 = bytes([0xA9, 0x00, 0x85, 0x02, 0xAD, 0xFE, 0x01,
                        0xAD, 0xF8, 0x1F, 0x4C, 0x0A, 0xF0])
    both = _banked_image([bytes(both_bank0), both_bank1], 0xF000, 4096)
    both_choice = choose_mapper(both, 0xF000)
    check("two switch schemes are reported ambiguous",
          both_choice["source"] == "ambiguous", both_choice["source"], category="mapper")
    check("ambiguity names both schemes",
          len(both_choice["ambiguous"]) >= 2, str(both_choice["ambiguous"]), category="mapper")

    # 3F selects a bank by the VALUE written to $003F, which needs the immediate.
    tf_bank0 = bytearray(2048)
    tf_bank0[4:4 + 7] = bytes([0xA9, 0x42, 0x85, 0x80, 0x4C, 0x08, 0xF8])
    tf_bank3 = bytes([0xA9, 0x00, 0x85, 0x3F, 0x4C, 0x03, 0xF8])
    three_f = _banked_image([bytes(tf_bank0), b"", b"", tf_bank3], 0xF800, 2048)
    tf_report = scan_banked(three_f, build_mapper("3F", len(three_f)), 0xF800)
    check("3F resolves the bank from the written value",
          tf_report["reached_banks"] == [0, 3], str(tf_report["reached_banks"]),
          category="mapper")
    check("3F marks the switch as value-selected",
          bool(tf_report["switches"]) and tf_report["switches"][0]["value"] == 0,
          str(tf_report["switches"]), category="mapper")

    # A 64K image is banked by schemes this intake does not model: it must be refused
    # rather than reported as one 64K window.
    check("an unmodelled banked size is refused",
          _refuses(bytes(65536)), "64K was accepted as a single window", category="mapper")

    if failures:
        print("SELFTEST FAILED (%d)" % len(failures))
        for failure in failures:
            print("  %s" % failure)
        return 1
    print("SELFTEST OK (%d decode controls, %d scan controls, %d mapper controls)"
          % (tally["decode"], tally["scan"], tally["mapper"]))
    return 0


def _refusal(image: bytes) -> Optional[str]:
    """The refusal message, or None when the image was accepted.

    Returning the message rather than a bool keeps the control inspectable: a test that
    only knows "something was refused" cannot tell a size refusal from a vector refusal,
    and would pass just as happily if the tool refused everything.
    """
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".a26") as handle:
        handle.write(image)
        path = Path(handle.name)
    message: Optional[str] = None
    try:
        inspect(path)
    except Refusal as refusal:
        message = str(refusal)
    finally:
        path.unlink(missing_ok=True)
    return message


def _refuses(image: bytes) -> bool:
    return _refusal(image) is not None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cartridge intake and 6502 static scan (no game bytes leave the file).")
    parser.add_argument("--inspect", metavar="ROM", help="a cartridge image to report on")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--selftest", action="store_true", help="run the controls")
    parser.add_argument("--mapper", default="auto",
                        choices=["auto"] + sorted(MAPPERS) + ["none"],
                        help="force a bank-switch scheme instead of measuring it (default: auto)")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.inspect:
        parser.print_help()
        return 2
    path = Path(args.inspect)
    if not path.exists():
        print("no such file: %s" % path, file=sys.stderr)
        return 2
    try:
        report = inspect(path, mapper_name=args.mapper)
    except Refusal as refusal:
        print("REFUSED: %s" % refusal, file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, sort_keys=True))
        return 0

    scan_info = report["scan"]
    hardware = report["hardware"]
    print("== %s" % report["path"])
    mapper = report["mapper"]
    print("   size      %d bytes -- %s, %d bank(s)"
          % (report["size_bytes"], report["size_class"], report["bank_count"]))
    print("   sha256    %s" % report["sha256"])
    print("   reset     %#06x   irq %#06x   nmi %#06x (the 6507 has no NMI/IRQ pin; the "
          "vectors exist in ROM regardless)"
          % (report["vectors"]["reset"], report["vectors"]["irq"], report["vectors"]["nmi"]))
    print("   mapper    %s x%d of %d bytes in %s -- %s; entry bank %d (source: %s, "
          "%d switch accesses)"
          % (mapper["name"], mapper["bank_count"], mapper["bank_size"], mapper["window"],
             mapper["note"], mapper["power_on_bank"], mapper["source"],
             mapper["switches_found"]))
    print("   banks     %d of %d reached"
          % (len(report["reached_banks"]), report["bank_count"]))
    for bank in report["banks"]:
        print("             bank %d  %-9s %4d instr  %5d B code / %5d B data%s"
              % (bank["bank"], "entry" if bank["entry"] else "",
                 bank["instructions"], bank["code_bytes"], bank["data_bytes"],
                 "" if bank["reached"] else "   NOT REACHED"))
    for switch in report["switches"]:
        print("   switch    bank %d -> bank %d at %s via %s%s, resume %#06x"
              % (switch["from_bank"], switch["to_bank"], switch["pc"], switch["address"],
                 "" if switch["value"] is None else " (value %d)" % switch["value"],
                 switch["resume"]))
    for missing in report["unresolved"]:
        print("   unresolved %s at %s: %s"
              % (missing["address"], missing["pc"], missing["why"]))
    for site in report["illegal_sites"][:3]:
        print("   stopped   bank %d at %s" % (site["bank"], site["site"]))
    if len(report["illegal_sites"]) > 3:
        print("             ... %d more" % (len(report["illegal_sites"]) - 3))
    if report["indirect_jumps"]:
        print("   indirect  %d jump table(s) followed: %s"
              % (len(report["indirect_jumps"]), ", ".join(report["indirect_jumps"][:3])))
    if report["deferred_returns"]:
        print("   deferred  %d return site(s) requeued in their own bank: %s"
              % (len(report["deferred_returns"]),
                 ", ".join("%s (bank %d)" % (entry["address"], entry["bank"])
                           for entry in report["deferred_returns"][:3])))
    print("   code      %d instructions, %d bytes code / %d bytes data"
          % (scan_info["instructions"], scan_info["code_bytes"], scan_info["data_bytes"]))
    print("   top       %s" % ", ".join("%s x%d" % (name, count)
                                        for name, count in list(scan_info["histogram"].items())[:6]))
    print("   hardware  WSYNC %d, graphics %d, timer %d, collisions %d, input %d"
          % (hardware["wsync_writes"], hardware["graphics_register_writes"],
             hardware["timer_writes"], hardware["collision_reads"], hardware["input_reads"]))
    for line in hardware["advice"]:
        print("   -> %s" % line)
    for line in report["verdict"]:
        print("   => %s" % line)
    print("   class     %s (%s)" % (report["conversion"]["class"], report["conversion"]["why"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

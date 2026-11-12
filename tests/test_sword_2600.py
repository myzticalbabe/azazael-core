"""Controls for the Atari 2600 intake (`tools/sword_2600.py`).

The tool's own `--selftest` runs the decode and scan controls; these tests make the same
checks reachable from CI, and add the two properties that matter for the revival legal
posture rather than the decoding:

  * a refused image is refused for a stated reason, never silently guessed;
  * the report declares R1 and `shipped_original_bytes: False`, and the tool writes no
    file at all -- it has no output path, which is the cheapest possible proof that it
    cannot be used to redistribute a cartridge.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "sword_2600.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("sword_2600", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool():
    return load_tool()


def cartridge(program: bytes, size: int = 4096) -> bytes:
    image = bytearray(size)
    image[0:len(program)] = program
    reset = (0xF800 if size == 2048 else 0xF000).to_bytes(2, "little")
    image[-6:-4] = reset
    image[-4:-2] = reset
    image[-2:] = reset
    return bytes(image)


PROGRAM = bytes([
    0xA9, 0x00,        # LDA #$00
    0x85, 0x02,        # STA WSYNC
    0x85, 0x80,        # STA $80
    0xA9, 0x02,        # LDA #$02
    0x85, 0x01,        # STA VBLANK
    0x4C, 0x00, 0xF0,  # JMP $F000
])


def write_image(tmp_path: pathlib.Path, image: bytes) -> pathlib.Path:
    path = tmp_path / "cart.a26"
    path.write_bytes(image)
    return path


def test_selftest_passes(tool):
    assert tool.selftest() == 0


def test_inspect_reports_the_machine(tool, tmp_path):
    report = tool.inspect(write_image(tmp_path, cartridge(PROGRAM)))
    assert report["size_class"].startswith("4K")
    assert report["scan"]["instructions"] == 6
    assert report["hardware"]["wsync_writes"] >= 1
    assert report["hardware"]["frame_budget"]["cycles_per_frame"] == 76 * 262
    assert report["hardware"]["frame_budget"]["colour_clocks_per_frame"] == 228 * 262
    assert report["hardware"]["frame_budget"]["ram_bytes"] == 128


def test_report_declares_no_shipped_bytes(tool, tmp_path):
    report = tool.inspect(write_image(tmp_path, cartridge(PROGRAM)))
    assert report["conversion"]["class"] == "R1"
    assert report["conversion"]["shipped_original_bytes"] is False


def test_odd_size_is_refused_not_guessed(tool, tmp_path):
    with pytest.raises(tool.Refusal) as refusal:
        tool.inspect(write_image(tmp_path, b"\x00" * 1234))
    assert "not a cartridge size" in str(refusal.value)


def test_out_of_range_reset_is_refused(tool, tmp_path):
    image = bytearray(cartridge(PROGRAM))
    image[-4:-2] = (0x1234).to_bytes(2, "little")
    with pytest.raises(tool.Refusal) as refusal:
        tool.inspect(write_image(tmp_path, bytes(image)))
    assert "RESET vector" in str(refusal.value)


def test_illegal_stream_yields_no_instructions(tool):
    report = tool.scan(cartridge(bytes([0x02] * 64)), 0xF000, base=0xF000)
    assert report["instructions"] == 0
    assert report["illegal_or_unreached_stop"] >= 1


# --------------------------------------------------------------------------- #
# M-32: the bank-switch model. The scheme is MEASURED, and an unreached bank is
# reported as unreached -- never as an empty game.
# --------------------------------------------------------------------------- #

TAIL = bytes([0xA9, 0x42, 0x85, 0x80, 0x4C, 0x0B, 0xF0])


def banked(banks, base=0xF000, size=4096):
    image = bytearray(size * len(banks))
    for index, program in enumerate(banks):
        image[index * size:index * size + len(program)] = program
    entry = base.to_bytes(2, "little")
    image[-6:-4] = entry
    image[-4:-2] = entry
    image[-2:] = entry
    return bytes(image)


def bank0_with_tail(offset=7):
    bank = bytearray(4096)
    bank[offset:offset + len(TAIL)] = TAIL
    return bytes(bank)


def test_f8_switch_is_measured_and_followed(tool, tmp_path):
    entry_bank = bytes([0xA9, 0x00, 0x85, 0x02, 0xAD, 0xF8, 0x1F, 0x4C, 0x07, 0xF0])
    image = banked([bank0_with_tail(), entry_bank])
    report = tool.inspect(write_image(tmp_path, image))
    assert report["mapper"]["name"] == "F8"
    assert report["mapper"]["source"] == "measured"
    assert report["reached_banks"] == [0, 1]
    assert len(report["switches"]) == 1
    assert report["switches"][0]["to_bank"] == 0
    # The far-side code is only visible because the switch was followed.
    assert report["banks"][0]["instructions"] == 3
    # Power-on selects the LAST bank, so the entry program lives there.
    assert report["banks"][1]["entry"] is True


def test_fe_is_selected_over_f8_by_measurement(tool, tmp_path):
    # Same geometry, different hotspot: only the FE probe sees a switch.
    entry_bank = bytes([0xA9, 0x00, 0x85, 0x02, 0xAD, 0xFE, 0x01, 0x4C, 0x07, 0xF0])
    report = tool.inspect(write_image(tmp_path, banked([bank0_with_tail(), entry_bank])))
    assert report["mapper"]["name"] == "FE"
    assert report["reached_banks"] == [0, 1]


def test_wrong_hotspot_reports_unreached_not_empty(tool, tmp_path):
    # A switch at an address no modelled scheme uses. Forced F8 must say bank 0 is NOT
    # reached while still reporting the entry bank's code -- the failure this control
    # exists for is a zero-code image being read as an empty game.
    entry_bank = bytes([0xA9, 0x00, 0x85, 0x02, 0xAD, 0xF0, 0x1F, 0x4C, 0x07, 0xF0])
    image = banked([bank0_with_tail(), entry_bank])
    path = write_image(tmp_path, image)

    forced = tool.inspect(path, mapper_name="F8")
    assert forced["unreached_banks"] == [0]
    assert forced["banks"][1]["instructions"] == 4
    assert any("NOT reached" in line for line in forced["verdict"])

    auto = tool.inspect(path)
    assert auto["mapper"]["source"] == "no switch found"
    assert auto["unreached_banks"] == [0]
    assert auto["mapper"]["unmodelled_alternatives"]


def test_two_switch_schemes_are_reported_ambiguous(tool, tmp_path):
    both_bank0 = bytearray(4096)
    both_bank0[7:7 + len(TAIL)] = TAIL
    both_bank0[10:10 + len(TAIL)] = TAIL
    entry_bank = bytes([0xA9, 0x00, 0x85, 0x02, 0xAD, 0xFE, 0x01,
                        0xAD, 0xF8, 0x1F, 0x4C, 0x0A, 0xF0])
    report = tool.inspect(write_image(tmp_path, banked([bytes(both_bank0), entry_bank])))
    assert report["mapper"]["source"] == "ambiguous"
    assert len(report["mapper"]["ambiguous_candidates"]) >= 2
    assert any("unknown rather than empty" in line for line in report["verdict"])


def test_3f_resolves_the_bank_from_the_value_written(tool, tmp_path):
    bank0 = bytearray(2048)
    bank0[4:11] = bytes([0xA9, 0x42, 0x85, 0x80, 0x4C, 0x08, 0xF8])
    entry_bank = bytes([0xA9, 0x00, 0x85, 0x3F, 0x4C, 0x03, 0xF8])
    image = banked([bytes(bank0), b"", b"", entry_bank], base=0xF800, size=2048)
    report = tool.inspect(write_image(tmp_path, image))
    assert report["mapper"]["name"] == "3F"
    assert report["reached_banks"] == [0, 3]
    assert report["switches"][0]["value"] == 0


def test_unmodelled_banked_size_is_refused(tool, tmp_path):
    with pytest.raises(tool.Refusal) as refusal:
        tool.inspect(write_image(tmp_path, bytes(65536)))
    assert "none of the modelled schemes" in str(refusal.value)


def test_cli_exit_codes(tmp_path):
    good = write_image(tmp_path, cartridge(PROGRAM))
    ok = subprocess.run([sys.executable, str(TOOL), "--inspect", str(good), "--json"],
                        capture_output=True, text=True, timeout=120)
    assert ok.returncode == 0, ok.stderr
    payload = json.loads(ok.stdout)
    assert payload["conversion"]["class"] == "R1"

    bad = tmp_path / "short.a26"
    bad.write_bytes(b"\x00" * 1234)
    refused = subprocess.run([sys.executable, str(TOOL), "--inspect", str(bad)],
                             capture_output=True, text=True, timeout=120)
    assert refused.returncode == 1
    assert "REFUSED" in refused.stderr

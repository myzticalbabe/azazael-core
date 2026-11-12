"""Microcode MSR reader for Python 3.16t free-threaded.

Provides access to Model-Specific Registers (MSRs), particularly
MSR 0x8B (IA32_MICROCODE_UPDATE_STATUS) and related microcode registers.
"""

import ctypes
import platform
import sys
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class MicrocodeInfo:
    """Microcode update status from MSR 0x8B."""
    # MSR 0x8B - IA32_MICROCODE_UPDATE_STATUS
    revision: int  # Bits 0-31: Microcode revision
    patch_load_status: int  # Bits 32-63: Patch load status
    raw_value: int  # Full 64-bit value

    @property
    def is_loaded(self) -> bool:
        """Check if microcode patch is loaded."""
        return (self.patch_load_status & 0x1) != 0

    @property
    def patch_id(self) -> int:
        """Get the patch ID (revision)."""
        return self.revision


@dataclass(frozen=True)
class MSRResult:
    """Result of an MSR read operation."""
    msr: int
    eax: int  # Low 32 bits
    edx: int  # High 32 bits
    value: int  # Combined 64-bit value
    success: bool
    error: str = ""


class MSRReader:
    """Model-Specific Register reader using Windows kernel driver."""

    # Known MSR addresses
    IA32_TSC = 0x10
    IA32_PLATFORM_ID = 0x17
    IA32_APIC_BASE = 0x1B
    IA32_FEATURE_CONTROL = 0x3A
    IA32_TSC_DEADLINE = 0x6E0
    IA32_HWP_CAPABILITIES = 0x771
    IA32_HWP_REQUEST = 0x774
    IA32_HWP_STATUS = 0x777
    IA32_MICROCODE_UPDATE_STATUS = 0x8B
    IA32_MISC_ENABLE = 0x1A0
    IA32_ENERGY_PERF_BIAS = 0x1B0
    IA32_PKG_HDC_CONFIG = 0x652
    IA32_PM_ENABLE = 0x770
    IA32_HWP_INTERRUPT = 0x772
    IA32_HWP_ACTIVITY = 0x773
    IA32_HWP_PECI = 0x775
    IA32_HWP_REQUEST_PKG = 0x776
    IA32_MC0_CTL = 0x400
    IA32_MC0_STATUS = 0x401
    IA32_MC0_ADDR = 0x402
    IA32_MC0_MISC = 0x403

    def __init__(self):
        self._driver_handle = None
        self._device_path = r"\\.\msr"
        self._ioctl_read_msr = 0x80002000  # Custom IOCTL for reading MSR
        self._ioctl_write_msr = 0x80002004  # Custom IOCTL for writing MSR

    def _open_driver(self) -> bool:
        """Open the MSR driver."""
        if platform.system() != "Windows":
            return False

        try:
            # Try to open the MSR device
            self._driver_handle = ctypes.windll.kernel32.CreateFileW(
                self._device_path,
                0xC0000000,  # GENERIC_READ | GENERIC_WRITE
                0,  # No sharing
                None,  # Default security
                3,  # OPEN_EXISTING
                0,  # Normal attributes
                None  # No template
            )
            return self._driver_handle != ctypes.c_void_p(-1).value
        except (AttributeError, OSError):
            return False

    def _close_driver(self) -> None:
        """Close the MSR driver."""
        if self._driver_handle and self._driver_handle != ctypes.c_void_p(-1).value:
            ctypes.windll.kernel32.CloseHandle(self._driver_handle)
            self._driver_handle = None

    def __enter__(self):
        self._open_driver()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._close_driver()

    def read_msr(self, msr: int, cpu: int = 0) -> MSRResult:
        """Read a Model-Specific Register."""
        if platform.system() != "Windows":
            return MSRResult(msr, 0, 0, 0, False, "MSR reading only supported on Windows")

        if not self._driver_handle or self._driver_handle == ctypes.c_void_p(-1).value:
            if not self._open_driver():
                return MSRResult(msr, 0, 0, 0, False, "Failed to open MSR driver")

        try:
            # Prepare input/output buffers
            class MSR_INPUT(ctypes.Structure):
                _fields_ = [
                    ("msr", ctypes.c_uint32),
                    ("cpu", ctypes.c_uint32),
                ]

            class MSR_OUTPUT(ctypes.Structure):
                _fields_ = [
                    ("eax", ctypes.c_uint32),
                    ("edx", ctypes.c_uint32),
                ]

            input_buf = MSR_INPUT(msr, cpu)
            output_buf = MSR_OUTPUT()
            bytes_returned = ctypes.c_uint32()

            success = ctypes.windll.kernel32.DeviceIoControl(
                self._driver_handle,
                self._ioctl_read_msr,
                ctypes.byref(input_buf),
                ctypes.sizeof(input_buf),
                ctypes.byref(output_buf),
                ctypes.sizeof(output_buf),
                ctypes.byref(bytes_returned),
                None
            )

            if success:
                value = (output_buf.edx << 32) | output_buf.eax
                return MSRResult(msr, output_buf.eax, output_buf.edx, value, True)
            else:
                error_code = ctypes.windll.kernel32.GetLastError()
                return MSRResult(msr, 0, 0, 0, False, f"DeviceIoControl failed: {error_code}")

        except (AttributeError, OSError) as e:
            return MSRResult(msr, 0, 0, 0, False, str(e))

    def write_msr(self, msr: int, value: int, cpu: int = 0) -> bool:
        """Write a Model-Specific Register (requires ring 0)."""
        if platform.system() != "Windows":
            return False

        if not self._driver_handle or self._driver_handle == ctypes.c_void_p(-1).value:
            if not self._open_driver():
                return False

        try:
            class MSR_WRITE_INPUT(ctypes.Structure):
                _fields_ = [
                    ("msr", ctypes.c_uint32),
                    ("cpu", ctypes.c_uint32),
                    ("eax", ctypes.c_uint32),
                    ("edx", ctypes.c_uint32),
                ]

            input_buf = MSR_WRITE_INPUT(msr, cpu, value & 0xFFFFFFFF, value >> 32)
            bytes_returned = ctypes.c_uint32()

            success = ctypes.windll.kernel32.DeviceIoControl(
                self._driver_handle,
                self._ioctl_write_msr,
                ctypes.byref(input_buf),
                ctypes.sizeof(input_buf),
                None,
                0,
                ctypes.byref(bytes_returned),
                None
            )

            return success != 0
        except (AttributeError, OSError):
            return False

    def read_microcode_status(self, cpu: int = 0) -> Optional[MicrocodeInfo]:
        """Read MSR 0x8B - Microcode Update Status."""
        result = self.read_msr(self.IA32_MICROCODE_UPDATE_STATUS, cpu)
        if not result.success:
            return None

        revision = result.eax & 0xFFFFFFFF
        patch_load_status = result.edx & 0xFFFFFFFF

        return MicrocodeInfo(
            revision=revision,
            patch_load_status=patch_load_status,
            raw_value=result.value
        )

    def read_all_core_msrs(self, cpu: int = 0) -> dict:
        """Read a set of commonly useful MSRs."""
        msrs = {
            "tsc": self.IA32_TSC,
            "platform_id": self.IA32_PLATFORM_ID,
            "apic_base": self.IA32_APIC_BASE,
            "feature_control": self.IA32_FEATURE_CONTROL,
            "tsc_deadline": self.IA32_TSC_DEADLINE,
            "microcode_status": self.IA32_MICROCODE_UPDATE_STATUS,
            "misc_enable": self.IA32_MISC_ENABLE,
            "energy_perf_bias": self.IA32_ENERGY_PERF_BIAS,
        }

        results = {}
        for name, msr_addr in msrs.items():
            result = self.read_msr(msr_addr, cpu)
            if result.success:
                results[name] = {
                    "msr": f"0x{msr_addr:X}",
                    "value": f"0x{result.value:016X}",
                    "eax": f"0x{result.eax:08X}",
                    "edx": f"0x{result.edx:08X}",
                }
            else:
                results[name] = {"error": result.error}

        return results


def read_msr(msr: int, cpu: int = 0) -> MSRResult:
    """Convenience function to read a single MSR."""
    with MSRReader() as reader:
        return reader.read_msr(msr, cpu)


def read_microcode_status(cpu: int = 0) -> Optional[MicrocodeInfo]:
    """Convenience function to read microcode status."""
    with MSRReader() as reader:
        return reader.read_microcode_status(cpu)


def get_microcode_revision() -> Optional[int]:
    """Get current microcode revision."""
    info = read_microcode_status()
    return info.revision if info else None


def is_microcode_loaded() -> bool:
    """Check if microcode patch is loaded."""
    info = read_microcode_status()
    return info.is_loaded if info else False


if __name__ == "__main__":
    print("MSR Reader Test")
    print("=" * 50)

    with MSRReader() as reader:
        # Read microcode status
        print("\nMicrocode Status (MSR 0x8B):")
        mc_info = reader.read_microcode_status()
        if mc_info:
            print(f"  Revision: 0x{mc_info.revision:08X}")
            print(f"  Patch Load Status: 0x{mc_info.patch_load_status:08X}")
            print(f"  Loaded: {mc_info.is_loaded}")
            print(f"  Raw: 0x{mc_info.raw_value:016X}")
        else:
            print("  Failed to read")

        # Read common MSRs
        print("\nCommon MSRs:")
        msrs = reader.read_all_core_msrs()
        for name, data in msrs.items():
            if "error" in data:
                print(f"  {name}: ERROR - {data['error']}")
            else:
                print(f"  {name} (MSR {data['msr']}): {data['value']}")
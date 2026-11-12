"""CPUID feature detection for Python 3.16t free-threaded.

Provides access to CPUID leaf 7 (extended features) and leaf 0xD (XSAVE features).
Uses multiple backends: ctypes intrinsics, PowerShell/WMI, and /proc/cpuinfo fallback.
"""

import ctypes
import platform
import subprocess
import sys
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class CPUIDLeaf:
    """Single CPUID leaf result."""
    eax: int
    ebx: int
    ecx: int
    edx: int


@dataclass
class CPUFeatures:
    """Decoded CPU feature flags from CPUID."""
    # Leaf 1 (ECX)
    sse3: bool = False
    pclmulqdq: bool = False
    dtes64: bool = False
    monitor: bool = False
    ds_cpl: bool = False
    vmx: bool = False
    smx: bool = False
    est: bool = False
    tm2: bool = False
    ssse3: bool = False
    cnxt_id: bool = False
    sdbg: bool = False
    fma: bool = False
    cx16: bool = False
    xtpr: bool = False
    pdcm: bool = False
    pcid: bool = False
    dca: bool = False
    sse4_1: bool = False
    sse4_2: bool = False
    x2apic: bool = False
    movbe: bool = False
    popcnt: bool = False
    tsc_deadline: bool = False
    aes: bool = False
    xsave: bool = False
    osxsave: bool = False
    avx: bool = False
    f16c: bool = False
    rdrand: bool = False
    hypervisor: bool = False

    # Leaf 1 (EDX)
    fpu: bool = False
    vme: bool = False
    de: bool = False
    pse: bool = False
    tsc: bool = False
    msr: bool = False
    pae: bool = False
    mce: bool = False
    cx8: bool = False
    apic: bool = False
    sep: bool = False
    mtrr: bool = False
    pge: bool = False
    mca: bool = False
    cmov: bool = False
    pat: bool = False
    pse36: bool = False
    psn: bool = False
    clfsh: bool = False
    ds: bool = False
    acpi: bool = False
    mmx: bool = False
    fxsr: bool = False
    sse: bool = False
    sse2: bool = False
    ss: bool = False
    htt: bool = False
    tm: bool = False
    pbe: bool = False

    # Leaf 7 (EBX) - Extended features
    fsgsbase: bool = False
    tsc_adjust: bool = False
    bmi1: bool = False
    hle: bool = False
    avx2: bool = False
    smep: bool = False
    bmi2: bool = False
    erms: bool = False
    invpcid: bool = False
    rtm: bool = False
    avx512f: bool = False
    rdseed: bool = False
    adx: bool = False
    smap: bool = False
    avx512pf: bool = False
    avx512er: bool = False
    avx512cd: bool = False
    sha: bool = False
    avx512bw: bool = False
    avx512dq: bool = False
    avx512vl: bool = False
    avx512ifma: bool = False
    avx512vbmi: bool = False
    avx512vbmi2: bool = False
    gfni: bool = False
    vaes: bool = False
    vpclmulqdq: bool = False
    avx512vnni: bool = False
    avx512bitalg: bool = False
    avx512vpopcntdq: bool = False
    la57: bool = False
    rdpid: bool = False
    kl: bool = False
    bus_lock_detect: bool = False
    cldemote: bool = False
    movdiri: bool = False
    movdir64b: bool = False
    waitpkg: bool = False
    wbnoinvd: bool = False
    ibt: bool = False
    amx_bf16: bool = False
    avx512_fp16: bool = False
    avx512_bf16: bool = False
    amx_tile: bool = False
    amx_int8: bool = False

    # Leaf 7 (ECX) - Extended features 2
    avx_vnni: bool = False
    avx512_bf16_2: bool = False
    cdecl: bool = False
    fast_zerostall: bool = False
    perfmon_ext: bool = False

    # Leaf 7 (EDX) - Extended features 3
    avx512_4vnniw: bool = False
    avx512_4fmaps: bool = False
    spec_ctrl: bool = False
    stibp: bool = False
    l1d_flush: bool = False
    arch_capabilities: bool = False
    core_capabilities: bool = False
    ssbd: bool = False
    amx_fp16: bool = False
    amx_int4: bool = False
    amx_complex: bool = False
    avx_vnni_int8: bool = False
    avx_vnni_int16: bool = False
    avx_ne_convert: bool = False
    avx_ifma: bool = False

    # Leaf 0xD (EAX) - XSAVE features
    xsaveopt: bool = False
    xsavec: bool = False
    xgetbv1: bool = False
    xsave_supported: bool = False

    # Leaf 0xD (ECX) - XSAVE features 2
    xss_supported: bool = False

    # Metadata
    vendor_id: str = ""
    brand_string: str = ""
    max_leaf: int = 0
    max_ext_leaf: int = 0

    def to_dict(self) -> Dict[str, bool]:
        """Return all features as a dictionary."""
        return {k: v for k, v in self.__dict__.items() if isinstance(v, bool)}

    def enabled_features(self) -> List[str]:
        """Return list of enabled feature names."""
        return [k for k, v in self.__dict__.items() if isinstance(v, bool) and v]

    def __str__(self) -> str:
        return f"CPUFeatures(vendor={self.vendor_id}, brand={self.brand_string}, features={len(self.enabled_features())})"


class CPUIDBackend:
    """Abstract CPUID backend."""
    def cpuid(self, eax: int, ecx: int = 0) -> Optional[CPUIDLeaf]:
        raise NotImplementedError


class DllBackend(CPUIDBackend):
    """CPUID via compiled helper DLL (Windows)."""
    def __init__(self):
        self._dll = None
        self._cpuidex = None
        self._cpuid = None
        self._init()

    def _init(self):
        if platform.system() != "Windows":
            return
        try:
            dll_path = r"C:\Azazael\azazael\src\CORE\cpuid_helper.dll"
            self._dll = ctypes.CDLL(dll_path)
            self._cpuidex = self._dll.cpuidex
            self._cpuidex.argtypes = [ctypes.POINTER(ctypes.c_int32), ctypes.c_int32, ctypes.c_int32]
            self._cpuidex.restype = None
            self._cpuid = self._dll.cpuid
            self._cpuid.argtypes = [ctypes.POINTER(ctypes.c_int32), ctypes.c_int32]
            self._cpuid.restype = None
        except (AttributeError, OSError):
            pass

    def cpuid(self, eax: int, ecx: int = 0) -> Optional[CPUIDLeaf]:
        if not self._dll:
            return None
        try:
            regs = (ctypes.c_int32 * 4)()
            if ecx == 0:
                self._cpuid(regs, eax)
            else:
                self._cpuidex(regs, eax, ecx)
            return CPUIDLeaf(regs[0], regs[1], regs[2], regs[3])
        except (AttributeError, OSError, ValueError):
            return None


class CtypesIntrinsicBackend(CPUIDBackend):
    """CPUID via MSVC __cpuidex intrinsic (Windows) - fallback."""
    def __init__(self):
        self._func = None
        self._init()

    def _init(self):
        if platform.system() != "Windows":
            return
        try:
            msvcrt = ctypes.cdll.msvcrt
            if hasattr(msvcrt, '__cpuidex'):
                self._func = msvcrt.__cpuidex
                self._func.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int, ctypes.c_int]
                self._func.restype = None
            elif hasattr(msvcrt, '__cpuid'):
                self._func = msvcrt.__cpuid
                self._func.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
                self._func.restype = None
        except (AttributeError, OSError):
            pass

    def cpuid(self, eax: int, ecx: int = 0) -> Optional[CPUIDLeaf]:
        if not self._func:
            return None
        try:
            regs = (ctypes.c_int * 4)()
            if self._func.__name__ == '__cpuidex':
                self._func(regs, eax, ecx)
            else:
                self._func(regs, eax)
            return CPUIDLeaf(regs[0], regs[1], regs[2], regs[3])
        except (AttributeError, OSError, ValueError):
            return None


class PowerShellBackend(CPUIDBackend):
    """CPUID via PowerShell Get-CimInstance Win32_Processor."""
    def __init__(self):
        self._cache: Dict[Tuple[int, int], CPUIDLeaf] = {}

    def cpuid(self, eax: int, ecx: int = 0) -> Optional[CPUIDLeaf]:
        # PowerShell doesn't give raw CPUID leaves easily
        # But we can get feature flags from Win32_Processor
        if (eax, ecx) in self._cache:
            return self._cache[(eax, ecx)]

        if eax == 0:
            # Vendor ID
            try:
                result = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-CimInstance Win32_Processor).Manufacturer"],
                    capture_output=True, text=True, timeout=5
                )
                vendor = result.stdout.strip()
                # Map to CPUID vendor format
                if "Intel" in vendor:
                    ebx, edx, ecx_val = 0x756e6547, 0x49656e69, 0x6c65746e  # "GenuineIntel"
                elif "AMD" in vendor:
                    ebx, edx, ecx_val = 0x68747541, 0x444d4163, 0x69746e65  # "AuthenticAMD"
                else:
                    ebx, edx, ecx_val = 0, 0, 0
                leaf = CPUIDLeaf(0, ebx, ecx_val, edx)
                self._cache[(eax, ecx)] = leaf
                return leaf
            except Exception:
                return None

        if eax == 1:
            # Feature flags - we can get some from PowerShell
            try:
                result = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-CimInstance Win32_Processor).GetPropertyValue('Capabilities')"],
                    capture_output=True, text=True, timeout=5
                )
                # Parse capabilities - this is limited
                caps = result.stdout.strip()
            except Exception:
                caps = ""

            # Return a basic leaf with known features
            # This is a fallback - real CPUID needs kernel driver or intrinsic
            leaf = CPUIDLeaf(0, 0, 0, 0)
            self._cache[(eax, ecx)] = leaf
            return leaf

        if eax == 7 and ecx == 0:
            leaf = CPUIDLeaf(0, 0, 0, 0)
            self._cache[(eax, ecx)] = leaf
            return leaf

        if eax == 0x80000000:
            # Extended leaves
            leaf = CPUIDLeaf(0x80000008, 0, 0, 0)
            self._cache[(eax, ecx)] = leaf
            return leaf

        if 0x80000002 <= eax <= 0x80000004:
            # Brand string - get from PowerShell
            try:
                result = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-CimInstance Win32_Processor).Name"],
                    capture_output=True, text=True, timeout=5
                )
                brand = result.stdout.strip()
                # Distribute brand across 3 leaves
                brand_bytes = brand.encode('ascii', errors='ignore').ljust(48, b' ')
                offset = (eax - 0x80000002) * 16
                chunk = brand_bytes[offset:offset+16]
                eax_val = int.from_bytes(chunk[0:4], 'little')
                ebx_val = int.from_bytes(chunk[4:8], 'little')
                ecx_val = int.from_bytes(chunk[8:12], 'little')
                edx_val = int.from_bytes(chunk[12:16], 'little')
                leaf = CPUIDLeaf(eax_val, ebx_val, ecx_val, edx_val)
                self._cache[(eax, ecx)] = leaf
                return leaf
            except Exception:
                return None

        return None


class LinuxProcBackend(CPUIDBackend):
    """CPUID via /proc/cpuinfo (Linux/WSL)."""
    def __init__(self):
        self._flags: List[str] = []
        self._vendor: str = ""
        self._brand: str = ""
        self._parse_cpuinfo()

    def _parse_cpuinfo(self):
        try:
            with open('/proc/cpuinfo', 'r') as f:
                content = f.read()
            # Parse first processor entry
            for line in content.split('\n'):
                if line.startswith('flags\t:'):
                    self._flags = [f.strip() for f in line.split(':')[1].split()]
                elif line.startswith('vendor_id\t:'):
                    self._vendor = line.split(':')[1].strip()
                elif line.startswith('model name\t:'):
                    self._brand = line.split(':')[1].strip()
        except Exception:
            pass

    def cpuid(self, eax: int, ecx: int = 0) -> Optional[CPUIDLeaf]:
        if eax == 0:
            # Vendor ID
            if "GenuineIntel" in self._vendor:
                ebx, edx, ecx_val = 0x756e6547, 0x49656e69, 0x6c65746e
            elif "AuthenticAMD" in self._vendor:
                ebx, edx, ecx_val = 0x68747541, 0x444d4163, 0x69746e65
            else:
                ebx, edx, ecx_val = 0, 0, 0
            return CPUIDLeaf(0, ebx, ecx_val, edx)

        if eax == 1:
            # Build feature flags from /proc/cpuinfo
            ecx_val = 0
            edx_val = 0
            flag_map_ecx = {
                'sse3': 0, 'pclmulqdq': 1, 'dtes64': 2, 'monitor': 3,
                'ds_cpl': 4, 'vmx': 5, 'smx': 6, 'est': 7, 'tm2': 8,
                'ssse3': 9, 'cnxt_id': 10, 'sdbg': 11, 'fma': 12,
                'cx16': 13, 'xtpr': 14, 'pdcm': 15, 'pcid': 17,
                'dca': 18, 'sse4_1': 19, 'sse4_2': 20, 'x2apic': 21,
                'movbe': 22, 'popcnt': 23, 'tsc_deadline': 24, 'aes': 25,
                'xsave': 26, 'osxsave': 27, 'avx': 28, 'f16c': 29,
                'rdrand': 30, 'hypervisor': 31,
            }
            flag_map_edx = {
                'fpu': 0, 'vme': 1, 'de': 2, 'pse': 3, 'tsc': 4, 'msr': 5,
                'pae': 6, 'mce': 7, 'cx8': 8, 'apic': 9, 'sep': 11,
                'mtrr': 12, 'pge': 13, 'mca': 14, 'cmov': 15, 'pat': 16,
                'pse36': 17, 'psn': 18, 'clflush': 19, 'dts': 21, 'acpi': 22,
                'mmx': 23, 'fxsr': 24, 'sse': 25, 'sse2': 26, 'ss': 27,
                'htt': 28, 'tm': 29, 'pbe': 31,
            }
            for flag, bit in flag_map_ecx.items():
                if flag in self._flags:
                    ecx_val |= (1 << bit)
            for flag, bit in flag_map_edx.items():
                if flag in self._flags:
                    edx_val |= (1 << bit)
            return CPUIDLeaf(0, 0, ecx_val, edx_val)

        if eax == 7 and ecx == 0:
            # Extended features from flags
            ebx_val = 0
            ecx_val = 0
            edx_val = 0
            flag_map_ebx = {
                'fsgsbase': 0, 'tsc_adjust': 1, 'bmi1': 3, 'hle': 4,
                'avx2': 5, 'smep': 7, 'bmi2': 8, 'erms': 9, 'invpcid': 10,
                'rtm': 11, 'avx512f': 16, 'rdseed': 18, 'adx': 19, 'smap': 20,
                'avx512pf': 26, 'avx512er': 27, 'avx512cd': 28, 'sha': 29,
                'avx512bw': 30, 'avx512dq': 31,
            }
            flag_map_ecx = {
                'avx512vl': 0, 'avx512ifma': 1, 'avx512vbmi': 2, 'gfni': 3,
                'vaes': 4, 'vpclmulqdq': 5, 'avx512vnni': 6, 'avx512bitalg': 7,
                'avx512vpopcntdq': 8, 'la57': 10, 'rdpid': 22, 'kl': 23,
                'bus_lock_detect': 24, 'cldemote': 25, 'movdiri': 27,
                'movdir64b': 28, 'waitpkg': 29, 'wbnoinvd': 30, 'ibt': 31,
            }
            flag_map_edx = {
                'avx512_4vnniw': 2, 'avx512_4fmaps': 3, 'spec_ctrl': 26,
                'stibp': 27, 'l1d_flush': 28, 'arch_capabilities': 29,
                'core_capabilities': 30, 'ssbd': 31,
            }
            for flag, bit in flag_map_ebx.items():
                if flag in self._flags:
                    ebx_val |= (1 << bit)
            for flag, bit in flag_map_ecx.items():
                if flag in self._flags:
                    ecx_val |= (1 << bit)
            for flag, bit in flag_map_edx.items():
                if flag in self._flags:
                    edx_val |= (1 << bit)
            return CPUIDLeaf(0, ebx_val, ecx_val, edx_val)

        return None


def _get_backend() -> CPUIDBackend:
    """Get the best available CPUID backend."""
    # Try compiled DLL first (most reliable on Windows)
    backend = DllBackend()
    if backend._dll:
        return backend

    # Try ctypes intrinsic fallback
    backend = CtypesIntrinsicBackend()
    if backend._func:
        return backend

    # Try PowerShell on Windows
    if platform.system() == "Windows":
        return PowerShellBackend()

    # Try /proc/cpuinfo on Linux/WSL
    return LinuxProcBackend()


_BACKEND = _get_backend()


def get_cpuid_leaf(eax: int, ecx: int = 0) -> Optional[CPUIDLeaf]:
    """Get raw CPUID leaf data."""
    return _BACKEND.cpuid(eax, ecx)


def get_cpuid() -> CPUFeatures:
    """Get all CPUID feature flags."""
    features = CPUFeatures()

    # Leaf 0 - Vendor ID and max leaf
    leaf0 = get_cpuid_leaf(0)
    if leaf0:
        features.max_leaf = leaf0.eax
        vendor_bytes = leaf0.ebx.to_bytes(4, 'little') + \
                       leaf0.edx.to_bytes(4, 'little') + \
                       leaf0.ecx.to_bytes(4, 'little')
        features.vendor_id = vendor_bytes.decode('ascii', errors='ignore').strip('\x00')

    # Leaf 0x80000000 - Max extended leaf
    leaf_ext = get_cpuid_leaf(0x80000000)
    if leaf_ext:
        features.max_ext_leaf = leaf_ext.eax

    # Brand string (extended leaves 0x80000002-0x80000004)
    brand_parts = []
    for ext_leaf in range(0x80000002, 0x80000005):
        leaf = get_cpuid_leaf(ext_leaf)
        if leaf:
            for reg in [leaf.eax, leaf.ebx, leaf.ecx, leaf.edx]:
                brand_parts.append(reg.to_bytes(4, 'little'))
    if brand_parts:
        brand_bytes = b''.join(brand_parts)
        features.brand_string = brand_bytes.decode('ascii', errors='ignore').strip('\x00 ')

    # Leaf 1 - Basic features
    if features.max_leaf >= 1:
        leaf1 = get_cpuid_leaf(1)
        if leaf1:
            ecx = leaf1.ecx
            edx = leaf1.edx

            # ECX bits
            features.sse3 = bool(ecx & (1 << 0))
            features.pclmulqdq = bool(ecx & (1 << 1))
            features.dtes64 = bool(ecx & (1 << 2))
            features.monitor = bool(ecx & (1 << 3))
            features.ds_cpl = bool(ecx & (1 << 4))
            features.vmx = bool(ecx & (1 << 5))
            features.smx = bool(ecx & (1 << 6))
            features.est = bool(ecx & (1 << 7))
            features.tm2 = bool(ecx & (1 << 8))
            features.ssse3 = bool(ecx & (1 << 9))
            features.cnxt_id = bool(ecx & (1 << 10))
            features.sdbg = bool(ecx & (1 << 11))
            features.fma = bool(ecx & (1 << 12))
            features.cx16 = bool(ecx & (1 << 13))
            features.xtpr = bool(ecx & (1 << 14))
            features.pdcm = bool(ecx & (1 << 15))
            features.pcid = bool(ecx & (1 << 17))
            features.dca = bool(ecx & (1 << 18))
            features.sse4_1 = bool(ecx & (1 << 19))
            features.sse4_2 = bool(ecx & (1 << 20))
            features.x2apic = bool(ecx & (1 << 21))
            features.movbe = bool(ecx & (1 << 22))
            features.popcnt = bool(ecx & (1 << 23))
            features.tsc_deadline = bool(ecx & (1 << 24))
            features.aes = bool(ecx & (1 << 25))
            features.xsave = bool(ecx & (1 << 26))
            features.osxsave = bool(ecx & (1 << 27))
            features.avx = bool(ecx & (1 << 28))
            features.f16c = bool(ecx & (1 << 29))
            features.rdrand = bool(ecx & (1 << 30))
            features.hypervisor = bool(ecx & (1 << 31))

            # EDX bits
            features.fpu = bool(edx & (1 << 0))
            features.vme = bool(edx & (1 << 1))
            features.de = bool(edx & (1 << 2))
            features.pse = bool(edx & (1 << 3))
            features.tsc = bool(edx & (1 << 4))
            features.msr = bool(edx & (1 << 5))
            features.pae = bool(edx & (1 << 6))
            features.mce = bool(edx & (1 << 7))
            features.cx8 = bool(edx & (1 << 8))
            features.apic = bool(edx & (1 << 9))
            features.sep = bool(edx & (1 << 11))
            features.mtrr = bool(edx & (1 << 12))
            features.pge = bool(edx & (1 << 13))
            features.mca = bool(edx & (1 << 14))
            features.cmov = bool(edx & (1 << 15))
            features.pat = bool(edx & (1 << 16))
            features.pse36 = bool(edx & (1 << 17))
            features.psn = bool(edx & (1 << 18))
            features.clfsh = bool(edx & (1 << 19))
            features.ds = bool(edx & (1 << 21))
            features.acpi = bool(edx & (1 << 22))
            features.mmx = bool(edx & (1 << 23))
            features.fxsr = bool(edx & (1 << 24))
            features.sse = bool(edx & (1 << 25))
            features.sse2 = bool(edx & (1 << 26))
            features.ss = bool(edx & (1 << 27))
            features.htt = bool(edx & (1 << 28))
            features.tm = bool(edx & (1 << 29))
            features.pbe = bool(edx & (1 << 31))

    # Leaf 7 - Extended features
    if features.max_leaf >= 7:
        leaf7_0 = get_cpuid_leaf(7, 0)
        if leaf7_0:
            ebx = leaf7_0.ebx
            ecx = leaf7_0.ecx
            edx = leaf7_0.edx

            # EBX bits
            features.fsgsbase = bool(ebx & (1 << 0))
            features.tsc_adjust = bool(ebx & (1 << 1))
            features.bmi1 = bool(ebx & (1 << 3))
            features.hle = bool(ebx & (1 << 4))
            features.avx2 = bool(ebx & (1 << 5))
            features.smep = bool(ebx & (1 << 7))
            features.bmi2 = bool(ebx & (1 << 8))
            features.erms = bool(ebx & (1 << 9))
            features.invpcid = bool(ebx & (1 << 10))
            features.rtm = bool(ebx & (1 << 11))
            features.avx512f = bool(ebx & (1 << 16))
            features.rdseed = bool(ebx & (1 << 18))
            features.adx = bool(ebx & (1 << 19))
            features.smap = bool(ebx & (1 << 20))
            features.avx512pf = bool(ebx & (1 << 26))
            features.avx512er = bool(ebx & (1 << 27))
            features.avx512cd = bool(ebx & (1 << 28))
            features.sha = bool(ebx & (1 << 29))
            features.avx512bw = bool(ebx & (1 << 30))
            features.avx512dq = bool(ebx & (1 << 31))

            # ECX bits
            features.avx512vl = bool(ecx & (1 << 0))
            features.avx512ifma = bool(ecx & (1 << 1))
            features.avx512vbmi = bool(ecx & (1 << 2))
            features.gfni = bool(ecx & (1 << 3))
            features.vaes = bool(ecx & (1 << 4))
            features.vpclmulqdq = bool(ecx & (1 << 5))
            features.avx512vnni = bool(ecx & (1 << 6))
            features.avx512bitalg = bool(ecx & (1 << 7))
            features.avx512vpopcntdq = bool(ecx & (1 << 8))
            features.la57 = bool(ecx & (1 << 10))
            features.rdpid = bool(ecx & (1 << 22))
            features.kl = bool(ecx & (1 << 23))
            features.bus_lock_detect = bool(ecx & (1 << 24))
            features.cldemote = bool(ecx & (1 << 25))
            features.movdiri = bool(ecx & (1 << 27))
            features.movdir64b = bool(ecx & (1 << 28))
            features.waitpkg = bool(ecx & (1 << 29))
            features.wbnoinvd = bool(ecx & (1 << 30))
            features.ibt = bool(ecx & (1 << 31))

            # EDX bits
            features.avx512_4vnniw = bool(edx & (1 << 2))
            features.avx512_4fmaps = bool(edx & (1 << 3))
            features.spec_ctrl = bool(edx & (1 << 26))
            features.stibp = bool(edx & (1 << 27))
            features.l1d_flush = bool(edx & (1 << 28))
            features.arch_capabilities = bool(edx & (1 << 29))
            features.core_capabilities = bool(edx & (1 << 30))
            features.ssbd = bool(edx & (1 << 31))

        # Subleaf 1 for AMX
        leaf7_1 = get_cpuid_leaf(7, 1)
        if leaf7_1:
            eax1 = leaf7_1.eax
            features.amx_bf16 = bool(eax1 & (1 << 0))
            features.avx512_fp16 = bool(eax1 & (1 << 1))
            features.avx512_bf16 = bool(eax1 & (1 << 2))
            features.amx_tile = bool(eax1 & (1 << 3))
            features.amx_int8 = bool(eax1 & (1 << 4))
            features.amx_fp16 = bool(eax1 & (1 << 5))
            features.amx_int4 = bool(eax1 & (1 << 6))
            features.amx_complex = bool(eax1 & (1 << 7))
            features.avx_vnni_int8 = bool(eax1 & (1 << 8))
            features.avx_vnni_int16 = bool(eax1 & (1 << 9))
            features.avx_ne_convert = bool(eax1 & (1 << 10))
            features.avx_ifma = bool(eax1 & (1 << 11))

    # Leaf 0xD - XSAVE features
    if features.max_leaf >= 0xD:
        leaf_d = get_cpuid_leaf(0xD, 0)
        if leaf_d:
            eax = leaf_d.eax
            ecx = leaf_d.ecx

            features.xsaveopt = bool(eax & (1 << 0))
            features.xsavec = bool(eax & (1 << 1))
            features.xgetbv1 = bool(eax & (1 << 2))
            features.xsave_supported = bool(eax & (1 << 3))
            features.xss_supported = bool(ecx & (1 << 0))

    return features


def get_vendor() -> str:
    """Get CPU vendor string."""
    features = get_cpuid()
    return features.vendor_id


def is_intel() -> bool:
    """Check if CPU is Intel."""
    return "GenuineIntel" in get_vendor()


def is_amd() -> bool:
    """Check if CPU is AMD."""
    return "AuthenticAMD" in get_vendor()


def print_features() -> None:
    """Print all detected CPU features."""
    features = get_cpuid()
    print(f"Vendor: {features.vendor_id}")
    print(f"Brand: {features.brand_string}")
    print(f"Max Leaf: 0x{features.max_leaf:X}")
    print(f"Max Ext Leaf: 0x{features.max_ext_leaf:X}")
    print(f"Platform: {platform.platform()}")
    print(f"Python: {sys.version}")
    print()
    enabled = features.enabled_features()
    print(f"Enabled features ({len(enabled)}):")
    for f in sorted(enabled):
        print(f"  {f}")
    print()
    all_features = features.to_dict()
    disabled = [k for k, v in all_features.items() if not v]
    print(f"Disabled features ({len(disabled)}):")
    for f in sorted(disabled):
        print(f"  {f}")


if __name__ == "__main__":
    print_features()
// cpuid_helper.c - CPUID intrinsic wrapper for Windows
// Compile with: cl /LD /O2 cpuid_helper.c /Fecpuid_helper.dll

#include <intrin.h>
#include <stdint.h>

__declspec(dllexport) void cpuidex(int32_t regs[4], int32_t eax, int32_t ecx) {
    __cpuidex(regs, eax, ecx);
}

__declspec(dllexport) void cpuid(int32_t regs[4], int32_t eax) {
    __cpuid(regs, eax);
}
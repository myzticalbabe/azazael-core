@echo off
call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvarsall.bat" x64
cl /LD /O2 C:\Azazael\azazael\src\CORE\cpuid_helper.c /FeC:\Azazael\azazael\src\CORE\cpuid_helper.dll
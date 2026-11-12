# Pin an existing .lnk to the Windows 11 taskbar via the undocumented
# IPinnedList3 COM interface (the public taskbarpin verb is blocked on
# Windows 11 for .lnk files). Works for shortcuts that target an .exe,
# including msedge.exe/chrome.exe with an HTML file as an argument.
#
# Usage: powershell -File pin_to_taskbar.ps1 C:\path\to\shortcut.lnk

param([Parameter(Mandatory = $true)][string]$ShortcutPath)

$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("0DD79AE2-D156-45D4-9EEB-3B549769E940")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IPinnedList3
{
    void Unpin([MarshalAs(UnmanagedType.Interface)] object punk);
    void GetPinnedItem([MarshalAs(UnmanagedType.Interface)] object punk, out IntPtr ppidl);
    void GetChangeCount(ref uint pdwChangeCount);
    void EnumItems([MarshalAs(UnmanagedType.Interface)] out object ppenum);
    [PreserveSig] int Modify([MarshalAs(UnmanagedType.Interface)] object punk);
    void GetPinnedListID(ref Guid pguidPinnedListId);
}

[ComImport, Guid("90AA3A4E-1CBA-4233-B8BB-535773D48449")]
[ClassInterface(ClassInterfaceType.None)]
public class PinnedList3
{
}

public static class Pinner
{
    public static int Pin(string path)
    {
        Type t = Type.GetTypeFromCLSID(new Guid("90AA3A4E-1CBA-4233-B8BB-535773D48449"));
        object com = Activator.CreateInstance(t);
        IPinnedList3 list = (IPinnedList3)com;
        object item = PinnedList3Item(path);
        return list.Modify(item);
    }

    private static object PinnedList3Item(string path)
    {
        // Build an IShellItem for the shortcut path
        Type shellItemType = Type.GetTypeFromCLSID(new Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7"));
        object shellItem = Activator.CreateInstance(shellItemType);
        Guid iidIShellItem = new Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE");
        object item = null;
        // SHCreateItemFromParsingName in shell32
        int hr = SHCreateItemFromParsingName(path, IntPtr.Zero, ref iidIShellItem, out item);
        if (hr != 0) throw new COMException("SHCreateItemFromParsingName failed", hr);
        return item;
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    private static extern int SHCreateItemFromParsingName(
        [MarshalAs(UnmanagedType.LPWStr)] string pszPath,
        IntPtr pbc,
        ref Guid riid,
        [MarshalAs(UnmanagedType.Interface)] out object ppv);
}
"@ -ErrorAction SilentlyContinue

if (-not (Test-Path $ShortcutPath)) {
    Write-Output "ERROR: shortcut not found: $ShortcutPath"
    exit 1
}

try {
    $hr = [Pinner]::Pin($ShortcutPath)
    if ($hr -eq 0) {
        Write-Output "PINNED: $ShortcutPath"
    } else {
        Write-Output "FAILED hr=0x$($hr.ToString('X8')): $ShortcutPath"
        exit 2
    }
} catch {
    Write-Output "ERROR pinning: $($_.Exception.Message)"
    exit 3
}

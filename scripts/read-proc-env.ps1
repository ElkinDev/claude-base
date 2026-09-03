# Print selected environment variables of another process of the same user, read from its
# PEB (NtQueryInformationProcess + ReadProcessMemory). Read-only. 64-bit host and target.
param([int]$ProcessId, [string[]]$Names = @("CLAUDE_ROLE", "CLAUDE_CODE_DISABLE_1M_CONTEXT", "CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_CONFIG_DIR"))

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class ProcEnv {
    [StructLayout(LayoutKind.Sequential)]
    public struct PROCESS_BASIC_INFORMATION {
        public IntPtr Reserved1; public IntPtr PebBaseAddress; public IntPtr Reserved2_0;
        public IntPtr Reserved2_1; public IntPtr UniqueProcessId; public IntPtr Reserved3;
    }
    [DllImport("ntdll.dll")] public static extern int NtQueryInformationProcess(IntPtr h, int cls, ref PROCESS_BASIC_INFORMATION info, int len, out int ret);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern bool ReadProcessMemory(IntPtr h, IntPtr addr, byte[] buf, int size, out int read);
    [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr h);
    public static string Read(int pid) {
        IntPtr h = OpenProcess(0x0400 | 0x0010, false, pid);
        if (h == IntPtr.Zero) throw new Exception("OpenProcess failed " + Marshal.GetLastWin32Error());
        try {
            var pbi = new PROCESS_BASIC_INFORMATION(); int ret;
            int st = NtQueryInformationProcess(h, 0, ref pbi, Marshal.SizeOf(pbi), out ret);
            if (st != 0) throw new Exception("NtQueryInformationProcess " + st);
            byte[] ptr = new byte[8]; int n;
            // PEB + 0x20 = ProcessParameters (x64)
            if (!ReadProcessMemory(h, pbi.PebBaseAddress + 0x20, ptr, 8, out n)) throw new Exception("read PEB failed");
            IntPtr pp = (IntPtr)BitConverter.ToInt64(ptr, 0);
            // RTL_USER_PROCESS_PARAMETERS + 0x80 = Environment (x64), + 0x3F0 = EnvironmentSize
            if (!ReadProcessMemory(h, pp + 0x80, ptr, 8, out n)) throw new Exception("read params failed");
            IntPtr env = (IntPtr)BitConverter.ToInt64(ptr, 0);
            if (!ReadProcessMemory(h, pp + 0x3F0, ptr, 8, out n)) throw new Exception("read size failed");
            long size = BitConverter.ToInt64(ptr, 0);
            if (size <= 0 || size > 4 * 1024 * 1024) size = 256 * 1024;
            byte[] buf = new byte[size];
            if (!ReadProcessMemory(h, env, buf, (int)size, out n)) throw new Exception("read env failed " + Marshal.GetLastWin32Error());
            return System.Text.Encoding.Unicode.GetString(buf, 0, n);
        } finally { CloseHandle(h); }
    }
}
"@

$block = [ProcEnv]::Read($ProcessId)
$vars = $block -split "`0" | Where-Object { $_ -match '^[^=]+=' }
"pid $ProcessId vars=$($vars.Count)"
foreach ($nm in $Names) {
    $hit = $vars | Where-Object { $_ -like "$nm=*" } | Select-Object -First 1
    if ($hit) { "  $hit" } else { "  $nm=(unset)" }
}

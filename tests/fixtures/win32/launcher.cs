// A stand-in for the python.exe of a Windows venv, which is a launcher. Like
// CPython's, it starts the real interpreter at once, here the one that
// MATHIMATOR_TEST_REAL_PYTHON names, with its own arguments, its stdio and
// __PYVENV_LAUNCHER__ set to its own path. It waits for that interpreter and
// exits with its exit code.
using System;
using System.Diagnostics;
using System.Reflection;
class Launcher {
    static int Main() {
        string line = Environment.CommandLine;
        // The arguments follow this program's own name, which may be quoted.
        int end = line.StartsWith("\"") ? line.IndexOf('"', 1) + 1 : line.IndexOf(' ');
        var info = new ProcessStartInfo(Environment.GetEnvironmentVariable("MATHIMATOR_TEST_REAL_PYTHON"));
        info.Arguments = end <= 0 ? "" : line.Substring(end).TrimStart();
        info.UseShellExecute = false;
        info.EnvironmentVariables["__PYVENV_LAUNCHER__"] = Assembly.GetEntryAssembly().Location;
        using (var child = Process.Start(info)) {
            child.WaitForExit();
            return child.ExitCode;
        }
    }
}

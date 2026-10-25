// The manim.exe stand-in for the Job Object reap tests. It waits until the
// MATHIMATOR_TEST_GO file exists, starts MATHIMATOR_TEST_LATEX and
// MATHIMATOR_TEST_DVISVGM with UseShellExecute false, writes their pids to
// MATHIMATOR_TEST_PIDS and sleeps 30 s.
using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
class JobTree {
    static int Main() {
        string go = Environment.GetEnvironmentVariable("MATHIMATOR_TEST_GO");
        string pids = Environment.GetEnvironmentVariable("MATHIMATOR_TEST_PIDS");
        string latex = Environment.GetEnvironmentVariable("MATHIMATOR_TEST_LATEX");
        string dvi = Environment.GetEnvironmentVariable("MATHIMATOR_TEST_DVISVGM");
        if (go == null || pids == null || latex == null || dvi == null) return 2;
        var deadline = DateTime.UtcNow.AddSeconds(20);
        while (!File.Exists(go)) {
            if (DateTime.UtcNow > deadline) return 3;
            Thread.Sleep(20);
        }
        var latexProc = StartHidden(latex);
        var dviProc = StartHidden(dvi);
        File.WriteAllText(pids, latexProc.Id + "\n" + dviProc.Id + "\n");
        Thread.Sleep(30000);
        return 0;
    }
    static Process StartHidden(string path) {
        var info = new ProcessStartInfo();
        info.FileName = path;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        return Process.Start(info);
    }
}

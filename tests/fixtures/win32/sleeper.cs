// A stand-in program that ignores its arguments and sleeps. A "<name>.ms" file
// next to "<name>.exe" holds the milliseconds; without one it sleeps 30 s.
using System.IO;
using System.Reflection;
using System.Threading;
class Sleeper {
    static void Main() {
        string setting = Path.ChangeExtension(Assembly.GetEntryAssembly().Location, ".ms");
        Thread.Sleep(File.Exists(setting) ? int.Parse(File.ReadAllText(setting)) : 30000);
    }
}

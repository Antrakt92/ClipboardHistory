using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("ClipboardHistory")]
[assembly: AssemblyDescription("ClipboardHistory")]
[assembly: AssemblyProduct("ClipboardHistory")]
[assembly: AssemblyVersion("1.0.1.0")]
[assembly: AssemblyFileVersion("1.0.1.0")]

internal static class ClipboardHistoryLauncher
{
    [STAThread]
    private static int Main(string[] args)
    {
        bool check = args.Length == 3 && args[0] == "--check";
        bool wait = args.Length == 3 && args[0] == "--wait";
        int offset = check || wait ? 1 : 0;
        if (args.Length - offset != 2) return 2;
        string python = args[offset], script = args[offset + 1];
        if (!Path.IsPathRooted(python) || !Path.IsPathRooted(script) ||
            !File.Exists(python) || !File.Exists(script)) return 3;
        if (check) return 0;
        try
        {
            // No shell expansion: spaces, &, ! and Unicode remain literal paths.
            var info = new ProcessStartInfo(python, "\"" + script + "\"");
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.WorkingDirectory = Path.GetDirectoryName(script);
            using (Process child = Process.Start(info))
            {
                if (child == null) return 4;
                if (wait) { child.WaitForExit(); return child.ExitCode; }
            }
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show("ClipboardHistory could not start.\n" + error.Message,
                            "ClipboardHistory", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}

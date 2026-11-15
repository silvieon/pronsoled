"""Run Printrun's pronsole without its GUI dependencies.

printrun.settings imports wxPython and printrun.gui at module level, although
pronsole never opens a window. Empty stand-ins satisfy those imports, so
pronsole needs only pyserial and platformdirs (see install.sh).
"""
import sys
import types

if __name__ == "__main__":
    sys.modules["wx"] = types.ModuleType("wx")
    sys.modules["printrun.gui"] = types.ModuleType("printrun.gui")
    sys.modules["printrun.gui.widgets"] = types.SimpleNamespace(get_space=lambda name: 0)

    from printrun.pronsole import pronsole

    interp = pronsole()
    interp.parse_cmdline(sys.argv[1:])
    try:
        interp.cmdloop()
    finally:
        interp.p.disconnect()

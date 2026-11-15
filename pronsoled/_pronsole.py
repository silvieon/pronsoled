"""Run Printrun's pronsole with this interpreter.

Used when Printrun is installed in pronsoled's own environment, so the daemon
does not depend on whichever pronsole.py happens to be on PATH (and on which
Python that script's shebang points to). Mirrors Printrun's pronsole.py.
"""
import sys

# Run as a script, sys.path[0] is the pronsoled package directory; drop it so
# its modules (cli, daemon) cannot shadow anything Printrun imports.
if sys.path and __file__.startswith(sys.path[0]):
    sys.path.pop(0)

import logging  # noqa: E402
import traceback  # noqa: E402

from printrun.pronsole import pronsole  # noqa: E402


def main() -> None:
    interp = pronsole()
    interp.parse_cmdline(sys.argv[1:])
    try:
        interp.cmdloop()
    except SystemExit:
        interp.p.disconnect()
    except Exception:
        logging.error("Caught an exception, exiting:\n" + traceback.format_exc())
        interp.p.disconnect()
        sys.exit(1)


if __name__ == "__main__":
    main()

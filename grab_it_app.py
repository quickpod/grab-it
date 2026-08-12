#!/usr/bin/env python3
r"""GrabIt entry point (built into GrabIt.exe). GUI with no args, CLI with args."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    argv = sys.argv[1:]
    if argv:
        from grabit import __main__ as cli
        if hasattr(cli, 'main'):
            try:
                return cli.main(argv)
            except TypeError:
                sys.argv = ['grabit', *argv]; return cli.main()
        sys.argv = ['grabit', *argv]
        import runpy; runpy.run_module('grabit', run_name='__main__'); return 0
    from grabit import gui
    return gui.main() or 0


if __name__ == '__main__':
    sys.exit(main() or 0)

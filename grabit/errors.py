"""Error types for grabit."""


class GrabItError(Exception):
    """Raised for any recoverable failure in a grabit operation.

    Every public function and the ``Download`` object raise this (and only this)
    on a failure a caller can reasonably handle -- a bad URL, an HTTP error, a
    checksum mismatch, a broken connection.  The CLI and the GUI each catch this
    single type and present a clean message instead of a traceback.
    """

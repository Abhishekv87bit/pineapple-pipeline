"""Simple CLI spinner for long-running operations."""
import sys
import threading
import time


class Spinner:
    """Context manager that shows a spinner while work is in progress.

    Usage:
        with Spinner("Calling LLM..."):
            result = slow_operation()
    """

    # Braille chars for Unicode-capable terminals; ASCII fallback for Windows cp1252
    _CHARS_UNICODE = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"
    _CHARS_ASCII = "-\\|/"
    _DONE_UNICODE = "\u2713"
    _DONE_ASCII = "+"

    def __init__(self, message: str = "Working..."):
        self.message = message
        self._stop = threading.Event()
        self._thread = None
        # Detect if the terminal can handle Unicode
        enc = getattr(sys.stdout, "encoding", "ascii") or "ascii"
        try:
            "\u2713".encode(enc)
            self._chars = self._CHARS_UNICODE
            self._done = self._DONE_UNICODE
        except (UnicodeEncodeError, LookupError):
            self._chars = self._CHARS_ASCII
            self._done = self._DONE_ASCII

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            char = self._chars[i % len(self._chars)]
            sys.stdout.write(f"\r  {char} {self.message}")
            sys.stdout.flush()
            i += 1
            self._stop.wait(0.1)
        sys.stdout.write(f"\r  {self._done} {self.message}\n")
        sys.stdout.flush()

    def __enter__(self):
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

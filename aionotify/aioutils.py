# Copyright (c) 2016 The aionotify project
# This code is distributed under the two-clause BSD License.
from typing import Optional
from asyncio import (
    get_event_loop, ReadTransport, StreamReader,
    StreamReaderProtocol, AbstractEventLoop)
from asyncio.futures import Future
import errno
import logging
import os

logger = logging.getLogger("asyncio.aionotify")


class UnixFileDescriptorTransport(ReadTransport):
    # Inspired from asyncio.unix_events._UnixReadPipeTransport
    max_size = 1024

    def __init__(self, loop, fileno, protocol, waiter: Optional[Future]):
        super().__init__()
        self._loop = loop
        self._fileno = fileno
        self._protocol = protocol

        self._active = False
        self._closing = False

        self._loop.call_soon(self._protocol.connection_made, self)
        # only start reading when connection_made() has been called.
        self._loop.call_soon(self.resume_reading)
        # only wake up the waiter when connection_made() has been called.
        self._loop.call_soon(self._notify_waiter, waiter)

    def _notify_waiter(self, waiter):
        if waiter.cancelled():  # pragma: nocover
            pass
        else:
            waiter.set_result(None)

    def _read_ready(self):
        """Called by the event loop whenever the fd is ready for reading."""

        try:
            data = os.read(self._fileno, self.max_size)
        except InterruptedError:  # pragma: nocover
            # No worries ;)
            pass
        except OSError as exc:  # pragma: nocover
            # Some OS-level problem, crash.
            self._fatal_error(exc, "Fatal read error on file descriptor read")
        else:
            if data:
                self._protocol.data_received(data)
            else:  # pragma: nocover
                # We reached end-of-file.
                if self._loop.get_debug():
                    logger.info("%r was closed by the kernel", self)
                self._closing = False
                self.pause_reading()
                self._loop.call_soon(self._protocol.eof_received)
                self._loop.call_soon(self._call_connection_lost, None)

    def pause_reading(self):
        """Public API: pause reading the transport."""
        self._loop.remove_reader(self._fileno)
        self._active = False

    def resume_reading(self):
        """Public API: resume transport reading."""
        self._loop.add_reader(self._fileno, self._read_ready)
        self._active = True

    def close(self):
        """Public API: close the transport."""
        if not self._closing:
            self._close()

    def _fatal_error(self, exc, message):  # pragma: nocover
        if isinstance(exc, OSError) and exc.errno == errno.EIO:
            if self._loop.get_debug():
                logger.debug("%r: %s", self, message, exc_info=True)
        else:
            self._loop.call_exception_handler({
                "message": message,
                "exception": exc,
                "transport": self,
                "protocol": self._protocol,
            })
        self._close(error=exc)

    def _close(self, error=None):
        """Actual closing code, both from manual close and errors."""
        self._closing = True
        self.pause_reading()
        self._loop.call_soon(self._call_connection_lost, error)

    def _call_connection_lost(self, error):
        """Finalize closing."""
        try:
            self._protocol.connection_lost(error)
        finally:
            os.close(self._fileno)
            self._fileno = None
            self._protocol = None
            self._loop = None

    def __repr__(self):  # pragma: nocover
        if self._active:
            status = "active"
        elif self._closing:
            status = "closing"
        elif self._fileno:
            status = "paused"
        else:
            status = "closed"

        parts = [
            self.__class__.__name__,
            status,
            "fd=%s" % self._fileno,
        ]
        return "<%s>" % " ".join(parts)


async def stream_from_fd(fd, loop: Optional[AbstractEventLoop] = None):
    """Recieve a streamer for a given file descriptor."""
    loop = loop if loop is not None else get_event_loop()
    reader = StreamReader(loop=loop)
    protocol = StreamReaderProtocol(reader, loop=loop)
    waiter: Future = Future(loop=loop)

    transport = UnixFileDescriptorTransport(
        loop=loop,
        fileno=fd,
        protocol=protocol,
        waiter=waiter,
    )

    try:
        await waiter
    except Exception:  # pragma: nocover
        transport.close()

    if loop.get_debug():
        logger.debug("Read fd %r connected: (%r, %r)", fd, transport, protocol)
    return reader, transport

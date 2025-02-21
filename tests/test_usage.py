# Copyright (c) 2016 The aionotify project
# This code is distributed under the two-clause BSD License.

from typing import Type
import asyncio
import logging
import os
import os.path
import tempfile
import unittest
from pathlib import Path

import aionotify

TestBase: Type[object]
try:
    TestBase = unittest.IsolatedAsyncioTestCase
except AttributeError:
    import asynctest  # type: ignore
    TestBase = asynctest.TestCase


AIODEBUG = bool(os.environ.get("PYTHONAIODEBUG") == "1")


if AIODEBUG:
    logger = logging.getLogger("asyncio")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(logging.StreamHandler())


TESTDIR = os.environ.get("AIOTESTDIR", Path(__file__).parent / "testevents")


class AIONotifyTestCase(TestBase):  # type: ignore
    forbid_get_event_loop = True
    timeout = 3.

    def setUp(self):
        # asynctest stuff. Drop this with python 3.7.
        if not getattr(self, "loop", None):
            self.loop = asyncio.get_event_loop()
        if AIODEBUG:
            self.loop.set_debug(True)
        self.watcher = aionotify.Watcher()
        self._testdir = tempfile.TemporaryDirectory(dir=TESTDIR)
        self.testdir = self._testdir.name
        self.testdirpath = Path(self.testdir)

        # Schedule a loop shutdown
        self.loop.call_later(self.timeout, self.loop.stop)

    def tearDown(self):
        if not self.watcher.closed:
            self.watcher.close()
        self._testdir.cleanup()
        self.assertFalse(os.path.exists(self.testdir))

    # Utility functions
    # =================

    # Those allow for more readable tests.

    def _touch(self, filename, *, parent=None):
        path = os.path.join(parent or self.testdir, filename)
        with open(path, "w") as f:
            f.write("")

    def _unlink(self, filename, *, parent=None):
        path = os.path.join(parent or self.testdir, filename)
        os.unlink(path)

    def _mkdir(self, dirname, *, parent=None):
        path = os.path.join(parent or self.testdir, dirname)
        os.mkdir(path)

    def _rmdir(self, dirname, *, parent=None):
        path = os.path.join(parent or self.testdir, dirname)
        os.rmdir(path)

    def _rename(self, source, target, *, parent=None):
        source_path = os.path.join(parent or self.testdir, source)
        target_path = os.path.join(parent or self.testdir, target)
        os.rename(source_path, target_path)

    def _assert_file_event(self, event, name, flags=aionotify.Flags.CREATE,
                           alias=None):
        """Check for an expected file event.

        Allows for more readable tests.
        """
        if alias is None:
            alias = self.testdir

        self.assertEqual(name, event.name)
        self.assertEqual(flags, event.flags)
        self.assertEqual(alias, event.alias)

    def _assert_watcher_closed(self):
        self.assertTrue(self.watcher.closed)

    async def _assert_no_events(self, timeout=0.1):
        """Ensure that no events are left in the queue."""
        task = self.watcher.get_event()
        try:
            result = await asyncio.wait_for(task, timeout)
        except asyncio.TimeoutError:
            # All fine: we didn't receive any event.
            pass
        else:
            raise AssertionError("Event %r occurred within timeout %s" % (result, timeout))


class SimpleUsageTests(AIONotifyTestCase):

    async def test_watch_before_start(self):
        """A watch call is valid before startup."""
        self.watcher.watch(self.testdir, aionotify.Flags.CREATE)
        await self.watcher.setup()

        # Touch a file: we get the event.
        self._touch("a")
        event = await self.watcher.get_event()
        self._assert_file_event(event, "a")

        # And it's over.
        await self._assert_no_events()

    async def test_watch_before_start_path(self):
        """A watch call is valid before startup, using a Path"""
        self.watcher.watch(self.testdirpath, aionotify.Flags.CREATE)
        await self.watcher.setup()

        # Touch a file: we get the event.
        self._touch("a")
        event = await self.watcher.get_event()
        self._assert_file_event(event, "a")

        # And it's over.
        await self._assert_no_events()

    async def test_watch_after_start(self):
        """A watch call is valid after startup."""
        await self.watcher.setup()
        self.watcher.watch(self.testdir, aionotify.Flags.CREATE)

        # Touch a file: we get the event.
        self._touch("a")
        event = await self.watcher.get_event()
        self._assert_file_event(event, "a")

        # And it's over.
        await self._assert_no_events()

    async def test_watcher_iterator(self):
        self.watcher.watch(self.testdir, aionotify.Flags.CREATE)

        self.loop.call_later(0.03, self._touch, "a")
        self.loop.call_later(0.03, self._touch, "b")
        self.loop.call_later(0.05, self.watcher.close)

        a_seen = False
        async for event in self.watcher:
            if not a_seen:
                self.assertEqual(event.name, "a")
                a_seen = True
            else:
                self.assertEqual(event.name, "b")

        # And it's closed.
        self._assert_watcher_closed()

    async def test_watcher_context_OK(self):
        self.watcher.watch(self.testdir, aionotify.Flags.CREATE)

        async with self.watcher:
            # Touch a file: we get the event.
            self._touch("a")
            event = await self.watcher.get_event()
            self._assert_file_event(event, "a")

        # And it's closed.
        self._assert_watcher_closed()

    async def test_watcher_context_KO(self):
        self.watcher.watch(self.testdir, aionotify.Flags.CREATE)

        with self.assertRaises(ZeroDivisionError):
            async with self.watcher:
                # Touch a file: we get the event.
                self._touch("a")
                event = await self.watcher.get_event()
                self._assert_file_event(event, "a")
                1/0

        # And it's closed.
        self._assert_watcher_closed()

    async def test_duplicate_alias_raises(self):
        """A watch call is valid after startup."""
        await self.watcher.setup()
        self.watcher.watch(
            self.testdir, aionotify.Flags.CREATE, alias="an alias")
        with self.assertRaises(ValueError):
            self.watcher.watch(
                self.testdir, aionotify.Flags.CREATE, alias="an alias")

    async def test_event_ordering(self):
        """Events should arrive in the order files where created."""
        await self.watcher.setup()
        self.watcher.watch(self.testdir, aionotify.Flags.CREATE)

        # Touch 2 files
        self._touch("a")
        self._touch("b")

        # Get the events
        event1 = await self.watcher.get_event()
        event2 = await self.watcher.get_event()
        self._assert_file_event(event1, "a")
        self._assert_file_event(event2, "b")

        # And it's over.
        await self._assert_no_events()

    async def test_filtering_events(self):
        """We only get targeted events."""
        await self.watcher.setup()
        self.watcher.watch(self.testdir, aionotify.Flags.CREATE)
        self._touch("a")
        event = await self.watcher.get_event()
        self._assert_file_event(event, "a")

        # Perform a filtered-out event; we shouldn't see anything
        self._unlink("a")
        await self._assert_no_events()

    async def test_watch_unwatch(self):
        """Watches can be removed."""
        self.watcher.watch(self.testdir, aionotify.Flags.CREATE)
        await self.watcher.setup()

        self.watcher.unwatch(self.testdir)
        await asyncio.sleep(0.1)

        # Touch a file; we shouldn't see anything.
        self._touch("a")
        await self._assert_no_events()

    async def test_watch_unwatch_before_drain(self):
        """Watches can be removed, no events occur afterwards."""
        self.watcher.watch(self.testdir, aionotify.Flags.CREATE)
        await self.watcher.setup()

        # Touch a file before unwatching
        self._touch("a")
        self.watcher.unwatch(self.testdir)

        # We shouldn't see anything.
        await self._assert_no_events()

    async def test_rename_detection(self):
        """A file rename can be detected through event cookies."""
        self.watcher.watch(self.testdir, aionotify.Flags.MOVED_FROM | aionotify.Flags.MOVED_TO)
        await self.watcher.setup()
        self._touch("a")

        # Rename a file => two events
        self._rename("a", "b")
        event1 = await self.watcher.get_event()
        event2 = await self.watcher.get_event()

        # We got moved_from then moved_to; they share the same cookie.
        self._assert_file_event(event1, "a", aionotify.Flags.MOVED_FROM)
        self._assert_file_event(event2, "b", aionotify.Flags.MOVED_TO)
        self.assertEqual(event1.cookie, event2.cookie)

        # And it's over.
        await self._assert_no_events()

    async def test_remove_directory(self):
        """ A deleted file or directory should be unwatched."""
        full_path = os.path.join(self.testdir, "a")
        self._mkdir("a")
        self.watcher.watch(full_path, aionotify.Flags.IGNORED)
        await self.watcher.setup()

        self._rmdir("a")
        event = await self.watcher.get_event()
        self._assert_file_event(event, "", flags=aionotify.Flags.IGNORED, alias=full_path)

        # Make sure we can watch the same path again (#2)
        self._mkdir("a")
        self.watcher.watch(full_path, aionotify.Flags.IGNORED)

        await self._assert_no_events()

    async def test_watch_after_created(self):
        """It should be possible to retry watching a file that didn't exist."""
        await self.watcher.setup()

        full_path = os.path.join(self.testdir, "a")
        with self.assertRaises(OSError):
            self.watcher.watch(full_path, aionotify.Flags.MODIFY)

        self._touch("a")
        self.watcher.watch(full_path, aionotify.Flags.MODIFY)

        await self._assert_no_events()


class ErrorTests(AIONotifyTestCase):
    """Test error cases."""

    async def test_watch_nonexistent(self):
        """Watching a non-existent directory raises an OSError."""
        badpath = os.path.join(self.testdir, "nonexistent")
        self.watcher.watch(badpath, aionotify.Flags.CREATE)
        with self.assertRaises(OSError):
            await self.watcher.setup()

    async def test_unwatch_bad_alias(self):
        self.watcher.watch(self.testdir, aionotify.Flags.CREATE)
        await self.watcher.setup()
        with self.assertRaises(ValueError):
            self.watcher.unwatch("blah")


class SanityTests(AIONotifyTestCase):
    timeout = 0.1

    @unittest.expectedFailure
    async def test_timeout_works(self):
        """A test cannot run longer than the defined timeout."""
        # This test should fail, since we're setting a global timeout of 0.1 yet ask to wait for 0.3 seconds.
        await asyncio.sleep(0.2)

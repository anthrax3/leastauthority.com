# Copyright 2014-2016 ClusterHQ
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import

"""
Various utilities to help with unit and functional testing.

Originally ``flocker/testtools/__init__.py``.
"""

import gc
import io
import socket
import os
import pwd
from collections import namedtuple
from contextlib import contextmanager
from random import randrange
from functools import wraps
from unittest import skipIf

from zope.interface import implementer
from zope.interface.verify import verifyClass

from twisted.internet.interfaces import (
    IProcessTransport, IReactorProcess, IReactorCore,
)
from twisted.python.filepath import FilePath, Permissions
from twisted.internet.base import _ThreePhaseEvent
from twisted.internet.task import Clock
from twisted.internet.defer import Deferred
from twisted.internet.error import ConnectionDone
from twisted.trial.unittest import SkipTest
from twisted.internet.protocol import Factory, Protocol
from twisted.test.proto_helpers import MemoryReactor

from ._base import (
    TestCase,
)

__all__ = [
    'CustomException',
    'FakeProcessReactor',
    'FakeSysModule',
    'MemoryCoreReactor',
    'TestCase',
    'assertContainsAll',
    'assertNoFDsLeaked',
    'assert_equal_comparison',
    'assert_not_equal_comparison',
    'attempt_effective_uid',
    'find_free_port',
    'help_problems',
    'if_root',
    'make_with_init_tests',
    'not_root',
    'random_name',
    'skip_on_broken_permissions',
]



@implementer(IProcessTransport)
class FakeProcessTransport(object):
    """
    Mock process transport to observe signals sent to a process.

    @ivar signals: L{list} of signals sent to process.
    """

    def __init__(self):
        self.signals = []
        self.stdin_open = [True]

    def signalProcess(self, signal):
        self.signals.append(signal)

    def closeStdin(self):
        self.stdin_open.append(False)


class SpawnProcessArguments(namedtuple(
                            'ProcessData',
                            'processProtocol executable args env path '
                            'uid gid usePTY childFDs transport')):
    """
    Object recording the arguments passed to L{FakeProcessReactor.spawnProcess}
    as well as the L{IProcessTransport} that was connected to the protocol.

    @ivar transport: Fake transport connected to the protocol.
    @type transport: L{IProcessTransport}

    @see L{twisted.internet.interfaces.IReactorProcess.spawnProcess}
    """


@implementer(IReactorProcess)
class FakeProcessReactor(Clock):
    """
    Fake reactor implmenting process support.

    @ivar processes: List of process that have been spawned
    @type processes: L{list} of L{SpawnProcessArguments}.
    """

    def __init__(self):
        Clock.__init__(self)
        self.processes = []

    def timeout(self):
        if self.calls:
            return max(0, self.calls[0].getTime() - self.seconds())
        return 0

    def spawnProcess(self, processProtocol, executable, args=(), env={},
                     path=None, uid=None, gid=None, usePTY=0, childFDs=None):
        transport = FakeProcessTransport()
        self.processes.append(SpawnProcessArguments(
            processProtocol, executable, args, env, path, uid, gid, usePTY,
            childFDs, transport=transport))
        processProtocol.makeConnection(transport)
        return transport


verifyClass(IReactorProcess, FakeProcessReactor)


@contextmanager
def assertNoFDsLeaked(test_case):
    """Context manager that asserts no file descriptors are leaked.

    :param test_case: The ``TestCase`` running this unit test.
    :raise SkipTest: when /dev/fd virtual filesystem is not available.
    """
    # Make sure there's no file descriptors that will be cleared by GC
    # later on:
    gc.collect()

    def process_fds():
        path = FilePath(b"/dev/fd")
        if not path.exists():
            raise SkipTest("/dev/fd is not available.")

        return set([child.basename() for child in path.children()])

    fds = process_fds()
    try:
        yield
    finally:
        test_case.assertEqual(process_fds(), fds)


def assert_equal_comparison(case, a, b):
    """
    Assert that ``a`` equals ``b``.

    :param a: Any object to compare to ``b``.
    :param b: Any object to compare to ``a``.

    :raise: If ``a == b`` evaluates to ``False`` or if ``a != b`` evaluates to
        ``True``, ``case.failureException`` is raised.
    """
    equal = a == b
    unequal = a != b

    messages = []
    if not equal:
        messages.append("a == b evaluated to False")
    if unequal:
        messages.append("a != b evaluated to True")

    if messages:
        case.fail(
            "Expected a and b to be equal: " + "; ".join(messages))


def assert_not_equal_comparison(case, a, b):
    """
    Assert that ``a`` does not equal ``b``.

    :param a: Any object to compare to ``b``.
    :param b: Any object to compare to ``a``.

    :raise: If ``a == b`` evaluates to ``True`` or if ``a != b`` evaluates to
        ``False``, ``case.failureException`` is raised.
    """
    equal = a == b
    unequal = a != b

    messages = []
    if equal:
        messages.append("a == b evaluated to True")
    if not unequal:
        messages.append("a != b evaluated to False")

    if messages:
        case.fail(
            "Expected a and b to be not-equal: " + "; ".join(messages))


def random_name(case):
    """
    Return a short, random name.

    :param TestCase case: The test case being run.  The test method that is
        running will be mixed into the name.

    :return name: A random ``unicode`` name.
    """
    return u"{}-{}".format(case.id().replace(u".", u"_"), randrange(10 ** 6))


def help_problems(command_name, help_text):
    """Identify and return a list of help text problems.

    :param unicode command_name: The name of the command which should appear in
        the help text.
    :param bytes help_text: The full help text to be inspected.
    :return: A list of problems found with the supplied ``help_text``.
    :rtype: list
    """
    problems = []
    expected_start = u'Usage: {command}'.format(
        command=command_name).encode('utf8')
    if not help_text.startswith(expected_start):
        problems.append(
            'Does not begin with {expected}. Found {actual} instead'.format(
                expected=repr(expected_start),
                actual=repr(help_text[:len(expected_start)])
            )
        )
    return problems


class FakeSysModule(object):
    """A ``sys`` like substitute.

    For use in testing the handling of `argv`, `stdout` and `stderr` by command
    line scripts.

    :ivar list argv: See ``__init__``
    :ivar stdout: A :py:class:`io.BytesIO` object representing standard output.
    :ivar stderr: A :py:class:`io.BytesIO` object representing standard error.
    """
    def __init__(self, argv=None):
        """Initialise the fake sys module.

        :param list argv: The arguments list which should be exposed as
            ``sys.argv``.
        """
        if argv is None:
            argv = []
        self.argv = argv
        # io.BytesIO is not quite the same as sys.stdout/stderr
        # particularly with respect to unicode handling.  So,
        # hopefully the implementation doesn't try to write any
        # unicode.
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()


def make_with_init_tests(record_type, kwargs, expected_defaults=None):
    """
    Return a ``TestCase`` which tests that ``record_type.__init__`` accepts the
    supplied ``kwargs`` and exposes them as public attributes.

    :param record_type: The class with an ``__init__`` method to be tested.
    :param kwargs: The keyword arguments which will be supplied to the
        ``__init__`` method.
    :param dict expected_defaults: The default keys and default values of
        arguments which have defaults and which may be omitted when calling the
        initialiser.
    :returns: A ``TestCase``.
    """
    if expected_defaults is None:
        expected_defaults = {}

    unknown_defaults = set(expected_defaults.keys()) - set(kwargs.keys())
    if unknown_defaults:
        raise TypeError(
            'expected_defaults contained the following unrecognized keys: '
            '{}'.format(tuple(unknown_defaults)))

    required_kwargs = kwargs.copy()
    for k in expected_defaults.keys():
        required_kwargs.pop(k)

    class WithInitTests(TestCase):
        """
        Tests for classes decorated with ``with_init``.
        """
        def test_init(self):
            """
            The record type accepts keyword arguments which are exposed as
            public attributes.
            """
            record = record_type(**kwargs)
            self.assertEqual(
                kwargs.values(),
                [getattr(record, key) for key in kwargs.keys()]
            )

        def test_optional_arguments(self):
            """
            The record type initialiser has arguments which may be omitted.
            """
            try:
                record = record_type(**required_kwargs)
            except ValueError as e:
                self.fail(
                    'One of the following arguments was expected to be '
                    'optional but appears to be required: %r. '
                    'Error was: %r' % (
                        expected_defaults.keys(), e))

            self.assertEqual(
                required_kwargs.values(),
                [getattr(record, key) for key in required_kwargs.keys()]
            )

        def test_optional_defaults(self):
            """
            The optional arguments have the expected defaults.
            """
            try:
                record = record_type(**required_kwargs)
            except ValueError as e:
                self.fail(
                    'One of the following arguments was expected to be '
                    'optional but appears to be required: %r. '
                    'Error was: %r' % (
                        expected_defaults.keys(), e))
            self.assertEqual(
                expected_defaults.values(),
                [getattr(record, key) for key in expected_defaults.keys()]
            )

    return WithInitTests


def find_free_port(interface='127.0.0.1', socket_family=socket.AF_INET,
                   socket_type=socket.SOCK_STREAM):
    """
    Ask the platform to allocate a free port on the specified interface, then
    release the socket and return the address which was allocated.

    Copied from ``twisted.internet.test.connectionmixins.findFreePort``.

    :param bytes interface: The local address to try to bind the port on.
    :param int socket_family: The socket family of port.
    :param int socket_type: The socket type of the port.

    :return: A two-tuple of address and port, like that returned by
        ``socket.getsockname``.
    """
    address = socket.getaddrinfo(interface, 0)[0][4]
    probe = socket.socket(socket_family, socket_type)
    try:
        probe.bind(address)
        return probe.getsockname()
    finally:
        probe.close()


def make_capture_protocol():
    """
    Return a ``Deferred``, and a ``Protocol`` which will capture bytes and fire
    the ``Deferred`` when its connection is lost.

    :returns: A 2-tuple of ``Deferred`` and ``Protocol`` instance.
    :rtype: tuple
    """
    d = Deferred()
    captured_data = []

    class Recorder(Protocol):
        def dataReceived(self, data):
            captured_data.append(data)

        def connectionLost(self, reason):
            if reason.check(ConnectionDone):
                d.callback(b''.join(captured_data))
            else:
                d.errback(reason)
    return d, Recorder()


class ProtocolPoppingFactory(Factory):
    """
    A factory which only creates a fixed list of protocols.

    For example if in a test you want to ensure that a test server only handles
    a single connection, you'd supply a list of one ``Protocol``
    instance. Subsequent requests will result in an ``IndexError``.
    """
    def __init__(self, protocols):
        """
        :param list protocols: A list of ``Protocol`` instances which will be
            used for successive connections.
        """
        self.protocols = protocols

    def buildProtocol(self, addr):
        return self.protocols.pop()


def skip_on_broken_permissions(test_method):
    """
    Skips the wrapped test when the temporary directory is on a
    filesystem with broken permissions.

    Virtualbox's shared folder (as used for :file:`/vagrant`) doesn't entirely
    respect changing permissions. For example, this test detects running on a
    shared folder by the fact that all permissions can't be removed from a
    file.

    :param callable test_method: Test method to wrap.
    :return: The wrapped method.
    :raise SkipTest: when the temporary directory is on a filesystem with
        broken permissions.
    """
    @wraps(test_method)
    def wrapper(case, *args, **kwargs):
        test_file = FilePath(case.mktemp())
        test_file.touch()
        test_file.chmod(0o000)
        permissions = test_file.getPermissions()
        test_file.chmod(0o777)
        if permissions != Permissions(0o000):
            raise SkipTest(
                "Can't run test on filesystem with broken permissions.")
        return test_method(case, *args, **kwargs)
    return wrapper


@contextmanager
def attempt_effective_uid(username, suppress_errors=False):
    """
    A context manager to temporarily change the effective user id.

    :param bytes username: The username whose uid will take effect.
    :param bool suppress_errors: Set to `True` to suppress `OSError`
        ("Operation not permitted") when running as a non-root user.
    """
    original_euid = os.geteuid()
    new_euid = pwd.getpwnam(username).pw_uid
    restore_euid = False

    if original_euid != new_euid:
        try:
            os.seteuid(new_euid)
        except OSError as e:
            # Only handle "Operation not permitted" errors.
            if not suppress_errors or e.errno != 1:
                raise
        else:
            restore_euid = True
    try:
        yield
    finally:
        if restore_euid:
            os.seteuid(original_euid)


def assertContainsAll(haystack, needles, test_case):
    """
    Assert that all the terms in the needles list are found in the haystack.

    :param test_case: The ``TestCase`` instance on which to call assertions.
    :param list needles: A list of terms to search for in the ``haystack``.
    :param haystack: An iterable in which to search for the terms in needles.
    """
    for needle in reversed(needles):
        if needle in haystack:
            needles.remove(needle)

    if needles:
        test_case.fail(
            '{haystack} did not contain {needles}'.format(
                haystack=haystack, needles=needles
            )
        )


# Skip decorators for tests:
if_root = skipIf(os.getuid() != 0, "Must run as root.")
not_root = skipIf(os.getuid() == 0, "Must not run as root.")


# TODO: This should be provided by Twisted (also it should be more complete
# instead of 1/3rd done).


@implementer(IReactorCore)
class MemoryCoreReactor(MemoryReactor, Clock):
    """
    Fake reactor with listenTCP, IReactorTime and just enough of an
    implementation of IReactorCore.
    """
    def __init__(self):
        MemoryReactor.__init__(self)
        Clock.__init__(self)
        self._triggers = {}

    def addSystemEventTrigger(self, phase, eventType, f, *args, **kw):
        event = self._triggers.setdefault(eventType, _ThreePhaseEvent())
        return eventType, event.addTrigger(phase, f, *args, **kw)

    def removeSystemEventTrigger(self, triggerID):
        eventType, handle = triggerID
        event = self._triggers.setdefault(eventType, _ThreePhaseEvent())
        event.removeTrigger(handle)

    def fireSystemEvent(self, eventType):
        event = self._triggers.get(eventType)
        if event is not None:
            event.fireEvent()


class CustomException(Exception):
    """
    An exception that will never be raised by real code, useful for
    testing.
    """

from os import path
from typing import Any, Callable, Iterable, Iterator, List, Optional

import grpc
import pytest
from django.conf import settings
from django.core import mail
from django.db.models.query import QuerySet
from django.test.testcases import TestCase
from google.protobuf import empty_pb2
from google.protobuf.message import Message
from grpc import Compression, StatusCode
from grpc_interceptor.exceptions import GrpcException, InvalidArgument

from protos import image_pb2, pagination_pb2

from .emails import Email


def get_asset(name: str) -> str:
    return path.join(path.dirname(__file__), "..", "assets", name)


class PytestTestRunner:
    def __init__(self, verbosity=1, failfast=False, keepdb=False, **kwargs):
        self.verbosity = verbosity
        self.failfast = failfast
        self.keepdb = keepdb

    def run_tests(self, test_labels: Iterable[str], **kwargs) -> int:
        argv = []

        if self.verbosity == 0:
            argv.append("--quiet")
        elif self.verbosity == 2:
            argv.append("--verbose")
        elif self.verbosity == 3:
            argv.append("-vv")

        if self.failfast:
            argv.append("--exitfirst")

        if self.keepdb:
            argv.append("--reuse-db")

        argv.extend(test_labels)
        return pytest.main(argv)


class BaseTestCase(TestCase):
    def setUp(self):
        self.grpc_context = FakeContext()
        self.request = empty_pb2.Empty()

    def tearDown(self):
        pass

    def assertEmails(self, emails: List[Email]):
        self.assertEqual(len(mail.outbox), len(emails))

        for i in range(0, len(emails)):
            self.assertEqual(emails[i].subject, mail.outbox[i].subject)


class PaginationTestCase(BaseTestCase):
    date_field = "date_created"

    def setUp(self):
        super().setUp()
        self.page_size = 12

    def assertCursorEmpty(self, cursor: pagination_pb2.Cursor):
        for pair in cursor.data:
            self.assertEqual(pair.value, "")

    def assertCursorNotEmpty(self, cursor: pagination_pb2.Cursor):
        for pair in cursor.data:
            self.assertNotEqual(pair.value, "")

    def paginate(self, request_iterator: Iterator[pagination_pb2.Page]) -> Iterator:
        raise NotImplementedError

    def items_list(self, items) -> list:
        return getattr(items, items.__class__.__name__.lower())

    def run_test(self, check: Callable[[Message, int], None]):
        page_requests = [pagination_pb2.Page(forward=True, size=self.page_size)]
        items_iterator = self.paginate(page_requests)
        items = next(items_iterator)
        self.assertCursorEmpty(items.previous)
        self.assertCursorNotEmpty(items.next)
        self.assertEqual(len(self.items_list(items)), self.page_size)

        for i, item in enumerate(self.items_list(items)):
            check(item, i)

        page_requests.append(pagination_pb2.Page(forward=True, cursor=items.next))
        items = next(items_iterator)
        self.assertCursorNotEmpty(items.previous)
        self.assertCursorEmpty(items.next)
        self.assertEqual(len(self.items_list(items)), self.page_size)

        for i, item in enumerate(self.items_list(items)):
            check(item, i + self.page_size)

    def run_test_previous(self, check: Callable[[Any, int], None]):
        page_requests = [pagination_pb2.Page(forward=True, size=self.page_size)]
        items_iterator = self.paginate(page_requests)
        items = next(items_iterator)
        self.assertCursorEmpty(items.previous)
        self.assertCursorNotEmpty(items.next)
        self.assertEqual(len(self.items_list(items)), self.page_size)
        page_requests.append(pagination_pb2.Page(forward=True, cursor=items.next))
        items = next(items_iterator)
        self.assertCursorNotEmpty(items.previous)
        page_requests.append(pagination_pb2.Page(forward=True, cursor=items.previous))
        items = next(items_iterator)
        self.assertCursorEmpty(items.previous)
        self.assertCursorNotEmpty(items.next)

        for i, item in enumerate(self.items_list(items)):
            check(item, i)

    def run_test_reverse(self, check: Callable[[Any, int], None]):
        page_requests = [pagination_pb2.Page(forward=False, size=self.page_size)]
        items_iterator = self.paginate(page_requests)
        items = next(items_iterator)
        self.assertCursorEmpty(items.previous)
        self.assertCursorNotEmpty(items.next)
        self.assertEqual(len(self.items_list(items)), self.page_size)

        for i, item in enumerate(self.items_list(items)):
            check(item, -i - 1)

        page_requests.append(pagination_pb2.Page(forward=False, cursor=items.next))
        items = next(items_iterator)
        self.assertCursorNotEmpty(items.previous)
        self.assertCursorEmpty(items.next)
        self.assertEqual(len(self.items_list(items)), self.page_size)

        for i, item in enumerate(self.items_list(items)):
            check(item, -i - 1 - self.page_size)

    def run_test_reverse_previous(self, check: Callable[[Any, int], None]):
        page_requests = [pagination_pb2.Page(forward=False, size=self.page_size)]
        items_iterator = self.paginate(page_requests)
        items = next(items_iterator)
        self.assertCursorEmpty(items.previous)
        self.assertCursorNotEmpty(items.next)
        self.assertEqual(len(self.items_list(items)), self.page_size)
        page_requests.append(pagination_pb2.Page(forward=False, cursor=items.next))
        items = next(items_iterator)
        self.assertCursorNotEmpty(items.previous)
        page_requests.append(pagination_pb2.Page(forward=False, cursor=items.previous))
        items = next(items_iterator)
        self.assertCursorEmpty(items.previous)
        self.assertCursorNotEmpty(items.next)

        for i, item in enumerate(self.items_list(items)):
            check(item, -i - 1)

    def run_test_empty(self, query: QuerySet):
        query.delete()
        items_iterator = self.paginate([])
        l = list(items_iterator)
        self.assertEqual(l, [])

    def run_test_invalid_size(self):
        for size in [0, settings.PAGINATION_MAX_SIZE + 1]:
            page_requests = [pagination_pb2.Page(forward=True, size=size)]
            items_iterator = self.paginate(page_requests)

            with self.assertRaises(InvalidArgument):
                next(items_iterator)

    def run_test_no_initial_size(self, cursor: pagination_pb2.Cursor):
        page_requests = [pagination_pb2.Page(forward=True, cursor=cursor)]
        items_iterator = self.paginate(page_requests)

        with self.assertRaises(InvalidArgument):
            next(items_iterator)

    def run_test_out_of_bounds(self, cursor: pagination_pb2.Cursor):
        page_requests = [
            pagination_pb2.Page(forward=True, size=12),
            pagination_pb2.Page(forward=True, cursor=cursor),
        ]
        items_iterator = self.paginate(page_requests)
        items = next(items_iterator)
        self.assertEqual(len(self.items_list(items)), self.page_size)
        items = next(items_iterator)
        self.assertEqual(len(self.items_list(items)), 0)
        self.assertCursorEmpty(items.previous)
        self.assertCursorEmpty(items.next)


class ImageTestCaseMixin:
    def make_request(self, extension: str) -> Iterator[image_pb2.ImageChunk]:
        with open(get_asset(f"image.{extension}"), "rb") as image:
            while data := image.read(64):
                yield image_pb2.ImageChunk(data=data)


class FakeContext(grpc.ServicerContext):
    def __init__(self):
        self._initial_metadata_allowed = True
        self._code = grpc.StatusCode.UNKNOWN
        self._details = ""
        self._invocation_metadata = {}

    def is_active(self) -> bool:
        return True

    def time_remaining(self) -> int:
        return 10000

    def cancel(self):
        pass

    def add_callback(self, callback):
        pass

    def invocation_metadata(self) -> dict:
        return self._invocation_metadata

    def peer(self) -> str:
        return "fake"

    def peer_identities(self) -> Optional[List[bytes]]:
        return None

    def peer_identity_key(self) -> Optional[str]:
        return None

    def auth_context(self) -> dict:
        return {}

    def set_compression(self, compression: Compression):
        pass

    def send_initial_metadata(self, initial_metadata: dict):
        if self._initial_metadata_allowed:
            self._initial_metadata_allowed = False
        else:
            raise ValueError("Initial metadata no longer allowed!")

    def set_trailing_metadata(self, trailing_metadata: dict):
        self._trailing_metadata = trailing_metadata

    def abort(self, code: StatusCode, details: str):
        raise GrpcException(status_code=code, details=details)

    def abort_with_status(self, status: StatusCode):
        self.abort(status, self._details)

    def set_code(self, code: StatusCode):
        self._code = code

    def set_details(self, details: str):
        self._details = details

    def disable_next_message_compression(self):
        pass

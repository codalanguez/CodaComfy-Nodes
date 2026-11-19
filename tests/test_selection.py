import concurrent.futures
import threading
import unittest

from support import selection


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.registry = selection.SelectionRegistry()
        self.request = self.registry.create("7", "client-a", [{} for _ in range(65)])

    def test_arbitrary_batch_and_last_selection(self):
        self.registry.submit(self.request.token, "client-a", 64)
        self.assertEqual(self.registry.wait(self.request, lambda: None), 64)
        self.assertEqual(self.registry.pending("client-a"), [])

    def test_pending_scoped_and_recoverable(self):
        self.assertEqual(self.registry.pending("client-b"), [])
        self.assertEqual(self.registry.pending("client-a")[0]["token"], self.request.token)

    def test_invalid_indices_do_not_release_worker(self):
        for index in (-1, 65, True, 1.5, "2", None):
            with self.subTest(index=index), self.assertRaises(selection.SelectionError):
                self.registry.submit(self.request.token, "client-a", index)
        self.assertFalse(self.request.event.is_set())

    def test_foreign_client_cannot_submit(self):
        with self.assertRaises(selection.SelectionError) as error:
            self.registry.submit(self.request.token, "client-b", 0)
        self.assertEqual(error.exception.status, 404)
        self.assertFalse(self.request.event.is_set())

    def test_duplicate_and_stale_submission(self):
        self.registry.submit(self.request.token, "client-a", 0)
        with self.assertRaises(selection.SelectionError) as error:
            self.registry.submit(self.request.token, "client-a", 1)
        self.assertEqual(error.exception.status, 409)
        self.registry.discard(self.request)
        with self.assertRaises(selection.SelectionError) as error:
            self.registry.submit(self.request.token, "client-a", 1)
        self.assertEqual(error.exception.status, 404)

    def test_cancel_and_timeout_never_choose(self):
        self.registry.submit(self.request.token, "client-a", cancel=True)
        with self.assertRaises(selection.SelectionCancelled):
            self.registry.wait(self.request, lambda: None)
        request = self.registry.create("7", "client-a", [{}])
        with self.assertRaises(selection.SelectionCancelled):
            self.registry.wait(request, lambda: None, timeout=.01)
        self.assertIsNone(request.index)

    def test_interrupt_releases_wait(self):
        def interrupted():
            raise RuntimeError("Stop")
        with self.assertRaisesRegex(RuntimeError, "Stop"):
            self.registry.wait(self.request, interrupted)

    def test_only_one_concurrent_submit_wins(self):
        barrier = threading.Barrier(2)
        def submit(index):
            barrier.wait()
            try:
                self.registry.submit(self.request.token, "client-a", index)
                return True
            except selection.SelectionError:
                return False
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(submit, [0, 1]))
        self.assertEqual(sum(results), 1)

    def test_new_execution_gets_unique_token(self):
        another = self.registry.create("7", "client-a", [{}])
        self.assertNotEqual(another.token, self.request.token)


if __name__ == "__main__":
    unittest.main()

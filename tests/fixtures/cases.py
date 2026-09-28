"""Bridge fixture cleanup to unittest without putting scenarios in fixtures."""

import unittest

from tests.fixtures.repos import RepoFixture


class RepoTestCase(unittest.TestCase, RepoFixture):
    def setUp(self):
        self.enterContext(self.prepared())

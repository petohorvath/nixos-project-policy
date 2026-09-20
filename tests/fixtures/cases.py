"""Bridge fixture cleanup to unittest without putting scenarios in fixtures."""

import unittest

from tests.fixtures.projects import ProjectFixture


class ProjectTestCase(unittest.TestCase, ProjectFixture):
    def setUp(self):
        self.enterContext(self.prepared())

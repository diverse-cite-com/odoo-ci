#!/usr/bin/env python3
"""
Convert Odoo test log output to JUnit XML format for GitLab CI test reports.

Parses Odoo log lines to extract test results and generates JUnit XML that
GitLab can display in the Tests tab of pipeline results.

Usage:
    python odoo_log_to_junit.py test-output.log -o test-results.xml
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class TestCase:
    """Represents a single test case result."""

    name: str
    classname: str
    module: str
    time: float = 0.0
    status: str = "passed"  # passed, failed, error, skipped
    message: Optional[str] = None
    output: Optional[str] = None
    is_subtest: bool = False  # True for HOOT JS tests (sub-assertions)


@dataclass
class TestSuite:
    """Represents a test suite (module) containing test cases."""

    name: str
    tests: list[TestCase] = field(default_factory=list)
    time: float = 0.0

    @property
    def failures(self) -> int:
        return sum(1 for t in self.tests if t.status == "failed")

    @property
    def errors(self) -> int:
        return sum(1 for t in self.tests if t.status == "error")

    @property
    def skipped(self) -> int:
        return sum(1 for t in self.tests if t.status == "skipped")


class OdooLogParser:
    """Parse Odoo test log output and extract test results."""

    # Regex patterns for parsing Odoo test logs
    # Pattern: 2026-01-28 13:29:16,814 44 INFO odoo odoo.addons.base.tests.test_intervals: Starting TestUtils.test_intervals_inversion ...
    STARTING_PATTERN = re.compile(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*?"
        r"INFO.*?odoo\.addons\.(\w+)\.tests\.(\w+).*?:\s*Starting\s+(\S+)\s*\.\.\."
    )

    # Pattern for HOOT JS tests: [HOOT] Test "@web/views/fields/..." passed (assertions: 3 / time: 85 ms)
    HOOT_PASSED_PATTERN = re.compile(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*?"
        r'\[HOOT\] Test "([^"]+)" passed \(assertions: \d+ / time: (\d+) ms\)'
    )

    # Pattern for HOOT failures: [HOOT] Test "@web/views/fields/..." failed:
    HOOT_FAILED_PATTERN = re.compile(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*?"
        r'ERROR.*?\[HOOT\] Test "([^"]+)" failed:'
    )

    # Pattern: ERROR odoo odoo.addons.web.tests.test_click_everywhere: FAIL: TestMenusDemoLight.test_01_click_apps_menus_as_demo
    FAIL_PATTERN = re.compile(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*?"
        r"ERROR.*?odoo\.addons\.(\w+)\.tests\.(\w+).*?:\s*FAIL:\s*(\S+)"
    )

    # Pattern: ERROR odoo odoo.addons.module.tests.test_file: ERROR: TestClass.test_method
    ERROR_PATTERN = re.compile(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*?"
        r"ERROR.*?odoo\.addons\.(\w+)\.tests\.(\w+).*?:\s*ERROR:\s*(\S+)"
    )

    # Pattern for test stats: odoo.tests.stats: web: 179 tests 1478.64s 8965 queries
    STATS_PATTERN = re.compile(
        r"odoo\.tests\.stats:\s*(\w+):\s*(\d+)\s*tests\s*([\d.]+)s"
    )

    # Pattern for final summary: 3 failed, 0 error(s) of 1465 tests
    SUMMARY_PATTERN = re.compile(
        r"(\d+)\s*failed,\s*(\d+)\s*error\(s\)\s*of\s*(\d+)\s*tests"
    )

    def __init__(self):
        self.suites: dict[str, TestSuite] = {}
        self.current_test: Optional[TestCase] = None
        self.current_test_start: Optional[datetime] = None
        self.failure_messages: dict[str, list[str]] = {}

    def parse_timestamp(self, ts_str: str) -> datetime:
        """Parse Odoo log timestamp."""
        return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S,%f")

    def get_or_create_suite(self, module: str) -> TestSuite:
        """Get or create a test suite for a module."""
        if module not in self.suites:
            self.suites[module] = TestSuite(name=module)
        return self.suites[module]

    def parse_line(self, line: str):
        """Parse a single log line."""
        # Check for test start
        match = self.STARTING_PATTERN.search(line)
        if match:
            ts_str, module, test_file, test_name = match.groups()

            # Finish previous test if any
            self._finish_current_test(ts_str)

            # Start new test
            self.current_test = TestCase(
                name=test_name,
                classname=f"{module}.tests.{test_file}",
                module=module,
            )
            self.current_test_start = self.parse_timestamp(ts_str)
            return

        # Check for HOOT passed tests (JS tests)
        match = self.HOOT_PASSED_PATTERN.search(line)
        if match:
            ts_str, test_name, time_ms = match.groups()
            # Extract module from test name like "@web/views/fields/..."
            module = test_name.split("/")[0].lstrip("@") if "/" in test_name else "web"

            test = TestCase(
                name=test_name,
                classname=f"{module}.hoot",
                module=module,
                time=int(time_ms) / 1000.0,
                status="passed",
                is_subtest=True,
            )
            suite = self.get_or_create_suite(module)
            suite.tests.append(test)
            return

        # Check for HOOT failed tests
        match = self.HOOT_FAILED_PATTERN.search(line)
        if match:
            ts_str, test_name = match.groups()
            module = test_name.split("/")[0].lstrip("@") if "/" in test_name else "web"

            test = TestCase(
                name=test_name,
                classname=f"{module}.hoot",
                module=module,
                status="failed",
                message="HOOT test failed",
                is_subtest=True,
            )
            suite = self.get_or_create_suite(module)
            suite.tests.append(test)
            return

        # Check for FAIL
        match = self.FAIL_PATTERN.search(line)
        if match:
            ts_str, module, test_file, test_name = match.groups()
            self._finish_current_test(ts_str, status="failed")
            return

        # Check for ERROR
        match = self.ERROR_PATTERN.search(line)
        if match:
            ts_str, module, test_file, test_name = match.groups()
            self._finish_current_test(ts_str, status="error")
            return

        # Check for stats (module-level timing)
        match = self.STATS_PATTERN.search(line)
        if match:
            module, test_count, time_s = match.groups()
            if module in self.suites:
                self.suites[module].time = float(time_s)
            return

    def _finish_current_test(self, ts_str: str, status: str = "passed"):
        """Finish the current test and add it to its suite."""
        if self.current_test and self.current_test_start:
            end_time = self.parse_timestamp(ts_str)
            self.current_test.time = (
                end_time - self.current_test_start
            ).total_seconds()
            self.current_test.status = status

            suite = self.get_or_create_suite(self.current_test.module)
            suite.tests.append(self.current_test)

        self.current_test = None
        self.current_test_start = None

    def parse_file(self, filepath: Path) -> dict[str, TestSuite]:
        """Parse an entire log file."""
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                self.parse_line(line)

        # Finish any remaining test
        if self.current_test:
            self.current_test.status = "passed"
            suite = self.get_or_create_suite(self.current_test.module)
            suite.tests.append(self.current_test)

        return self.suites


def generate_junit_xml(suites: dict[str, TestSuite]) -> ET.Element:
    """Generate JUnit XML from parsed test suites."""
    testsuites = ET.Element("testsuites")

    total_tests = sum(len(s.tests) for s in suites.values())
    total_failures = sum(s.failures for s in suites.values())
    total_errors = sum(s.errors for s in suites.values())
    total_time = sum(s.time for s in suites.values())

    testsuites.set("tests", str(total_tests))
    testsuites.set("failures", str(total_failures))
    testsuites.set("errors", str(total_errors))
    testsuites.set("time", f"{total_time:.3f}")

    for suite_name, suite in sorted(suites.items()):
        testsuite = ET.SubElement(testsuites, "testsuite")
        testsuite.set("name", suite_name)
        testsuite.set("tests", str(len(suite.tests)))
        testsuite.set("failures", str(suite.failures))
        testsuite.set("errors", str(suite.errors))
        testsuite.set("skipped", str(suite.skipped))
        testsuite.set("time", f"{suite.time:.3f}")

        for test in suite.tests:
            testcase = ET.SubElement(testsuite, "testcase")
            testcase.set("name", test.name)
            testcase.set("classname", test.classname)
            testcase.set("time", f"{test.time:.3f}")

            if test.status == "failed":
                failure = ET.SubElement(testcase, "failure")
                failure.set("message", test.message or "Test failed")
                if test.output:
                    failure.text = test.output
            elif test.status == "error":
                error = ET.SubElement(testcase, "error")
                error.set("message", test.message or "Test error")
                if test.output:
                    error.text = test.output
            elif test.status == "skipped":
                skipped = ET.SubElement(testcase, "skipped")
                if test.message:
                    skipped.set("message", test.message)

    return testsuites


def main():
    parser = argparse.ArgumentParser(
        description="Convert Odoo test log to JUnit XML format"
    )
    parser.add_argument("logfile", type=Path, help="Path to Odoo test log file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("test-results.xml"),
        help="Output JUnit XML file (default: test-results.xml)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Print summary to stdout"
    )

    args = parser.parse_args()

    if not args.logfile.exists():
        print(f"Error: Log file not found: {args.logfile}", file=sys.stderr)
        sys.exit(1)

    # Parse the log file
    log_parser = OdooLogParser()
    suites = log_parser.parse_file(args.logfile)

    # Generate JUnit XML
    xml_root = generate_junit_xml(suites)

    # Write output
    tree = ET.ElementTree(xml_root)
    ET.indent(tree, space="  ")
    tree.write(args.output, encoding="utf-8", xml_declaration=True)

    if args.verbose:
        # Count Python test methods (what Odoo reports)
        python_tests = sum(
            1 for s in suites.values() for t in s.tests if not t.is_subtest
        )
        python_failures = sum(
            1
            for s in suites.values()
            for t in s.tests
            if not t.is_subtest and t.status == "failed"
        )
        python_errors = sum(
            1
            for s in suites.values()
            for t in s.tests
            if not t.is_subtest and t.status == "error"
        )

        # Count all tests including HOOT subtests (for JUnit XML)
        total_tests = sum(len(s.tests) for s in suites.values())
        total_failures = sum(s.failures for s in suites.values())
        total_errors = sum(s.errors for s in suites.values())
        total_time = sum(s.time for s in suites.values())

        # Count HOOT subtests separately
        hoot_tests = total_tests - python_tests
        hoot_failures = total_failures - python_failures

        print(f"\n=== Test Summary ===")
        print(
            f"Python tests: {python_tests} tests, {python_failures} failures, {python_errors} errors"
        )
        print(f"HOOT JS tests: {hoot_tests} tests, {hoot_failures} failures")
        print(
            f"Total (JUnit): {total_tests} tests, {total_failures} failures, {total_errors} errors"
        )
        print(f"Time: {total_time:.2f}s")
        print(f"\nPer module:")
        for name, suite in sorted(suites.items()):
            status = "✓" if suite.failures == 0 and suite.errors == 0 else "✗"
            py_count = sum(1 for t in suite.tests if not t.is_subtest)
            hoot_count = sum(1 for t in suite.tests if t.is_subtest)
            count_str = f"{py_count} python"
            if hoot_count > 0:
                count_str += f", {hoot_count} hoot"
            print(
                f"  {status} {name}: {count_str}, {suite.failures} failures ({suite.time:.2f}s)"
            )
        print(f"\nJUnit XML written to: {args.output}")


if __name__ == "__main__":
    main()

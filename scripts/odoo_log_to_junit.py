#!/usr/bin/env python3
"""
Convert Odoo test log output to JUnit XML format for GitLab CI test reports.

Parses Odoo log lines to extract test results and generates JUnit XML that
GitLab can display in the Tests tab of pipeline results.

Usage:
    python odoo_log_to_junit.py test-output.log -o test-results.xml
"""

import argparse
import os
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
    # NOTE: Odoo's OdooTestResult.__str__ omits the skip count entirely, and
    # wasSuccessful() is `failures == errors == 0` — so skips never influence
    # the exit status. They have to be counted from the log lines below.
    SUMMARY_PATTERN = re.compile(
        r"(\d+)\s*failed,\s*(\d+)\s*error\(s\)\s*of\s*(\d+)\s*tests"
    )

    # Pattern: INFO odoo odoo.addons.web.tests.test_tour: skipped TestX.test_y : <reason>
    # Emitted by OdooTestResult.addSkip at INFO level (odoo/tests/result.py).
    SKIP_PATTERN = re.compile(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*?"
        r"INFO.*?odoo\.addons\.(\w+)\.tests\.(\w+).*?:\s*skipped\s+(\S+)\s*:\s*(.*)$"
    )

    # Class-level skips come through the odoo.tests.suite logger with the class
    # path in parentheses and NO preceding "Starting ..." line:
    #   skipped setUpClass (odoo.addons.base.tests.test_x.TestClass) : <reason>
    # A skipped setUpClass takes every test in the class with it, so these
    # represent the largest single blocks of lost coverage.
    # The dotted path between "tests." and the class name is NOT always a
    # single component: test packages nest, e.g.
    #   odoo.addons.sap_b1_to_odoo.tests.pipelines.test_foo.TestBar
    # Capture the whole tail and split off the class in code, so nesting of any
    # depth is handled. Getting this wrong silently drops the skip, and a
    # skipped setUpClass takes its entire class with it.
    SKIP_CLASS_PATTERN = re.compile(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*?"
        r"INFO.*?odoo\.tests\.suite.*?:\s*skipped\s+(\S+)\s+"
        r"\(odoo\.addons\.(\w+)\.tests\.([\w.]+)\)\s*:\s*(.*)$"
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

        # Check for a class-level SKIP (setUpClass/tearDownClass). Must run
        # before the per-test SKIP pattern, whose \S+ would otherwise swallow
        # the method name and mis-parse the reason.
        match = self.SKIP_CLASS_PATTERN.search(line)
        if match:
            ts_str, method, module, tail, reason = match.groups()
            # tail is "<maybe.nested.pkgs.><file>.<Class>"; the class is last.
            parts = tail.split(".")
            class_name = parts[-1] if len(parts) > 1 else tail
            test_file = ".".join(parts[:-1]) or tail
            # Standalone: there is no in-flight test to finish, and any test
            # that IS in flight belongs to a different class — leave it alone.
            test = TestCase(
                name=f"{class_name}.{method}",
                classname=f"{module}.tests.{test_file}",
                module=module,
                status="skipped",
                message=reason.strip(),
            )
            self.get_or_create_suite(module).tests.append(test)
            return

        # Check for SKIP. Must run before FAIL/ERROR: a skipped test has
        # already been "Started", and without this it falls through to
        # _finish_current_test's default status of "passed".
        match = self.SKIP_PATTERN.search(line)
        if match:
            ts_str, module, test_file, test_name, reason = match.groups()
            if self.current_test and self.current_test.name == test_name:
                self._finish_current_test(
                    ts_str, status="skipped", message=reason.strip()
                )
            else:
                # No matching "Starting ..." line in flight. Normally startTest
                # always precedes addSkip, but a truncated log (output limits)
                # can drop the start line — and losing the skip along with it
                # would silently under-report exactly what we gate on. Record it
                # standalone instead, and leave any unrelated in-flight test be.
                test = TestCase(
                    name=test_name,
                    classname=f"{module}.tests.{test_file}",
                    module=module,
                    status="skipped",
                    message=reason.strip(),
                )
                self.get_or_create_suite(module).tests.append(test)
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

    def _finish_current_test(
        self,
        ts_str: str,
        status: str = "passed",
        message: Optional[str] = None,
    ):
        """Finish the current test and add it to its suite."""
        if self.current_test and self.current_test_start:
            end_time = self.parse_timestamp(ts_str)
            self.current_test.time = (
                end_time - self.current_test_start
            ).total_seconds()
            self.current_test.status = status
            if message:
                self.current_test.message = message

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


# Skip reasons raised on the headless-browser path in odoo/tests/common.py
# (19.0): "Chrome executable not found", "Failed to detect chrome devtools port
# after ...", "Error during Chrome headless connection", "Error during Chrome
# connection: never found 'page' target", "Cannot connect to chrome dev tools",
# "websocket-client module is not installed".
#
# These all mean the SAME thing operationally: the tour did not run. Odoo raises
# unittest.SkipTest for every one of them, which keeps the job green — so an
# entire browser-test suite can vanish without any signal.
# Deliberately NOT a loose match on the word "browser": a developer's own
# @unittest.skip("only meaningful in a browser") would then fail the build for
# no reason. These terms track the actual reason strings above.
BROWSER_SKIP_PATTERN = re.compile(
    r"chrome|chromium|devtools|dev\s+tools|headless|websocket-client",
    re.IGNORECASE,
)


def browser_skips(
    suites: dict[str, TestSuite],
    allow: "list[re.Pattern] | None" = None,
) -> list[TestCase]:
    """Return skipped tests whose reason indicates the browser never started.

    ``allow`` excuses reasons that match — for the case where a browser-ish
    skip is genuinely expected in this environment and has been signed off.
    """
    allow = allow or []
    return [
        test
        for suite in suites.values()
        for test in suite.tests
        if test.status == "skipped"
        and test.message
        and BROWSER_SKIP_PATTERN.search(test.message)
        and not any(a.search(test.message) for a in allow)
    ]


def all_skips(suites: dict[str, TestSuite]) -> list[TestCase]:
    """Return every skipped test."""
    return [
        test
        for suite in suites.values()
        for test in suite.tests
        if test.status == "skipped"
    ]


def generate_junit_xml(suites: dict[str, TestSuite]) -> ET.Element:
    """Generate JUnit XML from parsed test suites."""
    testsuites = ET.Element("testsuites")

    total_tests = sum(len(s.tests) for s in suites.values())
    total_failures = sum(s.failures for s in suites.values())
    total_errors = sum(s.errors for s in suites.values())
    total_skipped = sum(s.skipped for s in suites.values())
    total_time = sum(s.time for s in suites.values())

    testsuites.set("tests", str(total_tests))
    testsuites.set("failures", str(total_failures))
    testsuites.set("errors", str(total_errors))
    testsuites.set("skipped", str(total_skipped))
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
    parser.add_argument(
        "logfile",
        type=Path,
        nargs="?",
        default=None,
        help="Path to Odoo test log file (use - or omit for stdin)",
    )
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
    parser.add_argument(
        "--fail-on-browser-skip",
        action="store_true",
        help=(
            "Exit non-zero if any test was skipped because the headless browser "
            "did not start. Odoo turns those into unittest.SkipTest, which keeps "
            "the job green while no tour actually ran."
        ),
    )
    parser.add_argument(
        "--fail-on-skip",
        action="store_true",
        help="Exit non-zero if ANY test was skipped, whatever the reason.",
    )
    parser.add_argument(
        "--allow-skip-reason",
        action="append",
        default=[],
        metavar="REGEX",
        help=(
            "Skip reasons matching this regex never fail the build. Repeatable. "
            "For signing off a browser-ish skip that is genuinely expected here "
            "— prefer fixing the cause; an allowlist entry hides a real gap."
        ),
    )

    args = parser.parse_args()

    # Parse the log file or stdin
    log_parser = OdooLogParser()

    if args.logfile is None or str(args.logfile) == "-":
        # Read from stdin (supports piping)
        for line in sys.stdin:
            log_parser.parse_line(line)
        # Finish any remaining test
        if log_parser.current_test:
            log_parser.current_test.status = "passed"
            suite = log_parser.get_or_create_suite(log_parser.current_test.module)
            suite.tests.append(log_parser.current_test)
        suites = log_parser.suites
    else:
        if not args.logfile.exists():
            print(f"Error: Log file not found: {args.logfile}", file=sys.stderr)
            sys.exit(1)
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
        total_skipped = sum(s.skipped for s in suites.values())
        browser_skipped = len(browser_skips(suites))

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
        print(f"Skipped: {total_skipped} ({browser_skipped} browser/tour)")
        print(f"Time: {total_time:.2f}s")
        print(f"\nPer module:")
        for name, suite in sorted(suites.items()):
            if suite.failures or suite.errors:
                status = "✗"
            elif suite.skipped:
                # Not a pass. A skipped tour is a test that did not run.
                status = "⚠"
            else:
                status = "✓"
            py_count = sum(1 for t in suite.tests if not t.is_subtest)
            hoot_count = sum(1 for t in suite.tests if t.is_subtest)
            count_str = f"{py_count} python"
            if hoot_count > 0:
                count_str += f", {hoot_count} hoot"
            skip_str = f", {suite.skipped} skipped" if suite.skipped else ""
            print(
                f"  {status} {name}: {count_str}, {suite.failures} failures"
                f"{skip_str} ({suite.time:.2f}s)"
            )
        print(f"\nJUnit XML written to: {args.output}")

    # Skip gates. Deliberately AFTER the XML is written, so the report is still
    # published when the gate trips.
    allow = [re.compile(r, re.IGNORECASE) for r in args.allow_skip_reason]

    # Surface every skip that is NOT going to fail the build. These are usually
    # legitimate under CI — no demo data, an optional dependency absent — but a
    # skip nobody ever sees is how coverage rots quietly.
    failing = set()
    if args.fail_on_skip:
        offenders = [t for t in all_skips(suites)
                     if not any(a.search(t.message or "") for a in allow)]
        label = "skipped test(s)"
    elif args.fail_on_browser_skip:
        offenders = browser_skips(suites, allow)
        label = "browser test(s) skipped because the browser never started"
    else:
        offenders = []
        label = ""
    failing = {id(t) for t in offenders}

    tolerated = [t for t in all_skips(suites) if id(t) not in failing]
    if tolerated:
        in_actions = bool(os.environ.get("GITHUB_ACTIONS"))
        print(f"\nSkipped but not failing ({len(tolerated)}):", file=sys.stderr)
        for test in tolerated:
            line = f"  - {test.classname}.{test.name}: {test.message}"
            print(line, file=sys.stderr)
            if in_actions:
                print(f"::warning title=Test skipped::"
                      f"{test.classname}.{test.name}: {test.message}")

    if offenders:
        print(
            f"\nERROR: {len(offenders)} {label}. "
            f"A skip is not a pass — failing the build.",
            file=sys.stderr,
        )
        for test in offenders:
            print(
                f"  - {test.classname}.{test.name}: {test.message}",
                file=sys.stderr,
            )
        sys.exit(2)


if __name__ == "__main__":
    main()

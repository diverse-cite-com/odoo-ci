#!/usr/bin/env python3
"""
Unit tests for odoo_log_to_junit.py

Focus: a test that Odoo SKIPS must never be reported as passing. Odoo's
OdooTestResult.addSkip logs at INFO level and wasSuccessful() ignores skips
entirely, so a broken headless browser turns every tour into a silent skip
while the pipeline stays green.
"""

import pytest

from odoo_log_to_junit import OdooLogParser, browser_skips


# Real-shape Odoo 19 log lines (odoo/tests/result.py startTest/addSkip).
START = (
    "2026-01-28 13:29:16,814 44 INFO odoo-test "
    "odoo.addons.kanit_portal.tests.test_portal_tour: "
    "Starting TestPortalTour.test_can_design_portal_tour ..."
)
SKIP_CHROME_PORT = (
    "2026-01-28 13:29:26,912 44 INFO odoo-test "
    "odoo.addons.kanit_portal.tests.test_portal_tour: "
    "skipped TestPortalTour.test_can_design_portal_tour : "
    "Failed to detect chrome devtools port after 12.0s."
)
START_PLAIN = (
    "2026-01-28 13:30:01,100 44 INFO odoo-test "
    "odoo.addons.sale.tests.test_sale_order: "
    "Starting TestSaleOrder.test_confirm ..."
)
SKIP_PLAIN = (
    "2026-01-28 13:30:01,900 44 INFO odoo-test "
    "odoo.addons.sale.tests.test_sale_order: "
    "skipped TestSaleOrder.test_confirm : requires demo data"
)
NEXT_START = (
    "2026-01-28 13:31:00,000 44 INFO odoo-test "
    "odoo.addons.sale.tests.test_sale_order: "
    "Starting TestSaleOrder.test_other ..."
)


def parse(lines):
    p = OdooLogParser()
    for line in lines:
        p.parse_line(line)
    if p.current_test:
        p.current_test.status = "passed"
        p.get_or_create_suite(p.current_test.module).tests.append(p.current_test)
    return p.suites


def only_test(suites, module):
    tests = suites[module].tests
    assert len(tests) == 1, f"expected 1 test, got {[t.name for t in tests]}"
    return tests[0]


class TestSkipParsing:
    def test_skipped_test_is_not_reported_as_passed(self):
        """The core regression: a skipped tour must not count as a pass."""
        suites = parse([START, SKIP_CHROME_PORT])
        test = only_test(suites, "kanit_portal")
        assert test.status == "skipped"

    def test_skip_reason_is_captured(self):
        suites = parse([START, SKIP_CHROME_PORT])
        test = only_test(suites, "kanit_portal")
        assert "chrome devtools port" in (test.message or "")

    def test_suite_counts_the_skip(self):
        suites = parse([START, SKIP_CHROME_PORT])
        assert suites["kanit_portal"].skipped == 1

    def test_passing_test_still_passes(self):
        """Regression guard: don't turn ordinary passes into skips."""
        suites = parse([START_PLAIN, NEXT_START])
        first = suites["sale"].tests[0]
        assert first.status == "passed"


class TestBrowserSkipDetection:
    def test_chrome_devtools_port_skip_is_a_browser_skip(self):
        suites = parse([START, SKIP_CHROME_PORT])
        assert len(browser_skips(suites)) == 1

    def test_unrelated_skip_is_not_a_browser_skip(self):
        suites = parse([START_PLAIN, SKIP_PLAIN])
        assert browser_skips(suites) == []

    @pytest.mark.parametrize(
        "reason",
        [
            "Chrome executable not found",
            "Error during Chrome headless connection",
            "Error during Chrome connection: never found 'page' target",
            "Cannot connect to chrome dev tools",
            "Failed to detect chrome devtools port after 12.0s.",
            "websocket-client module is not installed",
        ],
    )
    def test_every_odoo19_browser_skip_reason_is_detected(self, reason):
        """These are the SkipTest reasons in odoo/tests/common.py on 19.0."""
        line = (
            "2026-01-28 13:29:26,912 44 INFO odoo-test "
            "odoo.addons.kanit_portal.tests.test_portal_tour: "
            f"skipped TestPortalTour.test_tour : {reason}"
        )
        suites = parse([START, line])
        assert len(browser_skips(suites)) == 1, f"missed browser skip: {reason}"


# Class-level skips are logged by odoo.tests.suite with a DIFFERENT shape:
#   skipped setUpClass (odoo.addons.base.tests.test_x.TestClass) : <reason>
# There is no preceding "Starting ..." line, and a skipped setUpClass takes the
# WHOLE class with it — so missing these understates lost coverage the most.
# Real log lines carry ANSI colour codes around the level.
SKIP_SETUPCLASS = (
    "2026-01-28 15:03:56,094 21 \x1b[1;32m\x1b[1;49mINFO\x1b[0m odoo "
    "odoo.tests.suite: skipped setUpClass "
    "(odoo.addons.base.tests.test_test_suite.TestClassSetup) : Skip this class"
)
SKIP_SETUPCLASS_BROWSER = (
    "2026-01-28 15:03:56,094 21 INFO odoo "
    "odoo.tests.suite: skipped setUpClass "
    "(odoo.addons.kanit_portal.tests.test_portal_tour.TestPortalTour) : "
    "Chrome executable not found"
)


class TestClassLevelSkips:
    def test_setupclass_skip_is_recorded(self):
        suites = parse([SKIP_SETUPCLASS])
        test = only_test(suites, "base")
        assert test.status == "skipped"

    def test_setupclass_skip_reason_captured(self):
        suites = parse([SKIP_SETUPCLASS])
        assert only_test(suites, "base").message == "Skip this class"

    def test_setupclass_skip_names_the_class(self):
        suites = parse([SKIP_SETUPCLASS])
        assert "TestClassSetup" in only_test(suites, "base").name

    def test_browser_setupclass_skip_is_a_browser_skip(self):
        suites = parse([SKIP_SETUPCLASS_BROWSER])
        assert len(browser_skips(suites)) == 1

    def test_setupclass_skip_does_not_swallow_a_running_test(self):
        """A class skip must not be misattributed to an in-flight test."""
        suites = parse([START_PLAIN, SKIP_SETUPCLASS, NEXT_START])
        sale = [t for t in suites["sale"].tests if t.name == "TestSaleOrder.test_confirm"]
        assert sale and sale[0].status == "passed"
        assert only_test(suites, "base").status == "skipped"


# --- reasons that are legitimate under CI and must NOT fail the build ---
LEGIT_SKIPS = [
    "Needs demo data to be able to import those files",
    "pdfminer not installed",
    "aiosmtpd couldn't be imported",
    "unaccent not enabled",
    "Could not load the PdfSigner class properly",
    "only meaningful in a browser environment",   # a developer's own skip
]


class TestLegitimateCiSkips:
    @pytest.mark.parametrize("reason", LEGIT_SKIPS)
    def test_legit_skip_is_not_a_browser_skip(self, reason):
        """These come up on real CI runs; failing on them would be noise."""
        line = (
            "2026-01-28 13:30:01,900 44 INFO odoo-test "
            "odoo.addons.base.tests.test_x: "
            f"skipped TestX.test_y : {reason}"
        )
        suites = parse([START_PLAIN, line])
        assert browser_skips(suites) == [], f"false positive on: {reason}"

    def test_legit_skips_are_still_recorded_as_skipped(self):
        line = (
            "2026-01-28 13:30:01,900 44 INFO odoo-test "
            "odoo.addons.base.tests.test_x: "
            "skipped TestX.test_y : pdfminer not installed"
        )
        suites = parse([line])
        assert suites["base"].skipped == 1


class TestAllowlist:
    def test_allowlist_excuses_a_browser_skip(self):
        suites = parse([START, SKIP_CHROME_PORT])
        import re as _re
        allow = [_re.compile("devtools port", _re.IGNORECASE)]
        assert browser_skips(suites, allow) == []

    def test_allowlist_does_not_excuse_unrelated_browser_skips(self):
        line = (
            "2026-01-28 13:29:26,912 44 INFO odoo-test "
            "odoo.addons.kanit_portal.tests.test_portal_tour: "
            "skipped TestPortalTour.test_tour : Chrome executable not found"
        )
        import re as _re
        suites = parse([START, line])
        allow = [_re.compile("devtools port", _re.IGNORECASE)]
        assert len(browser_skips(suites, allow)) == 1

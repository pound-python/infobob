import unittest
import datetime

import mock

from infobat import util

class TestParseRelativeTimeString(unittest.TestCase):
    """Test infobat.util.parse_relative_time_string ."""

    def test_empty(self):
        with self.assertRaises(ValueError):
          util.parse_relative_time_string("")

    def test_case(self):
        """Both lowercase and uppercase work."""
        expected = dict(seconds=1)
        self.assertEqual(expected, util.parse_relative_time_string("+1s"))
        self.assertEqual(expected, util.parse_relative_time_string("+1S"))

    def test_combined(self):
        """Use short forms of all relative quantities."""
        expected = dict(
            seconds=1, minutes=2, hours=3, days=4, weeks=5, months=6, years=7)
        self.assertEqual(
            expected,
            util.parse_relative_time_string("+1s 2min 3h 4d 5w 6mo 7y"))

    def test_combined_long(self):
        """Use long forms of all relative quantities."""
        expected = dict(
            seconds=1, minutes=2, hours=3, days=4, weeks=5, months=6, years=7)
        self.assertEqual(
            expected,
            util.parse_relative_time_string(
                "+1seconds 2minutes 3hours 4days 5weeks 6months 7years"))

    def test_combined_long_singular(self):
        """Use singular long forms of all relative quantities."""
        expected = dict(
            seconds=1, minutes=2, hours=3, days=4, weeks=5, months=6, years=7)
        self.assertEqual(
            expected,
            util.parse_relative_time_string(
                "+1second 2minute 3hour 4day 5week 6month 7year"))

    def test_multiple_plusses(self):
        """Test that parsing can handle repeated +foos"""
        self.assertEqual(
            dict(seconds=3, hours=2), util.parse_relative_time_string("+3s +2h"))

    def test_whitespace_insensitive(self):
        """Test that parsing can handle various kinds of inserted whitespace."""
        expected = dict(seconds=3)
        # I have raged because of this.
        self.assertEqual(expected, util.parse_relative_time_string("+3s"))
        self.assertEqual(expected, util.parse_relative_time_string(" +3s")) 
        self.assertEqual(expected, util.parse_relative_time_string("+ 3s"))
        self.assertEqual(expected, util.parse_relative_time_string("+3 s"))
        self.assertEqual(expected, util.parse_relative_time_string("+3s "))
        self.assertEqual(expected, util.parse_relative_time_string(" + 3 s "))

        # Also, glued together:
        self.assertEqual(
            dict(seconds=3, hours=2), util.parse_relative_time_string("+3s2h"))
        self.assertEqual(
            dict(seconds=3, hours=2), util.parse_relative_time_string("+3s+2h"))

    def test_repeated_unit(self):
        """Don't repeat units, it's a mistake."""
        self.assertEqual(
            dict(seconds=3), util.parse_relative_time_string("+3s +3seconds"))
        with self.assertRaises(ValueError):
            util.parse_relative_time_string("+3s +4seconds")

    def test_invalid_unit(self):
        """Parse failure: kellicams are length, not time."""
        with self.assertRaises(ValueError):
            util.parse_relative_time_string("+3kellicams")

    def test_ambiguous_m(self):
        """Fuck m. It could mean minutes or months."""
        with self.assertRaises(ValueError):
            util.parse_relative_time_string("+3m")

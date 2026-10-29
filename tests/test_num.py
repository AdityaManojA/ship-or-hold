import unittest

from _load import load_tool

num = load_tool("proof-core", "num.py")


class Num(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(num.parse_number(7), 7.0)
        self.assertEqual(num.parse_number("7"), 7.0)
        self.assertEqual(num.parse_number("7.5"), 7.5)

    def test_locale_comma_decimal(self):
        self.assertEqual(num.parse_number("12,50"), 12.5)
        self.assertEqual(num.parse_number("1.234,56"), 1234.56)

    def test_locale_dot_decimal(self):
        self.assertEqual(num.parse_number("1,234.56"), 1234.56)
        self.assertEqual(num.parse_number("1,234,567"), 1234567)
        self.assertEqual(num.parse_number("1.234.567"), 1234567)

    def test_currency_and_sign(self):
        self.assertEqual(num.parse_number("$1,234.56"), 1234.56)
        self.assertEqual(num.parse_number("-12.5"), -12.5)
        self.assertEqual(num.parse_number("(12.5)"), -12.5)

    def test_ambiguous_refused(self):
        for s in ("1,250", "1.250"):
            with self.assertRaises(num.BadNumber):
                num.parse_number(s)

    def test_forced_decimal(self):
        self.assertEqual(num.parse_number("1,250", decimal="."), 1250)
        self.assertEqual(num.parse_number("1,250", decimal=","), 1.25)

    def test_missing_is_error(self):
        with self.assertRaises(num.MissingInput):
            num.parse_number(None)
        with self.assertRaises(num.MissingInput):
            num.parse_number("")
        with self.assertRaises(num.MissingInput):
            num.require({}, "price")

    def test_not_a_number(self):
        with self.assertRaises(num.BadNumber):
            num.parse_number("abc")
        with self.assertRaises(num.BadNumber):
            num.parse_number(True)

    def test_require_and_optional(self):
        self.assertEqual(num.require({"price": "9,99"}, "price"), 9.99)
        self.assertEqual(num.optional({}, "x", default=3), 3)
        self.assertIsNone(num.optional({"x": ""}, "x"))
        self.assertEqual(num.require_list({"a": [1]}, "a"), [1])
        with self.assertRaises(num.MissingInput):
            num.require_list({"a": []}, "a")


if __name__ == "__main__":
    unittest.main()

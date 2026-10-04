import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from payout_bridge.money import AmountError, FeeSchedule, exponent, fmt, parse_decimal, parse_minor, to_minor
from payout_bridge.timeparse import TimeError, parse_date, parse_datetime, zone

UTC = timezone.utc


class Amounts(unittest.TestCase):
    def test_plain_and_signed(self):
        self.assertEqual(parse_decimal("12.30"), Decimal("12.30"))
        self.assertEqual(parse_decimal("-12.30"), Decimal("-12.30"))
        self.assertEqual(parse_decimal("+5"), Decimal("5"))
        self.assertEqual(parse_decimal("(12.30)"), Decimal("-12.30"))
        self.assertEqual(parse_decimal("12.30-"), Decimal("-12.30"))
        self.assertIsNone(parse_decimal(""))
        self.assertIsNone(parse_decimal("  "))

    def test_thousands(self):
        self.assertEqual(parse_decimal("1,234.56"), Decimal("1234.56"))
        self.assertEqual(parse_decimal("1,234,567"), Decimal("1234567"))
        self.assertEqual(parse_decimal("1.234,56"), Decimal("1234.56"))
        self.assertEqual(parse_decimal("1 234.56"), Decimal("1234.56"))
        self.assertEqual(parse_decimal("1 234,56"), Decimal("1234.56"))

    def test_decimal_comma(self):
        self.assertEqual(parse_decimal("12,5"), Decimal("12.5"))
        self.assertEqual(parse_decimal("12,50"), Decimal("12.50"))
        self.assertEqual(parse_decimal("1.234", decimal_comma=True), Decimal("1234"))
        self.assertEqual(parse_decimal("1234,567", decimal_comma=True), Decimal("1234.567"))

    def test_three_decimal_currencies(self):
        self.assertEqual(parse_minor("12.345", "KWD"), 12345)
        self.assertEqual(parse_minor("1,234.500", "KWD"), 1234500)
        self.assertEqual(parse_minor("1,234", "KWD", decimal_comma=True), 1234)
        with self.assertRaises(AmountError):
            parse_minor("1,234", "KWD")
        with self.assertRaises(AmountError):
            parse_minor("1.234", "KWD", decimal_comma=True)
        self.assertEqual(parse_minor("1,234", "USD"), 123400)

    def test_ambiguous_is_an_error(self):
        for text in ("1,23,4", "1.2.3", "12,345.6.7", "1,2345", "12.5x", "abc", "1.234.56", "1,234,56"):
            with self.subTest(text=text), self.assertRaises(AmountError):
                parse_decimal(text)
        with self.assertRaises(AmountError):
            parse_decimal("12.5", decimal_comma=True)

    def test_minor_units(self):
        self.assertEqual(to_minor(Decimal("12.30"), "USD"), 1230)
        self.assertEqual(to_minor(Decimal("1500"), "JPY"), 1500)
        self.assertEqual(to_minor(Decimal("1500.00"), "JPY"), 1500)
        self.assertEqual(to_minor(Decimal("1.234"), "KWD"), 1234)
        self.assertEqual(parse_minor("0.10", "usd"), 10)
        with self.assertRaises(AmountError):
            to_minor(Decimal("12.345"), "USD")
        with self.assertRaises(AmountError):
            to_minor(Decimal("1500.5"), "JPY")
        self.assertEqual(exponent("JPY", {"JPY": 2}), 2)

    def test_fmt(self):
        self.assertEqual(fmt(123456, "USD"), "1,234.56")
        self.assertEqual(fmt(-5, "USD"), "−0.05")
        self.assertEqual(fmt(5, "USD", sign=True), "+0.05")
        self.assertEqual(fmt(0, "USD", sign=True), "0.00")
        self.assertEqual(fmt(1500, "JPY"), "1,500")
        self.assertEqual(fmt(1234, "KWD"), "1.234")

    def test_fee_schedule(self):
        s = FeeSchedule("2.9%+0.30")
        self.assertEqual(s.expected(10000, "USD"), 320)
        self.assertEqual(s.expected(-10000, "USD"), 320)
        self.assertEqual(s.expected(1950, "USD"), 87)  # 56.55 → 57, + 30
        self.assertEqual(FeeSchedule("1.4%").expected(10000, "EUR"), 140)
        self.assertEqual(FeeSchedule("3.6% + 40").expected(1000, "JPY"), 76)
        with self.assertRaises(ValueError):
            FeeSchedule("2.9")


class Times(unittest.TestCase):
    def test_zones(self):
        self.assertEqual(zone("UTC").utcoffset(None), timedelta(0))
        self.assertEqual(zone("+09:00").utcoffset(None), timedelta(hours=9))
        self.assertEqual(zone("-0530").utcoffset(None), -timedelta(hours=5, minutes=30))
        self.assertEqual(zone("CEST").utcoffset(None), timedelta(hours=2))
        self.assertEqual(zone("JST").utcoffset(None), timedelta(hours=9))
        self.assertEqual(zone("Europe/Amsterdam").utcoffset(datetime(2026, 7, 1)), timedelta(hours=2))
        for ambiguous in ("BST", "IST", "CST", "AST"):
            with self.subTest(zone=ambiguous), self.assertRaises(TimeError):
                zone(ambiguous)
        with self.assertRaises(TimeError):
            zone("Mars/Olympus")

    def test_datetimes(self):
        self.assertEqual(parse_datetime("2026-09-14 10:00:00"), datetime(2026, 9, 14, 10, tzinfo=UTC))
        self.assertEqual(parse_datetime("2026-09-14T10:00:00Z"), datetime(2026, 9, 14, 10, tzinfo=UTC))
        self.assertEqual(parse_datetime("2026-09-14T19:00:00+09:00"), datetime(2026, 9, 14, 10, tzinfo=UTC))
        self.assertEqual(parse_datetime("2026-09-14 19:00:00 +0900"), datetime(2026, 9, 14, 10, tzinfo=UTC))
        self.assertEqual(parse_datetime("2026-09-14 19:00", zone("+09:00")), datetime(2026, 9, 14, 10, tzinfo=UTC))
        self.assertEqual(parse_datetime("2026-09-14"), datetime(2026, 9, 14, tzinfo=UTC))
        self.assertEqual(parse_datetime("2026-09-14 10:00:00.250000").microsecond, 250000)
        self.assertEqual(parse_datetime("2026-09-14 12:00:00+02"), datetime(2026, 9, 14, 10, tzinfo=UTC))  # PostgreSQL
        self.assertEqual(parse_datetime("2026-09-14 10:00:00+00"), datetime(2026, 9, 14, 10, tzinfo=UTC))
        self.assertEqual(parse_datetime("2026-09-14 12:00:00.123456789+02:00"), datetime(2026, 9, 14, 10, 0, 0, 123456, tzinfo=UTC))
        self.assertEqual(parse_datetime("2026-09-14 10:00:00,5"), datetime(2026, 9, 14, 10, 0, 0, 500000, tzinfo=UTC))
        self.assertEqual(parse_datetime("1789466400"), datetime(2026, 9, 15, 10, tzinfo=UTC))
        self.assertIsNone(parse_datetime(""))
        with self.assertRaises(TimeError):
            parse_datetime("14/09/2026")

    def test_dates(self):
        self.assertEqual(parse_date("2026-09-14"), date(2026, 9, 14))
        self.assertEqual(parse_date("2026-09-14 00:00:00"), date(2026, 9, 14))
        self.assertEqual(parse_date("2026-09-14T23:30:00-05:00"), date(2026, 9, 14))
        self.assertIsNone(parse_date(""))
        with self.assertRaises(TimeError):
            parse_date("2026-02-30")


if __name__ == "__main__":
    unittest.main()

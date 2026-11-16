import unittest
from onboarding.reconcile import match_rows


class ReconciliationTests(unittest.TestCase):
    def row(self, description='Coffee', prefix='Spend_', amount=10):
        return dict(description=description,prefix=prefix,month='2026-11',amount=amount)

    def tx(self, name='Coffee', prefix='Spend_', amount=10):
        return dict(name=name,prefix=prefix,month='2026-11',amount=amount)

    def test_unique_name_and_amount_match(self):
        self.assertEqual(len(match_rows([self.row()], [self.tx()])['matches']), 1)

    def test_edited_name_requires_review_even_for_unique_amount(self):
        result=match_rows([self.row('My coffee')], [self.tx()])
        self.assertEqual(result['matches'], [])
        self.assertEqual(result['review'][0]['reason'], 'edited_description')

    def test_repeated_identical_rows_are_not_guessed(self):
        self.assertFalse(match_rows([self.row(),self.row()], [self.tx(),self.tx()])['matches'])

    def test_same_amount_in_different_account_is_not_a_match(self):
        self.assertFalse(match_rows([self.row(prefix='Savings_')], [self.tx()])['matches'])

    def test_other_month_and_opposite_sign_are_not_matches(self):
        other=self.tx();other['month']='2026-10'
        self.assertFalse(match_rows([self.row()], [other,self.tx(amount=-10)])['matches'])

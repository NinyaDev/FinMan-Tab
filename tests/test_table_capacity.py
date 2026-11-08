import unittest
from clients.table_capacity import expansion_requests


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.table={'tableId':'1','range':{'sheetId':7,'startRowIndex':25,'endRowIndex':43,
                                         'startColumnIndex':8,'endColumnIndex':10}}

    def test_total_is_moved_outside_data_and_style_is_one_row(self):
        footer={**self.table['range'],'startRowIndex':42}
        rule={'ranges':[footer],'booleanRule':{'condition':{'type':'CUSTOM_FORMULA'}}}
        requests=expansion_requests(self.table,[['Total','=SUM(J27:J42)']], [rule])
        self.assertEqual(requests[1]['updateCells']['range']['endRowIndex'],52)
        self.assertEqual(requests[2]['updateCells']['rows'][0]['values'][1]['userEnteredValue'],
                         {'formulaValue':'=SUM(J27:J52)'})
        self.assertEqual(requests[3]['updateConditionalFormatRule']['rule']['ranges'][0]['startRowIndex'],52)

    def test_occupied_space_or_custom_formula_is_not_overwritten(self):
        for rows in ([['Total','=SUM(J27:J42)'],['Personal note']], [['Total','=AVERAGE(J27:J42)']]):
            with self.assertRaises(ValueError): expansion_requests(self.table,rows,[])

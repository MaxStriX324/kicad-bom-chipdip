import csv
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kicad_bom_chipdip import (
    classify_value, convert, load_mapping, looks_like_part_number,
    read_csv_table, write_outputs,
)


class BomTests(unittest.TestCase):
    def test_heuristic(self):
        self.assertTrue(looks_like_part_number('AMC1301DWVR'))
        self.assertTrue(looks_like_part_number('BAV99'))
        self.assertTrue(looks_like_part_number('1N4148 NXP'))
        self.assertFalse(looks_like_part_number('100nF'))
        self.assertFalse(looks_like_part_number('10k'))
        self.assertFalse(looks_like_part_number('STM32F405VGTx'))
        self.assertFalse(looks_like_part_number('ACS712xLCTR-30A'))
        self.assertFalse(looks_like_part_number('Power_line'))

    def test_passives_and_nominal_search(self):
        part, status, _ = classify_value('100nF', 'Capacitor_SMD:C_0805_2012Metric', 'C1,C2', False)
        self.assertEqual((part, status), ('', 'unresolved'))
        part, status, _ = classify_value('100nF', 'Capacitor_SMD:C_0805_2012Metric', 'C1,C2', True)
        self.assertEqual((part, status), ('100nF 0805', 'nominal_search'))

    def test_quantity_group_merge_and_excluded(self):
        rows = [
            {'reference':'U1,U2', 'qty':'2', 'value':'AMC1301DWVR', 'footprint':'X'},
            {'reference':'U3', 'qty':'1', 'value':'AMC1301DWVR', 'footprint':'X'},
            {'reference':'R1,R2', 'qty':'2', 'value':'1k', 'footprint':'R_0805'},
            {'reference':'D1', 'qty':'1', 'value':'BAV99', 'dnp':'1'},
        ]
        items, warnings = convert(rows, {}, {}, boards=3)
        self.assertEqual(len(items), 3)
        self.assertEqual(sum(i.qty for i in items), 15)
        self.assertEqual(warnings, [])
        with tempfile.TemporaryDirectory() as folder:
            paths = write_outputs(Path('test.csv'), items, warnings, Path(folder), False)
            self.assertIn('AMC1301DWVR\t9', paths['chipdip'].read_text())
            self.assertIn('1k 0805\t6', paths['chipdip'].read_text())
            self.assertEqual(paths['candidates'].read_text().strip(), 'AMC1301DWVR\t9')
            self.assertTrue(paths['mapping'].exists())

    def test_mapping_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'mapping.csv'
            path.write_text('Reference;Value;Footprint;PartNumber\nR1,R2;10k;R_0805;RES-ABC\n', encoding='utf-8')
            ref_map, value_map = load_mapping(path)
            rows = [{'reference':'R1,R2','qty':'2','value':'10k','footprint':'R_0805'}]
            items, _ = convert(rows, ref_map, value_map)
            self.assertEqual((items[0].part, items[0].status), ('RES-ABC', 'mapped'))
            outputs = write_outputs(Path('foo.csv'), items, [], Path(folder), False)
            outputs['mapping'].write_text('user edits', encoding='utf-8')
            write_outputs(Path('foo.csv'), items, [], Path(folder), False)
            self.assertEqual(outputs['mapping'].read_text(), 'user edits')

    def test_kicad_csv_quoted_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'in.csv'
            file.write_text('"Reference","Qty","Value","Footprint"\n"C1,C2","2","100nF","C_0805"\n', encoding='utf-8')
            rows, _ = read_csv_table(file)
            self.assertEqual(rows[0]['reference'], 'C1,C2')
            self.assertEqual(rows[0]['qty'], '2')

    def test_invalid_qty(self):
        with self.assertRaisesRegex(ValueError, 'Qty'):
            convert([{'reference':'R1','qty':'many','value':'1k'}], {}, {})


if __name__ == '__main__':
    unittest.main()

import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kicad_bom_chipdip import convert, read_csv_table


class ExtendedKiCadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.input = self.root / 'Сборка платы зарядки (версия 2).csv'
        with self.input.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['Reference', 'Qty', 'Value', 'DNP', 'Exclude from BOM',
                             'Exclude from Board', 'Footprint', 'Datasheet',
                             'MANUFACTURER', 'MF', 'MP', 'Availability', 'Описание', '#'])
            writer.writerow(['U7', 1, 'AMC1301', '', '', '', 'Package_SO:SOIC-8',
                             'https://example.com/datasheet.pdf', 'TEXAS INSTRUMENTS',
                             'Texas Instruments', 'AMC1301DWVR', 'In Stock', 'Amplifier', 1])
            writer.writerow(['C1,C2', 2, '100nF', '', '', '',
                             'Capacitor_SMD:C_0805_2012Metric', '', '', '', '', '', 'Capacitor', 2])
            writer.writerow(['R1', 1, '10k', '1', '', '',
                             'Resistor_SMD:R_0805_2012Metric', '', '', '', '', '', 'Resistor', 3])
            writer.writerow(['D1', 1, 'BAV99', '', '', '',
                             'Diode_SMD:D_SOT-23', '', '', '', '', '', 'Diode', 4])

    def test_all_columns_and_explicit_mp(self):
        rows, headers = read_csv_table(self.input)
        self.assertIn('mp', headers)
        self.assertIn('описание', headers)
        self.assertEqual(len(rows), 4)
        items, warnings = convert(rows, {}, {})
        self.assertEqual((items[0].part, items[0].status), ('AMC1301DWVR', 'explicit'))
        self.assertEqual(sum(i.qty for i in items), 4)
        self.assertEqual(items[0].source['mf'], 'Texas Instruments')
        self.assertEqual(items[0].source['описание'], 'Amplifier')
        self.assertEqual(warnings, [])

    def test_cli_arbitrary_filename_and_review_fields(self):
        root = Path(__file__).resolve().parents[1]
        args = [sys.executable, str(root / 'kicad_bom_chipdip.py'), str(self.input),
                '--boards', '2']
        proc = subprocess.run(args, cwd=self.root, capture_output=True,
                              text=True, encoding='utf-8')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((self.root / 'Сборка платы зарядки (версия 2)_chipdip.txt')
                         .read_text(encoding='utf-8').splitlines(),
                         ['AMC1301DWVR\t2'])
        self.assertEqual((self.root / 'Сборка платы зарядки (версия 2)_chipdip_candidates_only.txt').read_text(encoding='utf-8').strip(), 'BAV99\t2')
        self.assertEqual((self.root / 'Сборка платы зарядки (версия 2)_search_all.txt').read_text(encoding='utf-8').splitlines(), ['AMC1301DWVR\t2', 'конденсатор керамический SMD 100 нФ 0805\t4', 'BAV99 SOT-23\t2'])
        with (self.root / 'Сборка платы зарядки (версия 2)_review.csv') \
                .open(encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle, delimiter=';')
            self.assertIn('SearchQuery', reader.fieldnames)
            self.assertIn('ImportReady', reader.fieldnames)
            self.assertIn('KiCad:mf', reader.fieldnames)
            self.assertIn('KiCad:описание', reader.fieldnames)
            self.assertEqual(next(reader)['KiCad:mp'], 'AMC1301DWVR')


if __name__ == '__main__':
    unittest.main()

import csv
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kicad_bom_chipdip import read_csv_table, load_mapping, convert, write_outputs


class FullExportTests(unittest.TestCase):
    def test_auto_mapping_for_named_csv(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            bom = folder / 'Блок питания (редакция 12).csv'
            map_csv = folder / 'Блок питания (редакция 12)_mapping.csv'
            with bom.open('w', encoding='utf-8-sig', newline='') as f:
                w = csv.writer(f)
                w.writerow(['Reference', 'Qty', 'Value', 'Footprint', 'MP'])
                w.writerow(['TP1,TP2,TP3', 3, 'Signal', 'Connector_PinHeader:PinHeader_1x01_P2.54mm_Vertical', ''])
                w.writerow(['R1,R2', 2, '120', 'Resistor_SMD:R_0805', ''])
                w.writerow(['U1', 1, 'AMC1301', 'Package_SO:SOIC-8', 'AMC1301DWVR'])
            with map_csv.open('w', encoding='utf-8-sig', newline='') as f:
                w = csv.writer(f, delimiter=';')
                w.writerow(['Reference', 'PartNumber', 'SelectionNote'])
                for i in range(1, 4):
                    w.writerow([f'TP{i}', 'TEST-PCB-A-R-01', 'параметры отверстия, высоты ещё уточнить'])
            out = folder / 'out'
            p = subprocess.run([sys.executable, str(ROOT / 'kicad_bom_chipdip.py'), str(bom), '--out', str(out)],
                               capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn('Соответствия:', p.stdout)
            data = (out / 'Блок питания (редакция 12)_chipdip.txt').read_text(encoding='utf-8')
            self.assertEqual(data.splitlines(), ['TEST-PCB-A-R-01\t3', 'резистор SMD 120 Ом 0805\t2', 'AMC1301DWVR\t1'])
            verified = (out / 'Блок питания (редакция 12)_chipdip_mpn_only.txt').read_text(encoding='utf-8')
            self.assertEqual(verified.splitlines(), ['TEST-PCB-A-R-01\t3', 'AMC1301DWVR\t1'])
            total = sum(int(s.rsplit('\t', 1)[1]) for s in data.splitlines())
            self.assertEqual(total, 6)

    def test_without_mapping_all_lines_still_in_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / 'Any BOM.csv'
            source.write_text('Reference,Qty,Value,Footprint\n"C1,C2",2,100nF,Capacitor_SMD:C_0805\nTP1,1,Test,Connector_PinHeader:PinHeader_1x01\n', encoding='utf-8')
            p = subprocess.run([sys.executable, str(ROOT / 'kicad_bom_chipdip.py'), str(source)],
                               capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(p.returncode, 0, p.stderr)
            lines = (folder / 'Any BOM_chipdip.txt').read_text(encoding='utf-8').splitlines()
            self.assertEqual(sum(int(l.rsplit('\t',1)[1]) for l in lines), 3)
            self.assertEqual(len(lines), 2)
            self.assertEqual((folder/'Any BOM_chipdip_mpn_only.txt').read_text(encoding='utf-8'), '')

if __name__ == '__main__':
    unittest.main()

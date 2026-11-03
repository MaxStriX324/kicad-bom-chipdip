import csv
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kicad_bom_chipdip import BomItem, search_query, review_note, write_outputs


class SearchQualityTests(unittest.TestCase):
    def item(self, ref, value, fp, qty=1, status="unresolved", part=""):
        return BomItem(ref, qty, value, fp, part, status, "", {})

    def test_resistors_have_unambiguous_ohms(self):
        cases = {"120": "120 Ом", "200": "200 Ом", "220": "220 Ом",
                 "68k": "68 кОм", "0": "0 Ом", "40.2k": "40,2 кОм"}
        for value, suffix in cases.items():
            with self.subTest(value=value):
                query = search_query(self.item("R1,R2", value, "Resistor_SMD:R_0805_2012Metric", qty=2))
                self.assertIn(suffix, query)
                self.assertIn("0805", query)
                self.assertIn("резистор", query)
        self.assertIn("перемычка", search_query(self.item("R1", "0", "R_0805")))

    def test_capacitor_voltage_and_dimensions(self):
        query = search_query(self.item("C1", "470uF 35V", "Capacitor_THT:CP_Radial_D10.0mm_P5.00mm"))
        self.assertIn("470 мкФ", query)
        self.assertEqual(query.count("35 В"), 1)
        self.assertIn("диаметр 10.0 мм", query)
        self.assertIn("шаг 5.00 мм", query)
        self.assertIn("электролитический", query)

    def test_component_specific_queries_are_not_generic_footprints(self):
        self.assertEqual(search_query(self.item("TP1", "Probe_A", "Connector_PinHeader_2.54mm:PinHeader_1x01_P2.54mm_Vertical")),
                         "контакт тестовый штыревой 1x01 шаг 2,54 мм")
        self.assertIn("PhoenixContact", search_query(self.item("J1", "Power_line", "Connector_Phoenix_MSTB:PhoenixContact_MSTBA_2,5_3-G_1x03_P5.00mm")))
        self.assertIn("PEC11R-4220F-S0024", search_query(self.item("SW1", "RotaryEncoder_Switch", "MY:Bourns-PEC11R-4220F-S0024-0-0-0")))
        self.assertIn("ANR5045", search_query(self.item("L4", "10 uH", "Inductor_SMD:L_APV_ANR5045")))

    def test_x_placeholders_are_not_invented(self):
        stm = search_query(self.item("U1", "STM32F405VGTx", "Package_QFP:LQFP-100_14x14mm_P0.5mm"))
        self.assertIn("суффикс уточнить", stm)
        self.assertNotIn("VGT6", stm)
        acs = search_query(self.item("U2", "ACS712xLCTR-30A", "Package_SO:SOIC-8"))
        self.assertIn("вариант уточнить", acs)

    def test_review_and_export_checksums(self):
        items = [self.item("R1,R2", "120", "R_0805", qty=2),
                 self.item("TP1", "Test", "PinHeader_1x01", qty=1),
                 self.item("U1", "AMC1301", "SOIC-8", qty=1, part="AMC1301DWVR", status="explicit")]
        self.assertEqual(review_note(items[1])[0], "high")
        with tempfile.TemporaryDirectory() as d:
            out = write_outputs(Path("Лаборатория (1).csv"), items, [], Path(d), False)
            self.assertEqual(out["chipdip"].read_text(encoding="utf-8"), "AMC1301DWVR\t1\n")
            self.assertEqual(sum(int(line.rsplit("\t", 1)[1]) for line in out["search"].read_text(encoding="utf-8").splitlines()), 4)
            with out["needs_review"].open(encoding="utf-8-sig", newline="") as f:
                rr = list(csv.DictReader(f, delimiter=";"))
                self.assertEqual(len(rr), 2)
                self.assertIn("CheckBeforeOrder", rr[0])


if __name__ == "__main__":
    unittest.main()

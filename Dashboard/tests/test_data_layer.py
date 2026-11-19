import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data_layer import load_csv, load_snapshot, store_from_invoice

class TestDataLayer(unittest.TestCase):
    def test_store_online(self):
        self.assertEqual(store_from_invoice('536365','Online'),1)

    def test_store_periodic(self):
        self.assertIn(store_from_invoice('536365','Periodic'),range(2,6))

    def test_snapshot(self):
        stats=load_snapshot()
        self.assertEqual(stats['records_after_cleaning'],397884)
        self.assertEqual(stats['shed_replayed'],75326)

    def test_csv_total_and_enrichment(self):
        df,ops,source=load_csv()
        self.assertEqual(len(df),278740)
        self.assertEqual(int((df.sales_mode=='Online').sum()),278740)
        self.assertEqual(df.customer_id.nunique(),3814)
        self.assertEqual(df.product_id.nunique(),3608)
        self.assertEqual(df.store_id.nunique(),1)
        self.assertAlmostEqual(df.revenue.sum(),6075186.073,places=2)
        self.assertEqual(source,'CSV')

if __name__=='__main__':
    unittest.main()

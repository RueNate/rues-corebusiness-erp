import os
import tempfile
import unittest
from app import app, init_db, seed_demo

class CoreBusinessERPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp.close()
        app.config["TESTING"] = True
        import app as app_module
        self.original_db = app_module.DATABASE
        app_module.DATABASE = self.temp.name
        init_db()
        self.client = app.test_client()

    def tearDown(self):
        import app as app_module
        app_module.DATABASE = self.original_db
        try: os.unlink(self.temp.name)
        except OSError: pass

    def test_dashboard_loads(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Business overview", response.data)

    def test_customer_validation(self):
        response = self.client.post("/customers/new", data={"name": ""}, follow_redirects=True)
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Customer name is required", response.data)

    def test_create_customer_order_invoice_and_payment(self):
        response = self.client.post("/customers/new", data={
            "name":"Test Customer","email":"test@example.com","phone":"123","address":"Test"
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        import app as app_module
        with app_module.app.app_context():
            customer=app_module.get_db().execute("SELECT id FROM customers WHERE name='Test Customer'").fetchone()
            cid=customer["id"]
        response=self.client.post("/orders/new", data={
            "customer_id":str(cid),"order_date":"2026-10-22",
            "description[]":["Consulting","Support"],
            "quantity[]":["2","1"],"unit_price[]":["10.00","7.50"]
        }, follow_redirects=True)
        self.assertEqual(response.status_code,200)
        self.assertIn(b"27.50",response.data)
        with app_module.app.app_context():
            order=app_module.get_db().execute("SELECT * FROM sales_orders WHERE customer_id=?",(cid,)).fetchone()
        self.client.post(f"/orders/{order['id']}/confirm",follow_redirects=True)
        response=self.client.post(f"/orders/{order['id']}/invoice",follow_redirects=True)
        self.assertEqual(response.status_code,200)
        with app_module.app.app_context():
            invoice=app_module.get_db().execute("SELECT * FROM invoices WHERE sales_order_id=?",(order["id"],)).fetchone()
        response=self.client.post(f"/invoices/{invoice['id']}/payments",data={
            "payment_date":"2026-10-22","amount":"10.00","method":"cash"
        },follow_redirects=True)
        self.assertEqual(response.status_code,200)
        with app_module.app.app_context():
            updated=app_module.get_db().execute("SELECT * FROM invoices WHERE id=?",(invoice["id"],)).fetchone()
        self.assertEqual(updated["status"],"partial")
        self.assertEqual(float(updated["balance"]),17.5)

    def test_payment_over_balance_is_rejected(self):
        import app as app_module
        with app_module.app.app_context():
            seed_demo()
            invoice=app_module.get_db().execute("SELECT * FROM invoices LIMIT 1").fetchone()
            inv_id=invoice["id"]
            balance=float(invoice["balance"])
        response=self.client.post(f"/invoices/{inv_id}/payments",data={
            "payment_date":"2026-10-22","amount":str(balance+1),"method":"cash"
        },follow_redirects=True)
        self.assertIn(b"cannot exceed",response.data.lower())

    def test_api_health_and_customer_create(self):
        self.assertEqual(self.client.get("/api/v1/health").status_code,200)
        response=self.client.post("/api/v1/customers",json={"name":"API Test Customer"})
        self.assertEqual(response.status_code,201)
        self.assertEqual(response.json["data"]["name"],"API Test Customer")

if __name__ == "__main__":
    unittest.main()

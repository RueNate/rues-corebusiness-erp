import os
import sqlite3
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from flask import Flask, g, render_template, request, redirect, url_for, flash, jsonify, abort

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.environ.get("RUES_ERP_DATABASE", os.path.join(APP_DIR, "rues_corebusiness_erp.db"))
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("RUES_ERP_SECRET_KEY", "local-demo-key-change-before-deployment")

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS customers (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT, phone TEXT,
 address TEXT, status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sales_orders (
 id INTEGER PRIMARY KEY AUTOINCREMENT, reference TEXT NOT NULL UNIQUE, customer_id INTEGER NOT NULL,
 order_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', notes TEXT,
 subtotal NUMERIC NOT NULL DEFAULT 0, total NUMERIC NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS sales_order_items (
 id INTEGER PRIMARY KEY AUTOINCREMENT, sales_order_id INTEGER NOT NULL, description TEXT NOT NULL,
 quantity NUMERIC NOT NULL, unit_price NUMERIC NOT NULL, line_total NUMERIC NOT NULL,
 FOREIGN KEY(sales_order_id) REFERENCES sales_orders(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS invoices (
 id INTEGER PRIMARY KEY AUTOINCREMENT, reference TEXT NOT NULL UNIQUE, customer_id INTEGER NOT NULL,
 sales_order_id INTEGER NOT NULL UNIQUE, invoice_date TEXT NOT NULL, due_date TEXT,
 status TEXT NOT NULL DEFAULT 'unpaid', subtotal NUMERIC NOT NULL, total NUMERIC NOT NULL,
 amount_paid NUMERIC NOT NULL DEFAULT 0, balance NUMERIC NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE RESTRICT,
 FOREIGN KEY(sales_order_id) REFERENCES sales_orders(id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS payments (
 id INTEGER PRIMARY KEY AUTOINCREMENT, reference TEXT NOT NULL UNIQUE, invoice_id INTEGER NOT NULL,
 payment_date TEXT NOT NULL, amount NUMERIC NOT NULL, method TEXT NOT NULL, notes TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(name);
CREATE INDEX IF NOT EXISTS idx_orders_status ON sales_orders(status);
CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_invoices_customer ON invoices(customer_id);
"""

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE, timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA busy_timeout = 10000")
    return g.db

@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(DATABASE)
    db.executescript(SCHEMA)
    db.commit()
    db.close()

def money(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError):
        raise ValueError("Enter a valid amount.")

def reference(prefix):
    # Random suffix helps avoid collisions across multiple requests.
    import secrets
    return f"{prefix}-{date.today():%y%m%d}-{secrets.token_hex(3).upper()}"

def json_error(message, status=400, errors=None):
    payload = {"error": message}
    if errors:
        payload["details"] = errors
    return jsonify(payload), status

@app.template_filter("currency")
def currency(value):
    return f"${float(value or 0):,.2f}"

@app.template_filter("pretty_date")
def pretty_date(value):
    if not value:
        return "—"
    try:
        return date.fromisoformat(str(value)[:10]).strftime("%d %b %Y")
    except ValueError:
        return str(value)

@app.route("/")
def dashboard():
    db = get_db()
    stats = {
        "customers": db.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
        "orders": db.execute("SELECT COUNT(*) FROM sales_orders").fetchone()[0],
        "invoices": db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0],
        "outstanding": db.execute("SELECT COALESCE(SUM(balance),0) FROM invoices").fetchone()[0],
        "received": db.execute("SELECT COALESCE(SUM(amount),0) FROM payments").fetchone()[0],
        "overdue": db.execute("SELECT COALESCE(SUM(balance),0) FROM invoices WHERE balance > 0 AND due_date < ?", (date.today().isoformat(),)).fetchone()[0],
        "overdue_count": db.execute("SELECT COUNT(*) FROM invoices WHERE balance > 0 AND due_date < ?", (date.today().isoformat(),)).fetchone()[0],
    }
    recent_invoices = db.execute("""SELECT i.*, c.name customer_name FROM invoices i
        JOIN customers c ON c.id=i.customer_id ORDER BY i.id DESC LIMIT 6""").fetchall()
    recent_orders = db.execute("""SELECT o.*, c.name customer_name FROM sales_orders o
        JOIN customers c ON c.id=o.customer_id ORDER BY o.id DESC LIMIT 5""").fetchall()
    return render_template("dashboard.html", stats=stats, recent_invoices=recent_invoices, recent_orders=recent_orders)

@app.route("/customers")
def customers():
    db = get_db()
    q = request.args.get("q", "").strip()
    rows = db.execute("""SELECT c.*,
      (SELECT COUNT(*) FROM sales_orders o WHERE o.customer_id=c.id) order_count,
      (SELECT COUNT(*) FROM invoices i WHERE i.customer_id=c.id) invoice_count
      FROM customers c WHERE (?='' OR c.name LIKE ? OR COALESCE(c.email,'') LIKE ?)
      ORDER BY c.id DESC""", (q, f"%{q}%", f"%{q}%")).fetchall()
    return render_template("customers/index.html", customers=rows, q=q)

@app.route("/customers/new", methods=["GET","POST"])
def customer_new():
    if request.method == "POST":
        name = request.form.get("name","").strip()
        email = request.form.get("email","").strip() or None
        phone = request.form.get("phone","").strip() or None
        address = request.form.get("address","").strip() or None
        errors=[]
        if not name: errors.append("Customer name is required.")
        if len(name)>180: errors.append("Customer name must be 180 characters or fewer.")
        if email and ("@" not in email or "." not in email.split("@")[-1]): errors.append("Enter a valid email address.")
        if errors:
            for e in errors: flash(e,"error")
            return render_template("customers/form.html", form=request.form), 400
        db=get_db()
        cur=db.execute("INSERT INTO customers(name,email,phone,address,status) VALUES(?,?,?,?, 'active')",(name,email,phone,address))
        db.commit()
        flash("Customer created successfully.","success")
        return redirect(url_for("customer_detail", customer_id=cur.lastrowid))
    return render_template("customers/form.html", form={})

@app.route("/customers/<int:customer_id>")
def customer_detail(customer_id):
    db=get_db()
    customer=db.execute("SELECT * FROM customers WHERE id=?",(customer_id,)).fetchone()
    if not customer: abort(404)
    orders=db.execute("SELECT * FROM sales_orders WHERE customer_id=? ORDER BY id DESC",(customer_id,)).fetchall()
    invoices=db.execute("SELECT * FROM invoices WHERE customer_id=? ORDER BY id DESC",(customer_id,)).fetchall()
    return render_template("customers/detail.html", customer=customer, orders=orders, invoices=invoices)

@app.route("/orders")
def orders():
    db=get_db()
    status=request.args.get("status","").strip()
    q=request.args.get("q","").strip()
    rows=db.execute("""SELECT o.*,c.name customer_name FROM sales_orders o JOIN customers c ON c.id=o.customer_id
      WHERE (?='' OR o.status=?) AND (?='' OR o.reference LIKE ?)
      ORDER BY o.id DESC""",(status,status,q,f"%{q}%")).fetchall()
    return render_template("orders/index.html", orders=rows, status=status, q=q)

@app.route("/orders/new", methods=["GET","POST"])
def order_new():
    db=get_db()
    customer_rows=db.execute("SELECT id,name FROM customers WHERE status='active' ORDER BY name").fetchall()
    if request.method=="POST":
        try:
            customer_id=int(request.form.get("customer_id","0"))
            customer=db.execute("SELECT id FROM customers WHERE id=? AND status='active'",(customer_id,)).fetchone()
            if not customer: raise ValueError("Choose an active customer.")
            order_date=request.form.get("order_date","").strip()
            date.fromisoformat(order_date)
            descriptions=request.form.getlist("description[]")
            quantities=request.form.getlist("quantity[]")
            prices=request.form.getlist("unit_price[]")
            lines=[]
            if not descriptions or len(descriptions)>30: raise ValueError("Add between 1 and 30 order lines.")
            total=Decimal("0.00")
            for desc,qty,price in zip(descriptions,quantities,prices):
                desc=desc.strip()
                if not desc: raise ValueError("Every order line needs a description.")
                q=Decimal(qty)
                p=money(price)
                if q<=0 or q>Decimal("1000000") or p<0: raise ValueError("Quantity must be positive and unit price cannot be negative.")
                line=(q*p).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
                lines.append((desc,str(q),str(p),str(line)))
                total+=line
            total=total.quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
            cur=db.execute("""INSERT INTO sales_orders(reference,customer_id,order_date,status,notes,subtotal,total)
              VALUES(?,?,?,'draft',?,?,?)""",(reference("SO"),customer_id,order_date,request.form.get("notes","").strip() or None,str(total),str(total)))
            order_id=cur.lastrowid
            db.executemany("INSERT INTO sales_order_items(sales_order_id,description,quantity,unit_price,line_total) VALUES(?,?,?,?,?)",
              [(order_id,*line) for line in lines])
            db.commit()
            flash("Sales order saved as a draft.","success")
            return redirect(url_for("order_detail",order_id=order_id))
        except (ValueError, InvalidOperation, TypeError) as exc:
            db.rollback()
            flash(str(exc) or "Check the order details and try again.","error")
    return render_template("orders/form.html", customers=customer_rows, today=date.today().isoformat())

@app.route("/orders/<int:order_id>")
def order_detail(order_id):
    db=get_db()
    order=db.execute("""SELECT o.*,c.name customer_name,c.email customer_email FROM sales_orders o
      JOIN customers c ON c.id=o.customer_id WHERE o.id=?""",(order_id,)).fetchone()
    if not order: abort(404)
    items=db.execute("SELECT * FROM sales_order_items WHERE sales_order_id=?",(order_id,)).fetchall()
    invoice=db.execute("SELECT * FROM invoices WHERE sales_order_id=?",(order_id,)).fetchone()
    return render_template("orders/detail.html", order=order, items=items, invoice=invoice)

@app.post("/orders/<int:order_id>/confirm")
def order_confirm(order_id):
    db=get_db()
    order=db.execute("SELECT * FROM sales_orders WHERE id=?",(order_id,)).fetchone()
    if not order: abort(404)
    if order["status"]!="draft":
        flash("Only draft orders can be confirmed.","error")
    else:
        db.execute("UPDATE sales_orders SET status='confirmed' WHERE id=?",(order_id,))
        db.commit()
        flash("Order confirmed.","success")
    return redirect(url_for("order_detail",order_id=order_id))

@app.post("/orders/<int:order_id>/invoice")
def order_invoice(order_id):
    db=get_db()
    order=db.execute("SELECT * FROM sales_orders WHERE id=?",(order_id,)).fetchone()
    if not order: abort(404)
    existing=db.execute("SELECT id FROM invoices WHERE sales_order_id=?",(order_id,)).fetchone()
    if existing:
        return redirect(url_for("invoice_detail",invoice_id=existing["id"]))
    if order["status"]!="confirmed":
        flash("Confirm the order before generating an invoice.","error")
        return redirect(url_for("order_detail",order_id=order_id))
    today=date.today()
    cur=db.execute("""INSERT INTO invoices(reference,customer_id,sales_order_id,invoice_date,due_date,status,subtotal,total,amount_paid,balance)
      VALUES(?,?,?,?,?,'unpaid',?,?,0,?)""",(reference("INV"),order["customer_id"],order_id,today.isoformat(),(today+timedelta(days=30)).isoformat(),order["subtotal"],order["total"],order["total"]))
    db.commit()
    flash("Invoice generated successfully.","success")
    return redirect(url_for("invoice_detail",invoice_id=cur.lastrowid))

@app.route("/invoices")
def invoices():
    db=get_db()
    status=request.args.get("status","").strip()
    q=request.args.get("q","").strip()
    rows=db.execute("""SELECT i.*,c.name customer_name FROM invoices i JOIN customers c ON c.id=i.customer_id
      WHERE (?='' OR i.status=?) AND (?='' OR i.reference LIKE ?)
      ORDER BY i.id DESC""",(status,status,q,f"%{q}%")).fetchall()
    return render_template("invoices/index.html", invoices=rows, status=status, q=q)

@app.route("/invoices/<int:invoice_id>")
def invoice_detail(invoice_id):
    db=get_db()
    invoice=db.execute("""SELECT i.*,c.name customer_name,c.email customer_email,o.reference order_reference
      FROM invoices i JOIN customers c ON c.id=i.customer_id JOIN sales_orders o ON o.id=i.sales_order_id WHERE i.id=?""",(invoice_id,)).fetchone()
    if not invoice: abort(404)
    items=db.execute("SELECT * FROM sales_order_items WHERE sales_order_id=?",(invoice["sales_order_id"],)).fetchall()
    payments=db.execute("SELECT * FROM payments WHERE invoice_id=? ORDER BY id DESC",(invoice_id,)).fetchall()
    return render_template("invoices/detail.html", invoice=invoice, items=items, payments=payments)

@app.post("/invoices/<int:invoice_id>/payments")
def payment_new(invoice_id):
    db=get_db()
    invoice=db.execute("SELECT * FROM invoices WHERE id=?",(invoice_id,)).fetchone()
    if not invoice: abort(404)
    try:
        amount=money(request.form.get("amount",""))
        payment_date=request.form.get("payment_date","")
        date.fromisoformat(payment_date)
        method=request.form.get("method","")
        if method not in {"cash","bank_transfer","card","other"}: raise ValueError("Select a valid payment method.")
        balance=money(invoice["balance"])
        if amount<=0: raise ValueError("Payment must be greater than zero.")
        if amount>balance: raise ValueError("Payment cannot exceed the outstanding balance.")
        db.execute("BEGIN IMMEDIATE")
        # Re-read after acquiring the SQLite write lock to avoid stale balances.
        locked=db.execute("SELECT * FROM invoices WHERE id=?",(invoice_id,)).fetchone()
        balance=money(locked["balance"])
        if amount>balance: raise ValueError("Payment cannot exceed the outstanding balance.")
        db.execute("INSERT INTO payments(reference,invoice_id,payment_date,amount,method,notes) VALUES(?,?,?,?,?,?)",
          (reference("PAY"),invoice_id,payment_date,str(amount),method,request.form.get("notes","").strip() or None))
        paid=money(locked["amount_paid"])+amount
        new_balance=max(Decimal("0.00"),money(locked["total"])-paid)
        status="paid" if new_balance==0 else "partial"
        db.execute("UPDATE invoices SET amount_paid=?,balance=?,status=? WHERE id=?",(str(paid),str(new_balance),status,invoice_id))
        db.commit()
        flash("Payment recorded and invoice balance updated.","success")
    except (ValueError, InvalidOperation, TypeError) as exc:
        db.rollback()
        flash(str(exc) or "Could not record payment.","error")
    return redirect(url_for("invoice_detail",invoice_id=invoice_id))

@app.route("/reports")
def reports():
    db=get_db()
    metrics={
        "sales":db.execute("SELECT COALESCE(SUM(total),0) FROM sales_orders WHERE status IN ('confirmed','fulfilled')").fetchone()[0],
        "invoiced":db.execute("SELECT COALESCE(SUM(total),0) FROM invoices").fetchone()[0],
        "received":db.execute("SELECT COALESCE(SUM(amount),0) FROM payments").fetchone()[0],
        "outstanding":db.execute("SELECT COALESCE(SUM(balance),0) FROM invoices").fetchone()[0],
    }
    open_invoices=db.execute("""SELECT i.*,c.name customer_name FROM invoices i JOIN customers c ON c.id=i.customer_id
      WHERE i.balance>0 ORDER BY i.due_date ASC LIMIT 100""").fetchall()
    top_customers=db.execute("""SELECT c.id,c.name,COALESCE(SUM(i.total),0) billed FROM customers c
      LEFT JOIN invoices i ON i.customer_id=c.id GROUP BY c.id,c.name ORDER BY billed DESC LIMIT 5""").fetchall()
    return render_template("reports/index.html", metrics=metrics, open_invoices=open_invoices, top_customers=top_customers)

# JSON REST API. Intended for local demo; add authentication and rate limiting before any public deployment.
@app.get("/api/v1/health")
def api_health():
    return jsonify({"status":"ok","application":"Rues CoreBusiness ERP API","version":"1.0"})

@app.get("/api/v1/customers")
def api_customers_list():
    db=get_db()
    q=request.args.get("q","").strip()
    rows=db.execute("SELECT * FROM customers WHERE (?='' OR name LIKE ? OR COALESCE(email,'') LIKE ?) ORDER BY id DESC LIMIT 100",(q,f"%{q}%",f"%{q}%")).fetchall()
    return jsonify({"data":[dict(row) for row in rows]})

@app.post("/api/v1/customers")
def api_customers_create():
    data=request.get_json(silent=True) or {}
    name=str(data.get("name","")).strip()
    email=str(data.get("email","")).strip() or None
    if not name: return json_error("Customer name is required.",422,{"name":"required"})
    if len(name)>180: return json_error("Customer name is too long.",422,{"name":"max 180"})
    if email and ("@" not in email or "." not in email.split("@")[-1]): return json_error("Email is invalid.",422,{"email":"invalid"})
    db=get_db()
    cur=db.execute("INSERT INTO customers(name,email,phone,address,status) VALUES(?,?,?,?, 'active')",(name,email,data.get("phone"),data.get("address")))
    db.commit()
    row=db.execute("SELECT * FROM customers WHERE id=?",(cur.lastrowid,)).fetchone()
    return jsonify({"data":dict(row)}),201

@app.get("/api/v1/orders")
def api_orders_list():
    db=get_db()
    rows=db.execute("""SELECT o.*,c.name customer_name FROM sales_orders o JOIN customers c ON c.id=o.customer_id ORDER BY o.id DESC LIMIT 100""").fetchall()
    return jsonify({"data":[dict(r) for r in rows]})

@app.post("/api/v1/orders")
def api_orders_create():
    data=request.get_json(silent=True) or {}
    try:
        customer_id=int(data.get("customer_id",0))
        order_date=str(data.get("order_date","")).strip()
        date.fromisoformat(order_date)
        customer=get_db().execute("SELECT id FROM customers WHERE id=? AND status='active'",(customer_id,)).fetchone()
        if not customer: return json_error("Choose an active customer.",422,{"customer_id":"invalid"})
        items=data.get("items")
        if not isinstance(items,list) or not 1 <= len(items) <= 30:
            return json_error("Add between 1 and 30 order items.",422,{"items":"required array"})
        parsed=[]; total=Decimal("0.00")
        for item in items:
            description=str(item.get("description","")).strip()
            if not description: return json_error("Every order line needs a description.",422,{"items":"description required"})
            quantity=Decimal(str(item.get("quantity","0")))
            price=money(item.get("unit_price",""))
            if quantity<=0 or price<0: return json_error("Quantity must be positive and price cannot be negative.",422)
            line=(quantity*price).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
            parsed.append((description,str(quantity),str(price),str(line))); total+=line
        total=total.quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
        db=get_db()
        cur=db.execute("INSERT INTO sales_orders(reference,customer_id,order_date,status,notes,subtotal,total) VALUES(?,?,?,'draft',?,?,?)",
            (reference("SO"),customer_id,order_date,data.get("notes"),str(total),str(total)))
        order_id=cur.lastrowid
        db.executemany("INSERT INTO sales_order_items(sales_order_id,description,quantity,unit_price,line_total) VALUES(?,?,?,?,?)",[(order_id,*line) for line in parsed])
        db.commit()
        row=db.execute("SELECT * FROM sales_orders WHERE id=?",(order_id,)).fetchone()
        return jsonify({"data":dict(row),"items":[dict(r) for r in db.execute("SELECT * FROM sales_order_items WHERE sales_order_id=?",(order_id,)).fetchall()]}),201
    except (ValueError,InvalidOperation,TypeError) as exc:
        get_db().rollback()
        return json_error(str(exc) or "Invalid order data.",422)

@app.post("/api/v1/orders/<int:order_id>/confirm")
def api_order_confirm(order_id):
    db=get_db()
    order=db.execute("SELECT * FROM sales_orders WHERE id=?",(order_id,)).fetchone()
    if not order: return json_error("Order not found.",404)
    if order["status"]!="draft": return json_error("Only draft orders can be confirmed.",409)
    db.execute("UPDATE sales_orders SET status='confirmed' WHERE id=?",(order_id,)); db.commit()
    return jsonify({"data":dict(db.execute("SELECT * FROM sales_orders WHERE id=?",(order_id,)).fetchone())})

@app.post("/api/v1/orders/<int:order_id>/invoice")
def api_order_invoice(order_id):
    db=get_db()
    order=db.execute("SELECT * FROM sales_orders WHERE id=?",(order_id,)).fetchone()
    if not order: return json_error("Order not found.",404)
    existing=db.execute("SELECT * FROM invoices WHERE sales_order_id=?",(order_id,)).fetchone()
    if existing: return jsonify({"data":dict(existing),"message":"Invoice already exists."})
    if order["status"]!="confirmed": return json_error("Confirm the order before invoicing.",409)
    today=date.today()
    cur=db.execute("""INSERT INTO invoices(reference,customer_id,sales_order_id,invoice_date,due_date,status,subtotal,total,amount_paid,balance)
      VALUES(?,?,?,?,?,'unpaid',?,?,0,?)""",(reference("INV"),order["customer_id"],order_id,today.isoformat(),(today+timedelta(days=30)).isoformat(),order["subtotal"],order["total"],order["total"]))
    db.commit()
    return jsonify({"data":dict(db.execute("SELECT * FROM invoices WHERE id=?",(cur.lastrowid,)).fetchone())}),201

@app.post("/api/v1/invoices/<int:invoice_id>/payments")
def api_invoice_payment(invoice_id):
    data=request.get_json(silent=True) or {}
    try:
        payment_date=str(data.get("payment_date","")).strip()
        date.fromisoformat(payment_date)
        amount=money(data.get("amount",""))
        method=data.get("method","bank_transfer")
        if method not in {"cash","bank_transfer","card","other"}: return json_error("Invalid payment method.",422)
        if amount<=0: return json_error("Payment must be greater than zero.",422)
        db=get_db()
        db.execute("BEGIN IMMEDIATE")
        invoice=db.execute("SELECT * FROM invoices WHERE id=?",(invoice_id,)).fetchone()
        if not invoice:
            db.rollback(); return json_error("Invoice not found.",404)
        balance=money(invoice["balance"])
        if amount>balance:
            db.rollback(); return json_error("Payment cannot exceed the outstanding balance.",422,{"amount":"exceeds balance"})
        db.execute("INSERT INTO payments(reference,invoice_id,payment_date,amount,method,notes) VALUES(?,?,?,?,?,?)",
            (reference("PAY"),invoice_id,payment_date,str(amount),method,data.get("notes")))
        paid=money(invoice["amount_paid"])+amount
        new_balance=max(Decimal("0.00"),money(invoice["total"])-paid)
        status="paid" if new_balance==0 else "partial"
        db.execute("UPDATE invoices SET amount_paid=?,balance=?,status=? WHERE id=?",(str(paid),str(new_balance),status,invoice_id))
        db.commit()
        return jsonify({"data":dict(db.execute("SELECT * FROM invoices WHERE id=?",(invoice_id,)).fetchone())}),201
    except (ValueError,InvalidOperation,TypeError) as exc:
        get_db().rollback()
        return json_error(str(exc) or "Invalid payment data.",422)

@app.get("/api/v1/invoices")
def api_invoices_list():
    db=get_db()
    rows=db.execute("""SELECT i.*,c.name customer_name FROM invoices i JOIN customers c ON c.id=i.customer_id ORDER BY i.id DESC LIMIT 100""").fetchall()
    return jsonify({"data":[dict(r) for r in rows]})

@app.get("/api/v1/reports/summary")
def api_report_summary():
    db=get_db()
    return jsonify({"data":{
        "customers":db.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
        "orders":db.execute("SELECT COUNT(*) FROM sales_orders").fetchone()[0],
        "invoices":db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0],
        "sales_confirmed":float(db.execute("SELECT COALESCE(SUM(total),0) FROM sales_orders WHERE status IN ('confirmed','fulfilled')").fetchone()[0]),
        "payments_received":float(db.execute("SELECT COALESCE(SUM(amount),0) FROM payments").fetchone()[0]),
        "outstanding_balance":float(db.execute("SELECT COALESCE(SUM(balance),0) FROM invoices").fetchone()[0]),
    }})

@app.errorhandler(404)
def not_found(error):
    if request.path.startswith("/api/"): return json_error("Resource not found.",404)
    return render_template("errors/404.html"),404

def seed_demo():
    """Insert a small, internally consistent set of fictional Zimbabwean business records."""
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    if db.execute("SELECT COUNT(*) FROM customers").fetchone()[0]:
        db.close()
        return

    today = date.today()
    customers = [
        ("Mbare Office & Stationery (Pvt) Ltd", "accounts@mbareoffice.example", "+263 77 410 2201", "Workington, Harare"),
        ("Chiedza Facilities Services", "finance@chiedzafacilities.example", "+263 71 582 6402", "Belvedere, Harare"),
        ("Nyota Building Supplies", "accounts@nyotabuilding.example", "+263 78 334 1903", "Gweru, Midlands"),
        ("Tariro Business Consultants", "admin@tariroconsulting.example", "+263 77 693 1154", "Mutare, Manicaland"),
        ("Greenline Property Services", "finance@greenlineproperty.example", "+263 71 220 7815", "Chitungwiza, Mashonaland East"),
    ]
    customer_ids = {}
    for name, email, phone, address in customers:
        cur = db.execute("INSERT INTO customers(name,email,phone,address,status) VALUES(?,?,?,?, 'active')", (name,email,phone,address))
        customer_ids[name] = cur.lastrowid

    # Each sample invoice has a different real-world state: overdue, partial, paid and not yet due.
    examples = [
        {"customer":"Mbare Office & Stationery (Pvt) Ltd", "so":"SO-2026-0041", "inv":"INV-2026-0038", "days":-42, "due":-12, "status":"partial", "lines":[("Monthly stationery supply - October", 1, 860.00), ("Delivery to client premises", 2, 35.00)], "payments":[(-18, 400.00, "bank_transfer", "Bank transfer ref: DEMO-TRX-4108")]},
        {"customer":"Chiedza Facilities Services", "so":"SO-2026-0042", "inv":"INV-2026-0039", "days":-31, "due":-1, "status":"unpaid", "lines":[("Office cleaning service - September", 1, 540.00), ("Cleaning consumables", 1, 95.00)], "payments":[]},
        {"customer":"Nyota Building Supplies", "so":"SO-2026-0043", "inv":"INV-2026-0040", "days":-24, "due":6, "status":"partial", "lines":[("PVC piping and fittings", 12, 18.50), ("Plumbing call-out", 1, 145.00)], "payments":[(-10, 150.00, "cash", "Sample receipt RCPT-2026-0186")]},
        {"customer":"Tariro Business Consultants", "so":"SO-2026-0044", "inv":"INV-2026-0041", "days":-15, "due":15, "status":"paid", "lines":[("Business process review", 4, 95.00), ("Staff training session", 1, 180.00)], "payments":[(-13, 560.00, "bank_transfer", "Sample receipt RCPT-2026-0191")]},
        {"customer":"Greenline Property Services", "so":"SO-2026-0045", "inv":"INV-2026-0042", "days":-3, "due":27, "status":"unpaid", "lines":[("Property inspection service", 3, 110.00), ("Inspection report preparation", 1, 75.00)], "payments":[]},
    ]
    for item in examples:
        cid = customer_ids[item["customer"]]
        order_date = today + timedelta(days=item["days"])
        due_date = today + timedelta(days=item["due"])
        lines = item["lines"]
        total = sum((Decimal(str(qty)) * Decimal(str(price)) for _, qty, price in lines), Decimal("0.00")).quantize(Decimal("0.01"))
        cur = db.execute("INSERT INTO sales_orders(reference,customer_id,order_date,status,notes,subtotal,total) VALUES(?,?,?,'confirmed',?,?,?)",
            (item["so"], cid, order_date.isoformat(), "Fictional sample order for portfolio demonstration", str(total), str(total)))
        oid = cur.lastrowid
        db.executemany("INSERT INTO sales_order_items(sales_order_id,description,quantity,unit_price,line_total) VALUES(?,?,?,?,?)",
            [(oid, description, str(qty), str(price), str((Decimal(str(qty))*Decimal(str(price))).quantize(Decimal("0.01")))) for description, qty, price in lines])
        paid = sum((Decimal(str(payment[1])) for payment in item["payments"]), Decimal("0.00"))
        balance = total - paid
        status = "paid" if balance == 0 else ("partial" if paid > 0 else "unpaid")
        cur = db.execute("INSERT INTO invoices(reference,customer_id,sales_order_id,invoice_date,due_date,status,subtotal,total,amount_paid,balance) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (item["inv"], cid, oid, order_date.isoformat(), due_date.isoformat(), status, str(total), str(total), str(paid), str(balance)))
        iid = cur.lastrowid
        for offset, amount, method, notes in item["payments"]:
            payment_date = today + timedelta(days=offset)
            db.execute("INSERT INTO payments(reference,invoice_id,payment_date,amount,method,notes) VALUES(?,?,?,?,?,?)",
                (f"RCPT-{payment_date:%Y}-{iid:04d}", iid, payment_date.isoformat(), str(amount), method, notes))
    db.commit()
    db.close()

if __name__ == "__main__":
    init_db()
    seed_demo()
    print("Rues CoreBusiness ERP is starting at http://127.0.0.1:8000")
    app.run(host="127.0.0.1",port=int(os.environ.get("PORT","8000")),debug=True)

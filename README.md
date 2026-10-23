# Rues CoreBusiness ERP — Python Edition

A portfolio project demonstrating a small-business sales and finance workflow: customer records, sales orders, invoices, payments, management reports and a JSON REST API.

## Business problem being addressed

In small organisations, customer details, sales orders, invoices and receipts can be tracked in disconnected spreadsheets. That makes it harder to follow a request from initial order to payment and to understand outstanding balances. Rues CoreBusiness ERP connects these steps, calculates totals on the server, gives each document a reference, and provides a concise receivables view.

## Technology

- Python 3.10+
- Flask web framework
- SQLite database (zero separate database-server setup for local demo)
- Jinja templates, Bootstrap 5 and custom CSS
- `unittest` automated tests
- JSON REST API at `/api/v1`

SQLite is used for easy local setup. It can be migrated to MySQL/MariaDB later. This is still a prototype, not production accounting software.

## Windows setup

### 1. Install Python

Install Python 3.10 or newer from https://www.python.org/downloads/windows/. During installation, select **Add python.exe to PATH**. Close and reopen Command Prompt after installation.

Verify:
```bat
py --version
```

### 2. Extract the project

Extract `Rues_CoreBusiness_ERP_Python.zip` into a folder such as:
`C:\Users\USER\Downloads\Rues_CoreBusiness_ERP_Python`

Open the extracted folder. It should contain `app.py`, `requirements.txt`, `templates`, `static` and `tests`.

### 3. Open Command Prompt in that folder

In File Explorer, open the extracted folder, click the address bar, type `cmd`, and press Enter. The Command Prompt should open at that folder.

### 4. Install dependencies

```bat
py -m venv .venv
.venv\Scripts\activate
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

If PowerShell is being used instead of Command Prompt, activation is:
```powershell
.\.venv\Scripts\Activate.ps1
```
Activation is optional; you can run `py -m pip install -r requirements.txt` without activating, although a virtual environment is recommended.

### 5. Run tests

```bat
py -m unittest discover -s tests -v
```

### 6. Start the application

```bat
py app.py
```

Leave the terminal open and visit http://127.0.0.1:8000 in Chrome.

The SQLite database `rues_corebusiness_erp.db` is created automatically. Fictional Zimbabwean business records are seeded on first start, including realistic invoice references, partial and full payments, and overdue balances. Names, contact details and receipt references are invented for demonstration; they are not real customers or audited accounts. To reset the demo, stop the app, delete the database file, then run `py app.py` again.

## Demonstration workflow

1. Open Customers and create a customer.
2. Create a sales order with one or more line items.
3. Confirm the draft order.
4. Generate an invoice from the confirmed order.
5. Record a partial or full payment.
6. Verify the invoice status and outstanding balance.
7. Open Reports to inspect receivables and export the open invoices as CSV.

## REST API examples

Health check:
```bat
curl http://127.0.0.1:8000/api/v1/health
```
List customers:
```bat
curl -H "Accept: application/json" http://127.0.0.1:8000/api/v1/customers
```
Create a customer:
```bat
curl -X POST http://127.0.0.1:8000/api/v1/customers -H "Content-Type: application/json" -d "{\"name\":\"Example Customer\",\"email\":\"example@example.com\"}"
```
List orders:
```bat
curl http://127.0.0.1:8000/api/v1/orders
```
List invoices:
```bat
curl http://127.0.0.1:8000/api/v1/invoices
```
Summary:
```bat
curl http://127.0.0.1:8000/api/v1/reports/summary
```

API routes currently provided:
- `GET /api/v1/health`
- `GET/POST /api/v1/customers`
- `GET/POST /api/v1/orders`
- `POST /api/v1/orders/<id>/confirm`
- `POST /api/v1/orders/<id>/invoice`
- `GET /api/v1/invoices`
- `POST /api/v1/invoices/<id>/payments`
- `GET /api/v1/reports/summary`

The API is unauthenticated for local demonstration only; do not expose it publicly without authentication, authorisation, rate limiting and security review.

## Current safeguards and limitations

- Order lines are validated and totals are calculated server-side.
- An order must be confirmed before an invoice can be generated.
- Each order can produce at most one invoice.
- Payment amounts must be positive and cannot exceed the current balance.
- Payment and balance updates are grouped in a SQLite write transaction.
- Customer and document references are linked with foreign keys.
- Sample customer names and `.example` email addresses are fictional.
- No authentication, role-based permissions, audit history, tax calculation, bank reconciliation, multi-currency logic, production backup policy or Sage/Odoo integration is included.
- The built-in Flask development server is for local demos only. Do not expose it publicly or enter real financial/customer data.
- Change `RUES_ERP_SECRET_KEY` before any non-local deployment. Add CSRF protection, authentication, authorisation, audit logs, rate limits and production hosting controls before production use.

## Project structure

```text
Rues_CoreBusiness_ERP_Python/
├── app.py
├── requirements.txt
├── README.md
├── templates/
│   ├── layout.html
│   ├── dashboard.html
│   ├── customers/
│   ├── orders/
│   ├── invoices/
│   ├── reports/
│   └── errors/
├── static/css/app.css
└── tests/test_app.py
```

## Interview talking points

- Why the order must be confirmed before invoicing.
- How the server computes line totals and prevents overpayment.
- Why related data uses foreign keys.
- How the REST API supports external consumers.
- Why a prototype must not be described as a production accounting system.
- What would change when migrating from SQLite to MySQL/MariaDB.

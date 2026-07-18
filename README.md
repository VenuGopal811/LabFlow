# 🧪 LabFlow — Pathology Lab Management System (V1)

A role-based, multi-station lab workflow engine built with **Django 5.x**. LabFlow tracks every patient visit from reception desk → payment → sample collection → lab testing → doctor review → PDF report delivery, with a full audit trail and pluggable SMS notifications.

---

## ✨ Key Features

| Feature | Description |
|---|---|
| **Multi-Station Workflow** | Dedicated dashboards for Reception, Chamber (Billing/Approvals), Sample Collection, Lab Technicians, Doctor Review, and **Reporting** (custom layout sorting and finalization) |
| **Dual State Machines** | Independent FSMs for Visit status (11 states, including Pending Reporting and Cancelled) and Test Order status (9 states, including Cancelled) with strictly validated transitions |
| **Role-Based Access Control** | Four user groups (`reception`, `chamber`, `collection`, `lab`) with per-view permission checks and cross-role Reporting access |
| **Search & Filters** | Reception Dashboard supports patient lookup by name, phone, or visit ID, with date-range filters clamped to a **hard 2-year limit** (backed by database indexes) |
| **Pagination** | Efficiently paginates the Reception Dashboard (25 visits per page) |
| **Test Order Cancellation** | Doctors (`chamber` role) can cancel individual test orders with password re-verification and a logged reason; auto-cancels visit if all tests are cancelled |
| **Test Catalog** | Configurable master list of tests with parameter groups, reference ranges, units, pricing, and sample types |
| **Auto-Calculation & Validation** | Dynamic client-side calculations (using `parameter_groups`) to auto-fill totals or missing members, and display sum warnings during lab result entry |
| **PDF Report Generation** | Professional lab reports generated with ReportLab, respecting customized test order layouts and excluding cancelled tests |
| **Token-Gated Report Download** | Patients receive a time-limited (configurable, default 72h) secure URL to download their report — no login needed |
| **SMS Notifications** | Pluggable SMS backend with console (dev) and MSG91 (production) support; auto-sends report links on finalization |
| **Immutable Audit Log** | Every status change, cancellation, result entry/edit, payment confirmation, and SMS dispatch is logged with actor and timestamp |
| **Result Integrity** | Original lab results are preserved when a doctor makes corrections; edits are logged with reason |
| **Retest & Recollection** | Doctor can flag individual tests for retest or recollection, which automatically reverts the visit to the appropriate station |
| **Django Admin Integration** | Full admin site with colored status badges, bulk actions, inline editing, and station-aware views |
| **Seed Data Command** | One command to populate the test catalog (14 common tests) and create demo users for all stations |

---

## 🏗️ Architecture

### Project Structure

```
LabFlow/
├── labflow/               # Django project settings & root URL config
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
├── core/                  # Main application
│   ├── models.py          # Visit, TestOrder, Sample, Payment, AuditLog, TestCatalog
│   ├── views.py           # Station-specific dashboards & workflow views
│   ├── services.py        # Business logic layer (all transitions go through here)
│   ├── forms.py           # Visit registration form with phone validation
│   ├── signals.py         # Safety-net audit logging for direct model saves
│   ├── admin.py           # Customized admin with status badges & bulk actions
│   ├── urls.py            # All front-end URL routes
│   ├── context_processors.py  # Template group context
│   ├── tests.py           # Comprehensive test suite (covers transitions, cancellation, auto-calculation, layout ordering)
│   ├── management/
│   │   └── commands/
│   │       └── seed_data.py   # Seed test catalog & demo users
│   └── migrations/
│       ├── 0001_initial.py
│       ├── 0002_create_groups.py  # Creates RBAC groups via data migration
│       └── ...                    # Subsequent migrations (display order, cancellation, search indexes, parameter groups)
├── reports/               # PDF report generation & token-gated download
│   ├── pdf_generator.py   # ReportLab-based PDF builder (respects display order, filters out cancelled tests)
│   ├── views.py           # Public download endpoint (no auth)
│   └── urls.py
├── notifications/         # Pluggable SMS backend
│   └── sms.py             # ConsoleSMSBackend, MSG91Backend, backend loader
├── templates/             # Django templates (station-specific)
│   ├── base.html          # Base layout
│   ├── login.html
│   ├── reception/         # Dashboard, register, detail, bill print
│   ├── chamber/           # Payment confirmation & visit approval
│   ├── collection/        # Sample collection forms
│   ├── lab/               # Result entry (per-visit, multi-test)
│   ├── doctor/            # Review, approve/edit/retest/recollect
│   └── reporting/         # Reporting dashboard, report layout & finalization
├── static/                # CSS and admin static files
├── requirements.txt
├── manage.py
├── .env.example
└── .gitignore
```

### Data Model

```
┌──────────────┐     1   *  ┌──────────────┐
│    Visit     │───────────▶│  TestOrder   │
│  (patient    │            │  (per-test   │
│   info,      │            │   status,    │
│   status)    │            │   results)   │
└──────┬───────┘            └──────────────┘
       │ 1   *
       ├──────────▶ Sample (container tracking)
       │ 1   1
       ├──────────▶ Payment (method, confirmation)
       │ 1   *
       └──────────▶ AuditLog (immutable trail)

┌──────────────┐
│ TestCatalog  │  ◀── Referenced by TestOrder
│ (master list,│
│  parameters, │
│  pricing)    │
└──────────────┘
```

### Workflow (Visit Lifecycle)

```
 Registered
     │
     ▼
 Payment Pending
     │
     ▼
 Payment Confirmed
     │
     ▼
 Approved by Chamber
     │
     ▼
 Sent to Collection ◄───────────────── Recollection Required
     │                                    ▲
     ▼                                    │
 Sample Collected ◄─── Retest                │
     │              Required              │
     ▼                 ▲                  │
 [Lab enters results]       │                  │
     │                 │                  │
     ▼                 │                  │
 Doctor Reviewed ─────────┴──────────────────┘
     │
     ▼
 Pending Reporting
     │
     ▼
 Report Ready  ──▶  SMS sent  ──▶  Report Delivered

  * Note: Any active visit state can transition to CANCELLED.
```

### Test Order Lifecycle (Independent per test)

```
 Pending → Sample Collected → Testing → Result Entered
     ▲                                       │
     │                            ┌──────────┼──────────┐
     │                            ▼          ▼          ▼
     │                     Doctor Reviewed  Retest    Recollect
     │                            │       Required   Required
     │                            ▼          │          │
 Recollection                Report Ready    │          │
 Required ◄───────────────────────┘          │          │
                                  │  to      │  to      │
                                  │  Testing)│  Pending)│
                                  └──────────┘──────────┘
```

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+**
- **pip** (or any Python package manager)

### 1. Clone the repository

```bash
git clone https://github.com/VenuGopal811/LabFlow.git
cd LabFlow
```

### 2. Create a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```ini
SECRET_KEY=your-random-secret-key
DEBUG=True
```

### 5. Run migrations

```bash
python manage.py migrate
```

### 6. Seed the database

```bash
python manage.py seed_data
```

This creates:
- **14 common lab tests** (CBC, LFT, KFT, Lipid Profile, Thyroid, etc.) with full parameters and reference ranges
- **5 demo users** (see table below)

### 7. Start the development server

```bash
python manage.py runserver
```

Open [http://localhost:8000](http://localhost:8000) to access the login page.

---

## 👤 Demo Users

| Username | Password | Role | Dashboard |
|---|---|---|---|
| `admin` | `admin` | Superuser | Full access (defaults to Reception) |
| `reception` | `reception123` | Reception | `/reception/` |
| `chamber` | `chamber123` | Chamber (Doctor) | `/chamber/` and `/doctor/` |
| `collector` | `collector123` | Collection | `/collection/` |
| `labtech` | `labtech123` | Lab Technician | `/lab/` |

> After login, users are automatically redirected to their role-specific dashboard.

---

## 🔧 Configuration

All settings are configurable via environment variables (`.env` file):

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | dev fallback | Django secret key (**change in production**) |
| `DEBUG` | `True` | Debug mode |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated allowed hosts |
| `SMS_BACKEND` | `notifications.sms.ConsoleSMSBackend` | SMS backend class path |
| `SMS_API_KEY` | — | API key for production SMS provider |
| `SMS_SENDER_ID` | — | Sender ID for SMS provider |
| `REPORT_BASE_URL` | `http://localhost:8000` | Base URL used in SMS report links |
| `REPORT_TOKEN_EXPIRY_HOURS` | `72` | Hours before report download links expire |
| `LAB_NAME` | `LabFlow Diagnostics` | Lab name shown on PDF reports |
| `LAB_ADDRESS` | — | Address shown on PDF report header |
| `LAB_PHONE` | — | Phone shown on PDF report header |

---

## 🧪 Test Catalog (Seeded)

The `seed_data` command populates these tests:

| Code | Test Name | Department | Sample | Price (₹) |
|---|---|---|---|---|
| CBC | Complete Blood Count | Hematology | Blood | 350 |
| LFT | Liver Function Test | Biochemistry | Blood | 500 |
| KFT | Kidney Function Test | Biochemistry | Blood | 450 |
| LIPID | Lipid Profile | Biochemistry | Blood | 400 |
| TFT | Thyroid Profile | Endocrinology | Blood | 600 |
| BSF | Blood Sugar (Fasting) | Biochemistry | Blood | 100 |
| BSPP | Blood Sugar (PP) | Biochemistry | Blood | 100 |
| HBA1C | HbA1c | Biochemistry | Blood | 500 |
| URM | Urine Routine / Microscopy | Pathology | Urine | 150 |
| SEMEN | Semen Analysis | Pathology | Semen | 400 |
| ESR | ESR | Hematology | Blood | 100 |
| VITD | Vitamin D (25-OH) | Biochemistry | Blood | 800 |
| VITB12 | Vitamin B12 | Biochemistry | Blood | 700 |
| WIDAL | Widal Test | Serology | Blood | 250 |

Each test includes full parameter definitions with units and reference ranges. Additional tests can be added via the Django admin panel.

---

## 🧑‍💻 Running Tests

```bash
# Remember to run it within the virtual environment
venv\Scripts\python manage.py test core
```

The test suite covers:
- Visit ID auto-generation (format `LF-YYYYMMDD-NNNN`, sequential ordering)
- Visit status state machine (valid/invalid transitions, audit logging)
- Payment confirmation flow
- Test order state machine (full happy path, retest/recollection cycles)
- Result editing with original value preservation
- Result validation (numeric type checking against reference ranges)
- Visit status transitions to `Pending Reporting` when all tests are ready
- Reporting dashboard access, report layout reordering (`display_order` tracking), and finalization (generates token, sends SMS, moves to `Report Delivered`)
- Test order cancellation by doctor (requires password re-verification, checks reason, auto-cancels visit if all tests are cancelled)
- Report token generation, uniqueness, and expiry validation
- Sample collection triggering visit status transitions
- Recollection and retest reverting visit status to correct stations (e.g. from reporting down to collection or sample collected)
- Phone number validation (10-digit, country code stripping)
- SMS send action (via view endpoint)

---

## 📝 API / URL Reference

### Authentication

| URL | Method | Description |
|---|---|---|
| `/` | GET/POST | Login page |
| `/logout/` | GET | Logout |
| `/dashboard/` | GET | Auto-redirect to role-specific dashboard |

### Reception

| URL | Method | Description |
|---|---|---|
| `/reception/` | GET | Reception dashboard (includes search by name/phone/ID and date filtering, paginated 25 per page) |
| `/reception/register/` | GET/POST | Register a new patient visit |
| `/visit/<id>/` | GET | Visit detail with tests, samples, payment, audit log |
| `/visit/<id>/bill/` | GET | Printable bill view (excludes cancelled test orders) |
| `/visit/<id>/pay-pending/` | POST | Mark visit as payment pending |
| `/visit/<id>/send-sms/` | POST | Manually trigger report SMS |

### Chamber (Billing & Approvals)

| URL | Method | Description |
|---|---|---|
| `/chamber/` | GET | Pending payments & visits awaiting approval |
| `/chamber/confirm-payment/<id>/` | POST | Confirm payment (cash/UPI) |
| `/chamber/approve/<id>/` | POST | Approve visit → auto-send to collection |

### Sample Collection

| URL | Method | Description |
|---|---|---|
| `/collection/` | GET | Collection queue |
| `/collection/collect/<id>/` | GET/POST | Record sample containers |

### Lab Technician

| URL | Method | Description |
|---|---|---|
| `/lab/` | GET | Lab queue (grouped by visit) |
| `/lab/visit/<id>/results/` | GET/POST | Enter results for all tests in a visit (supports auto-calculation and sum warnings via parameter groups) |

### Doctor Review

| URL | Method | Description |
|---|---|---|
| `/doctor/` | GET | Review queue (grouped by visit) |
| `/doctor/visit/<id>/review/` | GET/POST | Review all tests: approve, edit, retest, or recollect |
| `/test-order/cancel/` | POST | Cancel a test order (requires doctor role, password verification, and logs a cancellation reason) |

### Reporting Station

| URL | Method | Description |
|---|---|---|
| `/reporting/` | GET | Reporting queue (visits in `Pending Reporting` status) |
| `/reporting/detail/<id>/` | GET/POST | View/reorder test layouts (using custom `display_order`) and finalize reports |

### Report Download (Public)

| URL | Method | Description |
|---|---|---|
| `/report/<token>/` | GET | Download PDF report (token-gated, no login) |

### Django Admin

| URL | Description |
|---|---|
| `/admin/` | Full admin interface with colored badges, bulk actions, and inline editing |

---

## 🔐 Security Notes

- **Report tokens** are cryptographically random (48 bytes via `secrets.token_urlsafe`) with configurable expiry
- **CSRF protection** is enabled on all POST endpoints
- All status transitions are validated server-side — the service layer rejects invalid state changes
- The audit log is **append-only** — no edits or deletes are permitted (enforced at the admin level)
- Phone numbers are sanitized to 10 digits (country code stripped)
- Django's password validators are enabled (similarity, minimum length, common password, numeric)

---

## 📦 Dependencies

| Package | Purpose |
|---|---|
| `Django >= 5.1, < 6.0` | Web framework |
| `reportlab >= 4.0` | PDF generation |
| `python-dotenv >= 1.0` | Environment variable management |
| `Pillow >= 10.0` | Image processing (Django dependency) |

---

## 🗺️ Roadmap (Post-V1)

- [ ] Patient identity matching across visits
- [ ] Barcode / QR code generation for sample containers
- [ ] Full production SMS provider integration (MSG91 / Twilio)
- [ ] Role-specific admin themes
- [ ] Report template customization (letterhead, signatures)
- [ ] Bulk test result import (CSV / Excel)
- [ ] Dashboard analytics and charting
- [ ] Multi-language support
- [ ] REST API for mobile app integration

---

## 📄 License

This project is private. All rights reserved.

---

<div align="center">

**LabFlow V1** · Built with Django 🐍

</div>

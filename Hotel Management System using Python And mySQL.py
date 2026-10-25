

import os
import re
import sys
import datetime as dt
from contextlib import contextmanager
from tkinter import ttk, messagebox

import bcrypt
import mysql.connector
from mysql.connector import Error as MySQLError

import customtkinter as ctk

# ---------------------------------------------------------------
# CONFIGURATION (override with environment variables or .env)
# ---------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "127.0.0.1"),
    "port":     int(os.getenv("DB_PORT", "3306")),
    "user":     os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "hotel_booking_db"),
    "autocommit": False,
}

# ---------------------------------------------------------------
# THEME
# ---------------------------------------------------------------
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

NAVY   = "#0f1f3d"
NAVY2  = "#16294f"
TEAL   = "#1abc9c"
TEAL_D = "#16a085"
SLATE  = "#64748b"
BG     = "#f4f6fa"
CARD   = "#ffffff"
DANGER = "#e74c3c"
WARN   = "#f39c12"
OK     = "#27ae60"

# ===============================================================
# DATABASE LAYER
# ===============================================================
class Database:
    """Thin wrapper with connection + transaction helpers."""

    @staticmethod
    def connect(with_database=True):
        cfg = dict(DB_CONFIG)
        if not with_database:
            cfg.pop("database", None)
        return mysql.connector.connect(**cfg)

    @classmethod
    def ensure_database(cls):
        """Create database + all tables if they don't exist."""
        # Create database
        try:
            conn = cls.connect(with_database=False)
            cur = conn.cursor()
            cur.execute(
                "CREATE DATABASE IF NOT EXISTS hotel_booking_db "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            conn.commit()
            cur.close()
            conn.close()
        except MySQLError as e:
            raise RuntimeError(f"Cannot connect to MySQL: {e}")

        # Create tables
        ddl = [
            """
            CREATE TABLE IF NOT EXISTS admins (
                admin_id      INT AUTO_INCREMENT PRIMARY KEY,
                username      VARCHAR(50)  NOT NULL UNIQUE,
                password_hash VARCHAR(100) NOT NULL,
                full_name     VARCHAR(100) NOT NULL,
                created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS room_types (
                type_id     INT AUTO_INCREMENT PRIMARY KEY,
                type_name   VARCHAR(50)  NOT NULL UNIQUE,
                description VARCHAR(255) DEFAULT NULL
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS rooms (
                room_id         INT AUTO_INCREMENT PRIMARY KEY,
                room_number     VARCHAR(10)    NOT NULL UNIQUE,
                type_id         INT            NOT NULL,
                price_per_night DECIMAL(10,2)  NOT NULL,
                capacity        INT            NOT NULL DEFAULT 2,
                status          ENUM('Available','Occupied','Maintenance')
                                NOT NULL DEFAULT 'Available',
                is_active       TINYINT(1)     NOT NULL DEFAULT 1,
                CONSTRAINT fk_rooms_type FOREIGN KEY (type_id)
                    REFERENCES room_types(type_id)
                    ON DELETE RESTRICT ON UPDATE CASCADE,
                CONSTRAINT chk_rooms_price CHECK (price_per_night > 0),
                CONSTRAINT chk_rooms_capacity CHECK (capacity > 0)
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS customers (
                customer_id       INT AUTO_INCREMENT PRIMARY KEY,
                full_name         VARCHAR(100) NOT NULL,
                phone             VARCHAR(20)  NOT NULL,
                email             VARCHAR(120) NOT NULL UNIQUE,
                address           VARCHAR(255) DEFAULT NULL,
                registration_date DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                is_active         TINYINT(1) NOT NULL DEFAULT 1
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS bookings (
                booking_id        INT AUTO_INCREMENT PRIMARY KEY,
                customer_id       INT NOT NULL,
                room_id           INT NOT NULL,
                check_in_date     DATE NOT NULL,
                check_out_date    DATE NOT NULL,
                number_of_nights  INT  NOT NULL,
                nightly_rate      DECIMAL(10,2) NOT NULL,
                total_amount      DECIMAL(10,2) NOT NULL,
                status            ENUM('Confirmed','Checked In',
                                       'Checked Out','Cancelled')
                                  NOT NULL DEFAULT 'Confirmed',
                actual_check_in   DATETIME DEFAULT NULL,
                actual_check_out  DATETIME DEFAULT NULL,
                created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_bookings_customer FOREIGN KEY (customer_id)
                    REFERENCES customers(customer_id)
                    ON DELETE RESTRICT ON UPDATE CASCADE,
                CONSTRAINT fk_bookings_room FOREIGN KEY (room_id)
                    REFERENCES rooms(room_id)
                    ON DELETE RESTRICT ON UPDATE CASCADE,
                CONSTRAINT chk_booking_dates CHECK (check_out_date > check_in_date),
                CONSTRAINT chk_booking_amount CHECK (total_amount > 0)
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS payments (
                payment_id     INT AUTO_INCREMENT PRIMARY KEY,
                booking_id     INT NOT NULL,
                amount         DECIMAL(10,2) NOT NULL,
                payment_method ENUM('Cash','Credit/Debit Card','Bank Transfer')
                               NOT NULL,
                payment_date   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_payments_booking FOREIGN KEY (booking_id)
                    REFERENCES bookings(booking_id)
                    ON DELETE RESTRICT ON UPDATE CASCADE,
                CONSTRAINT chk_payments_amount CHECK (amount > 0)
            ) ENGINE=InnoDB
            """,
        ]
        conn = cls.connect()
        cur = conn.cursor()
        try:
            for stmt in ddl:
                cur.execute(stmt)
            # Seed room types (only if empty)
            cur.execute("SELECT COUNT(*) FROM room_types")
            if cur.fetchone()[0] == 0:
                cur.executemany(
                    "INSERT INTO room_types (type_name, description) VALUES (%s,%s)",
                    [
                        ("Single", "One twin bed, ideal for solo travelers"),
                        ("Double", "One queen bed, comfortable for two guests"),
                        ("Deluxe", "King bed, city view, premium amenities"),
                        ("Suite",  "Separate living area, luxury furnishings"),
                        ("Family", "Two double beds, extra space for families"),
                    ],
                )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    @classmethod
    @contextmanager
    def cursor(cls):
        """Yields (cursor, conn) and handles commit/rollback/close."""
        conn = cls.connect()
        cur = conn.cursor()
        try:
            yield cur, conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()


def fetch_all(sql, params=()):
    with Database.cursor() as (cur, _):
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(sql, params=()):
    with Database.cursor() as (cur, _):
        cur.execute(sql, params)
        return cur.fetchone()


def execute(sql, params=()):
    with Database.cursor() as (cur, _):
        cur.execute(sql, params)
        return cur.lastrowid


# ===============================================================
# VALIDATORS
# ===============================================================
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^[0-9+\-\s]{7,20}$")


def valid_email(s):
    return bool(EMAIL_RE.match(s.strip()))


def valid_phone(s):
    return bool(PHONE_RE.match(s.strip()))


def parse_date(s):
    return dt.datetime.strptime(s.strip(), "%Y-%m-%d").date()


# ===============================================================
# HELPERS
# ===============================================================
def money(v):
    return f"${float(v):,.2f}"


def info(title, msg):
    messagebox.showinfo(title, msg)


def error(title, msg):
    messagebox.showerror(title, msg)


def confirm(title, msg):
    return messagebox.askyesno(title, msg)


# ===============================================================
# MAIN APPLICATION
# ===============================================================
class HotelApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Hotel Booking Management System")
        self.geometry("1280x760")
        self.minsize(1180, 700)
        self.configure(fg_color=BG)

        self.admin = None  # dict when logged in

        try:
            Database.ensure_database()
        except RuntimeError as e:
            messagebox.showerror("Database Error", str(e))
            self.destroy()
            sys.exit(1)

        self.current_page = None
        self._build_shell()

        if self._admin_count() == 0:
            self._show_setup_wizard()
        else:
            self._show_login()

    # -------- shell --------
    def _build_shell(self):
        for w in self.winfo_children():
            w.destroy()
        self.sidebar = None
        self.content = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.content.pack(fill="both", expand=True)

    def _admin_count(self):
        return fetch_one("SELECT COUNT(*) FROM admins")[0]

    # -------- setup wizard --------
    def _show_setup_wizard(self):
        for w in self.content.winfo_children():
            w.destroy()
        wrap = ctk.CTkFrame(self.content, fg_color=CARD, corner_radius=16)
        wrap.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(wrap, text="Initial Administrator Setup",
                     font=("Segoe UI", 22, "bold"),
                     text_color=NAVY).pack(padx=40, pady=(28, 6))
        ctk.CTkLabel(wrap, text="Create the first admin account for this system.",
                     text_color=SLATE).pack(padx=40, pady=(0, 18))

        self._setup_entries = {}
        for label, key, show in [
            ("Full Name", "full_name", None),
            ("Username", "username", None),
            ("Password", "password", "*"),
            ("Confirm Password", "password2", "*"),
        ]:
            ctk.CTkLabel(wrap, text=label, anchor="w",
                         text_color=NAVY).pack(padx=40, fill="x")
            e = ctk.CTkEntry(wrap, width=360, show=show)
            e.pack(padx=40, pady=(2, 10))
            self._setup_entries[key] = e

        ctk.CTkButton(wrap, text="Create Administrator",
                      fg_color=TEAL, hover_color=TEAL_D,
                      command=self._do_setup).pack(padx=40, pady=(4, 24),
                                                    fill="x")

    def _do_setup(self):
        e = {k: v.get().strip() for k, v in self._setup_entries.items()}
        if not all(e.values()):
            return error("Missing", "All fields are required.")
        if len(e["password"]) < 6:
            return error("Weak Password", "Password must be at least 6 characters.")
        if e["password"] != e["password2"]:
            return error("Mismatch", "Passwords do not match.")
        hashed = bcrypt.hashpw(e["password"].encode(), bcrypt.gensalt()).decode()
        try:
            execute("INSERT INTO admins (username,password_hash,full_name) "
                    "VALUES (%s,%s,%s)",
                    (e["username"], hashed, e["full_name"]))
        except MySQLError as ex:
            return error("DB Error", str(ex))
        info("Success", "Administrator created. Please log in.")
        self._show_login()

    # -------- login --------
    def _show_login(self):
        for w in self.content.winfo_children():
            w.destroy()
        wrap = ctk.CTkFrame(self.content, fg_color=CARD, corner_radius=16)
        wrap.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(wrap, text="Hotel Admin Login",
                     font=("Segoe UI", 24, "bold"),
                     text_color=NAVY).pack(padx=50, pady=(30, 6))
        ctk.CTkLabel(wrap, text="Sign in to the management dashboard",
                     text_color=SLATE).pack(padx=50, pady=(0, 18))

        ctk.CTkLabel(wrap, text="Username", anchor="w",
                     text_color=NAVY).pack(padx=50, fill="x")
        self.login_user = ctk.CTkEntry(wrap, width=340)
        self.login_user.pack(padx=50, pady=(2, 12))

        ctk.CTkLabel(wrap, text="Password", anchor="w",
                     text_color=NAVY).pack(padx=50, fill="x")
        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(padx=50, pady=(2, 16), fill="x")
        self.login_pass = ctk.CTkEntry(row, width=280, show="*")
        self.login_pass.pack(side="left")
        self._show_pw = False

        def toggle():
            self._show_pw = not self._show_pw
            self.login_pass.configure(show="" if self._show_pw else "*")
            btn.configure(text="Hide" if self._show_pw else "Show")

        btn = ctk.CTkButton(row, text="Show", width=50, fg_color=SLATE,
                            hover_color=NAVY2, command=toggle)
        btn.pack(side="left", padx=(8, 0))

        ctk.CTkButton(wrap, text="Login", fg_color=TEAL, hover_color=TEAL_D,
                      command=self._do_login).pack(padx=50, pady=(0, 26),
                                                    fill="x")

        self.login_pass.bind("<Return>", lambda _e: self._do_login())
        self.login_user.focus_set()

    def _do_login(self):
        u = self.login_user.get().strip()
        p = self.login_pass.get()
        if not u or not p:
            return error("Missing", "Enter username and password.")
        row = fetch_one(
            "SELECT admin_id, username, password_hash, full_name "
            "FROM admins WHERE username=%s", (u,))
        if not row:
            return error("Login Failed", "Invalid username or password.")
        if not bcrypt.checkpw(p.encode(), row[2].encode()):
            return error("Login Failed", "Invalid username or password.")
        self.admin = {"id": row[0], "username": row[1], "name": row[3]}
        self._build_main_shell()
        self.show_page("dashboard")

    # -------- main shell --------
    def _build_main_shell(self):
        for w in self.content.winfo_children():
            w.destroy()

        self.sidebar = ctk.CTkFrame(self.content, fg_color=NAVY,
                                    corner_radius=0, width=220)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        ctk.CTkLabel(self.sidebar, text="HOTEL\nADMIN",
                     font=("Segoe UI", 20, "bold"),
                     text_color="white").pack(pady=(26, 6))
        ctk.CTkLabel(self.sidebar, text=self.admin["name"],
                     text_color=TEAL).pack(pady=(0, 20))

        self.main_area = ctk.CTkFrame(self.content, fg_color=BG,
                                      corner_radius=0)
        self.main_area.pack(side="left", fill="both", expand=True)

        items = [
            ("Dashboard",           "dashboard"),
            ("Rooms",               "rooms"),
            ("Customers",           "customers"),
            ("Reservations",        "bookings"),
            ("Check-In / Check-Out","checkin"),
            ("Payments",            "payments"),
            ("Reports",             "reports"),
        ]
        for label, key in items:
            ctk.CTkButton(
                self.sidebar, text=label, anchor="w",
                fg_color="transparent", hover_color=NAVY2,
                text_color="white", font=("Segoe UI", 13),
                command=lambda k=key: self.show_page(k),
            ).pack(fill="x", padx=12, pady=3)

        ctk.CTkButton(
            self.sidebar, text="Logout", anchor="w",
            fg_color="transparent", hover_color=DANGER,
            text_color="#fbb", font=("Segoe UI", 13),
            command=self._logout,
        ).pack(fill="x", padx=12, pady=(20, 20), side="bottom")

    def _logout(self):
        if confirm("Logout", "Are you sure you want to log out?"):
            self.admin = None
            self._build_shell()
            self._show_login()

    # -------- page router --------
    def show_page(self, key):
        for w in self.main_area.winfo_children():
            w.destroy()
        self.current_page = key
        getattr(self, f"page_{key}")()

    # -------- helpers --------
    def page_title(self, text, subtitle=""):
        ctk.CTkLabel(self.main_area, text=text,
                     font=("Segoe UI", 26, "bold"),
                     text_color=NAVY, anchor="w").pack(
            fill="x", padx=28, pady=(22, 0))
        if subtitle:
            ctk.CTkLabel(self.main_area, text=subtitle, text_color=SLATE,
                         anchor="w").pack(fill="x", padx=28, pady=(0, 12))
        else:
            ctk.CTkFrame(self.main_area, height=6,
                         fg_color="transparent").pack(pady=6)

    def tree(self, parent, cols, widths):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Hotel.Treeview",
                        rowheight=28,
                        font=("Segoe UI", 10),
                        background="white", fieldbackground="white",
                        bordercolor="#e2e8f0")
        style.configure("Hotel.Treeview.Heading",
                        font=("Segoe UI", 10, "bold"),
                        background=NAVY, foreground="white")
        style.map("Hotel.Treeview.Heading",
                  background=[("active", NAVY2)])
        container = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=10)
        container.pack(fill="both", expand=True, padx=24, pady=(4, 18))
        tv = ttk.Treeview(container, columns=cols, show="headings",
                          style="Hotel.Treeview")
        for c, w_ in zip(cols, widths):
            tv.heading(c, text=c)
            tv.column(c, width=w_, anchor="w")
        vsb = ttk.Scrollbar(container, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vsb.set)
        tv.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        vsb.pack(side="right", fill="y", padx=(0, 8), pady=8)
        return tv

    def load_tree(self, tv, rows):
        for i in tv.get_children():
            tv.delete(i)
        for r in rows:
            tv.insert("", "end", values=r)

    # =====================================================
    # DASHBOARD
    # =====================================================
    def page_dashboard(self):
        self.page_title("Dashboard", "Live statistics and recent bookings")

        stats = fetch_one("""
            SELECT
              (SELECT COUNT(*) FROM rooms WHERE is_active=1),
              (SELECT COUNT(*) FROM rooms WHERE is_active=1
                 AND status='Available'),
              (SELECT COUNT(*) FROM rooms WHERE is_active=1
                 AND status='Occupied'),
              (SELECT COUNT(*) FROM customers WHERE is_active=1),
              (SELECT COUNT(*) FROM bookings
                 WHERE status IN ('Confirmed','Checked In')),
              (SELECT COUNT(*) FROM bookings
                 WHERE check_in_date=CURDATE()
                   AND status='Confirmed'),
              (SELECT COUNT(*) FROM bookings
                 WHERE check_out_date=CURDATE()
                   AND status='Checked In'),
              (SELECT COALESCE(SUM(amount),0) FROM payments)
        """)
        cards = [
            ("Total Rooms",       stats[0], NAVY),
            ("Available",         stats[1], OK),
            ("Occupied",          stats[2], WARN),
            ("Customers",         stats[3], TEAL),
            ("Active Bookings",   stats[4], NAVY2),
            ("Today's Check-ins", stats[5], OK),
            ("Today's Check-outs",stats[6], WARN),
            ("Total Revenue",     money(stats[7]), TEAL_D),
        ]
        grid = ctk.CTkFrame(self.main_area, fg_color="transparent")
        grid.pack(fill="x", padx=22, pady=(0, 10))
        for i in range(4):
            grid.grid_columnconfigure(i, weight=1)
        for idx, (label, val, color) in enumerate(cards):
            card = ctk.CTkFrame(grid, fg_color=CARD, corner_radius=12,
                                height=90)
            card.grid(row=idx // 4, column=idx % 4,
                      padx=6, pady=6, sticky="nsew")
            card.grid_propagate(False)
            ctk.CTkLabel(card, text=label, text_color=SLATE,
                         font=("Segoe UI", 11)).pack(
                anchor="w", padx=14, pady=(12, 0))
            ctk.CTkLabel(card, text=str(val), text_color=color,
                         font=("Segoe UI", 20, "bold")).pack(
                anchor="w", padx=14, pady=(0, 12))

        ctk.CTkLabel(self.main_area, text="Recent Bookings",
                     font=("Segoe UI", 15, "bold"),
                     text_color=NAVY, anchor="w").pack(
            fill="x", padx=28, pady=(6, 0))

        tv = self.tree(self.main_area,
                       ("ID", "Customer", "Room", "Check-in", "Check-out",
                        "Nights", "Total", "Status"),
                       (50, 170, 80, 100, 100, 60, 90, 110))
        rows = fetch_all("""
            SELECT b.booking_id, c.full_name, r.room_number,
                   b.check_in_date, b.check_out_date, b.number_of_nights,
                   b.total_amount, b.status
            FROM bookings b
            JOIN customers c ON c.customer_id = b.customer_id
            JOIN rooms r ON r.room_id = b.room_id
            ORDER BY b.booking_id DESC
            LIMIT 15
        """)
        self.load_tree(tv, [tuple(r) for r in rows])

    # =====================================================
    # ROOMS
    # =====================================================
    def page_rooms(self):
        self.page_title("Room Management", "Add, edit, deactivate rooms")

        bar = ctk.CTkFrame(self.main_area, fg_color="transparent")
        bar.pack(fill="x", padx=24, pady=(0, 8))

        self.room_search = ctk.CTkEntry(bar, placeholder_text="Search room #",
                                        width=220)
        self.room_search.pack(side="left", padx=(0, 8))
        self.room_search.bind("<KeyRelease>", lambda _e: self._rooms_reload())

        self.room_filter_type = ctk.CTkComboBox(
            bar, values=["All Types"], width=140,
            command=lambda _v: self._rooms_reload())
        self.room_filter_type.pack(side="left", padx=(0, 8))

        self.room_filter_status = ctk.CTkComboBox(
            bar, values=["All Status", "Available", "Occupied",
                         "Maintenance"], width=140,
            command=lambda _v: self._rooms_reload())
        self.room_filter_status.pack(side="left", padx=(0, 8))

        ctk.CTkButton(bar, text="+ Add Room", fg_color=TEAL,
                      hover_color=TEAL_D,
                      command=self._room_add).pack(side="right", padx=2)
        ctk.CTkButton(bar, text="Deactivate", fg_color=DANGER,
                      hover_color="#c0392b",
                      command=self._room_deactivate).pack(side="right",
                                                          padx=2)
        ctk.CTkButton(bar, text="Edit", fg_color=NAVY,
                      hover_color=NAVY2,
                      command=self._room_edit).pack(side="right", padx=2)

        # populate type filter
        types = fetch_all("SELECT type_name FROM room_types ORDER BY type_id")
        self.room_filter_type.configure(
            values=["All Types"] + [t[0] for t in types])
        self.room_filter_type.set("All Types")
        self.room_filter_status.set("All Status")

        self.rooms_tv = self.tree(
            self.main_area,
            ("ID", "Number", "Type", "Price/Night", "Capacity",
             "Status", "Active"),
            (50, 90, 110, 110, 80, 110, 70))
        self._rooms_reload()

    def _rooms_reload(self):
        q = "SELECT r.room_id, r.room_number, rt.type_name, r.price_per_night, " \
            "r.capacity, r.status, r.is_active " \
            "FROM rooms r JOIN room_types rt ON rt.type_id = r.type_id WHERE 1=1"
        params = []
        s = self.room_search.get().strip()
        if s:
            q += " AND r.room_number LIKE %s"
            params.append(f"%{s}%")
        t = self.room_filter_type.get()
        if t and t != "All Types":
            q += " AND rt.type_name = %s"
            params.append(t)
        st = self.room_filter_status.get()
        if st and st != "All Status":
            q += " AND r.status = %s"
            params.append(st)
        q += " ORDER BY r.room_number"
        rows = fetch_all(q, tuple(params))
        self.load_tree(self.rooms_tv,
                       [(r[0], r[1], r[2], money(r[3]), r[4], r[5],
                         "Yes" if r[6] else "No") for r in rows])

    def _selected_id(self, tv, col=0):
        sel = tv.selection()
        if not sel:
            error("No Selection", "Please select a row first.")
            return None
        return tv.item(sel[0], "values")[col]

    def _room_add(self):
        RoomDialog(self, on_save=self._rooms_reload)

    def _room_edit(self):
        rid = self._selected_id(self.rooms_tv)
        if rid is None:
            return
        RoomDialog(self, room_id=int(rid), on_save=self._rooms_reload)

    def _room_deactivate(self):
        rid = self._selected_id(self.rooms_tv)
        if rid is None:
            return
        if not confirm("Deactivate Room",
                       "Mark this room inactive? Historical bookings remain."):
            return
        try:
            execute("UPDATE rooms SET is_active=0, status='Maintenance' "
                    "WHERE room_id=%s", (int(rid),))
            self._rooms_reload()
        except MySQLError as e:
            error("DB Error", str(e))

    # =====================================================
    # CUSTOMERS
    # =====================================================
    def page_customers(self):
        self.page_title("Customer Management", "Manage hotel customers")

        bar = ctk.CTkFrame(self.main_area, fg_color="transparent")
        bar.pack(fill="x", padx=24, pady=(0, 8))
        self.cust_search = ctk.CTkEntry(bar, placeholder_text="Search name/email",
                                        width=260)
        self.cust_search.pack(side="left", padx=(0, 8))
        self.cust_search.bind("<KeyRelease>", lambda _e: self._cust_reload())
        ctk.CTkButton(bar, text="+ Add Customer", fg_color=TEAL,
                      hover_color=TEAL_D,
                      command=self._cust_add).pack(side="right", padx=2)
        ctk.CTkButton(bar, text="Delete", fg_color=DANGER,
                      hover_color="#c0392b",
                      command=self._cust_delete).pack(side="right", padx=2)
        ctk.CTkButton(bar, text="Edit", fg_color=NAVY, hover_color=NAVY2,
                      command=self._cust_edit).pack(side="right", padx=2)
        ctk.CTkButton(bar, text="History", fg_color=SLATE,
                      hover_color=NAVY2,
                      command=self._cust_history).pack(side="right", padx=2)

        self.cust_tv = self.tree(
            self.main_area,
            ("ID", "Name", "Phone", "Email", "Address", "Registered"),
            (50, 160, 110, 200, 220, 130))
        self._cust_reload()

    def _cust_reload(self):
        s = self.cust_search.get().strip()
        q = ("SELECT customer_id, full_name, phone, email, "
             "COALESCE(address,''), registration_date "
             "FROM customers WHERE is_active=1")
        params = []
        if s:
            q += " AND (full_name LIKE %s OR email LIKE %s OR phone LIKE %s)"
            params += [f"%{s}%"] * 3
        q += " ORDER BY customer_id DESC"
        rows = fetch_all(q, tuple(params))
        self.load_tree(self.cust_tv,
                       [(r[0], r[1], r[2], r[3], r[4],
                         r[5].strftime("%Y-%m-%d") if r[5] else "") for r in rows])

    def _cust_add(self):
        CustomerDialog(self, on_save=self._cust_reload)

    def _cust_edit(self):
        cid = self._selected_id(self.cust_tv)
        if cid is None:
            return
        CustomerDialog(self, customer_id=int(cid), on_save=self._cust_reload)

    def _cust_delete(self):
        cid = self._selected_id(self.cust_tv)
        if cid is None:
            return
        n = fetch_one("SELECT COUNT(*) FROM bookings WHERE customer_id=%s",
                      (int(cid),))[0]
        if n:
            if confirm("History Found",
                       "This customer has bookings. Deactivate (hide) instead?"):
                execute("UPDATE customers SET is_active=0 WHERE customer_id=%s",
                        (int(cid),))
                self._cust_reload()
            return
        if confirm("Delete", "Permanently delete this customer?"):
            try:
                execute("DELETE FROM customers WHERE customer_id=%s",
                        (int(cid),))
                self._cust_reload()
            except MySQLError as e:
                error("DB Error", str(e))

    def _cust_history(self):
        cid = self._selected_id(self.cust_tv)
        if cid is None:
            return
        rows = fetch_all("""
            SELECT b.booking_id, r.room_number, b.check_in_date,
                   b.check_out_date, b.total_amount, b.status
            FROM bookings b JOIN rooms r ON r.room_id = b.room_id
            WHERE b.customer_id=%s ORDER BY b.check_in_date DESC
        """, (int(cid),))
        if not rows:
            return info("History", "No bookings for this customer.")
        msg = "\n".join(
            f"#{r[0]}  Room {r[1]}  {r[2]}→{r[3]}  {money(r[4])}  {r[5]}"
            for r in rows)
        info("Booking History", msg)

    # =====================================================
    # BOOKINGS
    # =====================================================
    def page_bookings(self):
        self.page_title("Reservations", "Create and manage bookings")

        bar = ctk.CTkFrame(self.main_area, fg_color="transparent")
        bar.pack(fill="x", padx=24, pady=(0, 8))
        self.bk_search = ctk.CTkEntry(bar, placeholder_text="Search customer/room",
                                      width=240)
        self.bk_search.pack(side="left", padx=(0, 8))
        self.bk_search.bind("<KeyRelease>", lambda _e: self._bk_reload())

        self.bk_status = ctk.CTkComboBox(
            bar, values=["All Status", "Confirmed", "Checked In",
                         "Checked Out", "Cancelled"],
            width=140, command=lambda _v: self._bk_reload())
        self.bk_status.set("All Status")
        self.bk_status.pack(side="left", padx=(0, 8))

        ctk.CTkButton(bar, text="+ New Booking", fg_color=TEAL,
                      hover_color=TEAL_D,
                      command=self._bk_new).pack(side="right", padx=2)
        ctk.CTkButton(bar, text="Cancel Booking", fg_color=DANGER,
                      hover_color="#c0392b",
                      command=self._bk_cancel).pack(side="right", padx=2)

        self.bk_tv = self.tree(
            self.main_area,
            ("ID", "Customer", "Room", "Check-in", "Check-out",
             "Nights", "Rate", "Total", "Status"),
            (50, 150, 70, 100, 100, 60, 80, 100, 100))
        self._bk_reload()

    def _bk_reload(self):
        s = self.bk_search.get().strip()
        q = """
            SELECT b.booking_id, c.full_name, r.room_number,
                   b.check_in_date, b.check_out_date, b.number_of_nights,
                   b.nightly_rate, b.total_amount, b.status
            FROM bookings b
            JOIN customers c ON c.customer_id = b.customer_id
            JOIN rooms r ON r.room_id = b.room_id
            WHERE 1=1
        """
        params = []
        if s:
            q += " AND (c.full_name LIKE %s OR r.room_number LIKE %s)"
            params += [f"%{s}%"] * 2
        st = self.bk_status.get()
        if st and st != "All Status":
            q += " AND b.status = %s"
            params.append(st)
        q += " ORDER BY b.booking_id DESC"
        rows = fetch_all(q, tuple(params))
        self.load_tree(self.bk_tv,
                       [(r[0], r[1], r[2], r[3], r[4], r[5],
                         money(r[6]), money(r[7]), r[8]) for r in rows])

    def _bk_new(self):
        BookingDialog(self, on_save=self._bk_reload)

    def _bk_cancel(self):
        bid = self._selected_id(self.bk_tv)
        if bid is None:
            return
        row = fetch_one("SELECT status FROM bookings WHERE booking_id=%s",
                        (int(bid),))
        if row and row[0] in ("Checked In", "Checked Out"):
            return error("Cannot Cancel",
                         "Only Confirmed bookings can be cancelled.")
        if not confirm("Cancel Booking",
                       "Cancel this reservation? It will not block "
                       "availability anymore."):
            return
        execute("UPDATE bookings SET status='Cancelled' WHERE booking_id=%s",
                (int(bid),))
        self._bk_reload()

    # =====================================================
    # CHECK-IN / CHECK-OUT
    # =====================================================
    def page_checkin(self):
        self.page_title("Check-In / Check-Out",
                        "Update reservation states on arrival/departure")

        bar = ctk.CTkFrame(self.main_area, fg_color="transparent")
        bar.pack(fill="x", padx=24, pady=(0, 8))
        ctk.CTkButton(bar, text="Check-In Selected", fg_color=TEAL,
                      hover_color=TEAL_D,
                      command=self._do_checkin).pack(side="left", padx=2)
        ctk.CTkButton(bar, text="Check-Out Selected", fg_color=NAVY,
                      hover_color=NAVY2,
                      command=self._do_checkout).pack(side="left", padx=2)
        ctk.CTkButton(bar, text="Refresh", fg_color=SLATE,
                      hover_color=NAVY2,
                      command=self._ci_reload).pack(side="right", padx=2)

        self.ci_tv = self.tree(
            self.main_area,
            ("ID", "Customer", "Room", "Check-in", "Check-out",
             "Status", "Actual In", "Actual Out"),
            (50, 150, 70, 100, 100, 110, 130, 130))
        self._ci_reload()

    def _ci_reload(self):
        rows = fetch_all("""
            SELECT b.booking_id, c.full_name, r.room_number,
                   b.check_in_date, b.check_out_date, b.status,
                   COALESCE(DATE_FORMAT(b.actual_check_in,'%Y-%m-%d %H:%i'),''),
                   COALESCE(DATE_FORMAT(b.actual_check_out,'%Y-%m-%d %H:%i'),'')
            FROM bookings b
            JOIN customers c ON c.customer_id = b.customer_id
            JOIN rooms r ON r.room_id = b.room_id
            WHERE b.status IN ('Confirmed','Checked In')
            ORDER BY b.check_in_date
        """)
        self.load_tree(self.ci_tv, [tuple(r) for r in rows])

    def _do_checkin(self):
        bid = self._selected_id(self.ci_tv)
        if bid is None:
            return
        try:
            with Database.cursor() as (cur, _):
                cur.execute("SELECT status FROM bookings WHERE booking_id=%s "
                            "FOR UPDATE", (int(bid),))
                status = cur.fetchone()[0]
                if status != "Confirmed":
                    error("Invalid", "Only Confirmed bookings can be checked in.")
                    return
                cur.execute("UPDATE bookings SET status='Checked In', "
                            "actual_check_in=NOW() WHERE booking_id=%s",
                            (int(bid),))
                cur.execute("SELECT room_id FROM bookings WHERE booking_id=%s",
                            (int(bid),))
                room_id = cur.fetchone()[0]
                cur.execute("UPDATE rooms SET status='Occupied' "
                            "WHERE room_id=%s", (room_id,))
            info("Success", "Guest checked in.")
            self._ci_reload()
        except MySQLError as e:
            error("DB Error", str(e))

    def _do_checkout(self):
        bid = self._selected_id(self.ci_tv)
        if bid is None:
            return
        try:
            with Database.cursor() as (cur, _):
                cur.execute("SELECT status FROM bookings WHERE booking_id=%s "
                            "FOR UPDATE", (int(bid),))
                status = cur.fetchone()[0]
                if status != "Checked In":
                    error("Invalid", "Only Checked-In bookings can be checked out.")
                    return
                cur.execute("UPDATE bookings SET status='Checked Out', "
                            "actual_check_out=NOW() WHERE booking_id=%s",
                            (int(bid),))
                cur.execute("SELECT room_id FROM bookings WHERE booking_id=%s",
                            (int(bid),))
                room_id = cur.fetchone()[0]
                cur.execute("UPDATE rooms SET status='Available' "
                            "WHERE room_id=%s", (room_id,))
            info("Success", "Guest checked out.")
            self._ci_reload()
        except MySQLError as e:
            error("DB Error", str(e))

    # =====================================================
    # PAYMENTS
    # =====================================================
    def page_payments(self):
        self.page_title("Payments", "Record payments and view balances")

        bar = ctk.CTkFrame(self.main_area, fg_color="transparent")
        bar.pack(fill="x", padx=24, pady=(0, 8))
        ctk.CTkButton(bar, text="+ Record Payment", fg_color=TEAL,
                      hover_color=TEAL_D,
                      command=self._pay_new).pack(side="left", padx=2)
        ctk.CTkButton(bar, text="View Invoice", fg_color=NAVY,
                      hover_color=NAVY2,
                      command=self._pay_invoice).pack(side="left", padx=2)
        ctk.CTkButton(bar, text="Refresh", fg_color=SLATE,
                      hover_color=NAVY2,
                      command=self._pay_reload).pack(side="right", padx=2)

        self.pay_tv = self.tree(
            self.main_area,
            ("Payment ID", "Booking", "Customer", "Amount", "Method",
             "Paid At", "Booking Total", "Paid", "Balance"),
            (80, 70, 140, 90, 130, 140, 110, 90, 90))
        self._pay_reload()

    def _pay_reload(self):
        rows = fetch_all("""
            SELECT p.payment_id, b.booking_id, c.full_name, p.amount,
                   p.payment_method,
                   DATE_FORMAT(p.payment_date,'%Y-%m-%d %H:%i'),
                   b.total_amount,
                   (SELECT COALESCE(SUM(amount),0) FROM payments
                     WHERE booking_id=b.booking_id) AS paid,
                   b.total_amount -
                   (SELECT COALESCE(SUM(amount),0) FROM payments
                     WHERE booking_id=b.booking_id) AS balance
            FROM payments p
            JOIN bookings b ON b.booking_id = p.booking_id
            JOIN customers c ON c.customer_id = b.customer_id
            ORDER BY p.payment_id DESC
        """)
        self.load_tree(self.pay_tv,
                       [(r[0], r[1], r[2], money(r[3]), r[4], r[5],
                         money(r[6]), money(r[7]), money(r[8])) for r in rows])

    def _pay_new(self):
        PaymentDialog(self, on_save=self._pay_reload)

    def _pay_invoice(self):
        pid = self._selected_id(self.pay_tv)
        if pid is None:
            return
        row = fetch_one("""
            SELECT b.booking_id, c.full_name, r.room_number,
                   b.check_in_date, b.check_out_date, b.number_of_nights,
                   b.nightly_rate, b.total_amount,
                   (SELECT COALESCE(SUM(amount),0) FROM payments
                    WHERE booking_id=b.booking_id) AS paid
            FROM payments p
            JOIN bookings b ON b.booking_id = p.booking_id
            JOIN customers c ON c.customer_id = b.customer_id
            JOIN rooms r ON r.room_id = b.room_id
            WHERE p.payment_id=%s
        """, (int(pid),))
        if not row:
            return
        text = (
            f"INVOICE — Booking #{row[0]}\n\n"
            f"Customer:   {row[1]}\n"
            f"Room:       {row[2]}\n"
            f"Check-in:   {row[3]}\n"
            f"Check-out:  {row[4]}\n"
            f"Nights:     {row[5]}\n"
            f"Rate:       {money(row[6])}\n"
            f"Total:      {money(row[7])}\n"
            f"Paid:       {money(row[8])}\n"
            f"Balance:    {money(float(row[7]) - float(row[8]))}\n"
        )
        info("Invoice", text)

    # =====================================================
    # REPORTS
    # =====================================================
    def page_reports(self):
        self.page_title("Reports", "Live SQL-powered business reports")

        tabs = ctk.CTkTabview(self.main_area, fg_color=CARD,
                              segmented_button_selected_color=NAVY,
                              segmented_button_selected_hover_color=NAVY2)
        tabs.pack(fill="both", expand=True, padx=22, pady=(4, 16))

        t1 = tabs.add("Reservations (JOIN)")
        t2 = tabs.add("Occupancy")
        t3 = tabs.add("Monthly Revenue")
        t4 = tabs.add("Top Rooms")
        t5 = tabs.add("Payments")

        tv1 = self.tree(t1, ("Booking", "Customer", "Room", "Type",
                             "Dates", "Total", "Status"),
                        (70, 160, 70, 100, 200, 90, 110))
        rows = fetch_all("""
            SELECT b.booking_id, c.full_name, r.room_number, rt.type_name,
                   CONCAT(b.check_in_date,' → ',b.check_out_date),
                   b.total_amount, b.status
            FROM bookings b
            JOIN customers c ON c.customer_id = b.customer_id
            JOIN rooms r ON r.room_id = b.room_id
            JOIN room_types rt ON rt.type_id = r.type_id
            ORDER BY b.booking_id DESC
        """)
        self.load_tree(tv1, [(r[0], r[1], r[2], r[3], r[4],
                              money(r[5]), r[6]) for r in rows])

        tv2 = self.tree(t2, ("Room", "Type", "Status", "Bookings"),
                        (90, 120, 120, 100))
        rows = fetch_all("""
            SELECT r.room_number, rt.type_name, r.status,
                   COUNT(b.booking_id)
            FROM rooms r
            JOIN room_types rt ON rt.type_id = r.type_id
            LEFT JOIN bookings b ON b.room_id = r.room_id
                AND b.status <> 'Cancelled'
            GROUP BY r.room_id, r.room_number, rt.type_name, r.status
            ORDER BY COUNT(b.booking_id) DESC
        """)
        self.load_tree(tv2, [tuple(r) for r in rows])

        tv3 = self.tree(t3, ("Month", "Payments", "Revenue"),
                        (140, 120, 140))
        rows = fetch_all("""
            SELECT DATE_FORMAT(payment_date,'%Y-%m') AS m,
                   COUNT(*), SUM(amount)
            FROM payments
            GROUP BY m
            HAVING SUM(amount) > 0
            ORDER BY m DESC
        """)
        self.load_tree(tv3, [(r[0], r[1], money(r[2])) for r in rows])

        tv4 = self.tree(t4, ("Room", "Type", "Bookings"),
                        (100, 140, 100))
        rows = fetch_all("""
            SELECT r.room_number, rt.type_name, COUNT(b.booking_id) AS c
            FROM bookings b
            JOIN rooms r ON r.room_id = b.room_id
            JOIN room_types rt ON rt.type_id = r.type_id
            WHERE b.status <> 'Cancelled'
            GROUP BY r.room_id, r.room_number, rt.type_name
            ORDER BY c DESC, r.room_number
            LIMIT 10
        """)
        self.load_tree(tv4, [tuple(r) for r in rows])

        tv5 = self.tree(t5, ("Payment", "Customer", "Booking", "Method",
                             "Amount", "Date"),
                        (80, 160, 80, 140, 100, 140))
        rows = fetch_all("""
            SELECT p.payment_id, c.full_name, p.booking_id,
                   p.payment_method, p.amount,
                   DATE_FORMAT(p.payment_date,'%Y-%m-%d %H:%i')
            FROM payments p
            JOIN bookings b ON b.booking_id = p.booking_id
            JOIN customers c ON c.customer_id = b.customer_id
            ORDER BY p.payment_id DESC
        """)
        self.load_tree(tv5, [(r[0], r[1], r[2], r[3], money(r[4]), r[5])
                             for r in rows])


# ===============================================================
# MODAL DIALOGS
# ===============================================================
class BaseDialog(ctk.CTkToplevel):
    def __init__(self, parent, title, width=460, height=520):
        super().__init__(parent)
        self.title(title)
        self.geometry(f"{width}x{height}")
        self.configure(fg_color=CARD)
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.on_save = None
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        self.geometry(f"+{max(x,0)}+{max(y,0)}")

    def field(self, label, value="", show=None, placeholder=""):
        ctk.CTkLabel(self, text=label, text_color=NAVY,
                     anchor="w").pack(fill="x", padx=24, pady=(10, 2))
        e = ctk.CTkEntry(self, width=380, show=show,
                         placeholder_text=placeholder)
        e.insert(0, value)
        e.pack(padx=24)
        return e

    def combo(self, label, values, default=None):
        ctk.CTkLabel(self, text=label, text_color=NAVY,
                     anchor="w").pack(fill="x", padx=24, pady=(10, 2))
        c = ctk.CTkComboBox(self, values=values, width=380)
        if default is not None:
            c.set(default)
        c.pack(padx=24)
        return c

    def footer(self, save_text="Save", command=None):
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=24, pady=(20, 20), side="bottom")
        ctk.CTkButton(row, text="Cancel", fg_color=SLATE,
                      hover_color=NAVY2,
                      command=self.destroy).pack(side="right", padx=4)
        ctk.CTkButton(row, text=save_text, fg_color=TEAL,
                      hover_color=TEAL_D,
                      command=command).pack(side="right", padx=4)


class RoomDialog(BaseDialog):
    def __init__(self, parent, room_id=None, on_save=None):
        super().__init__(parent, "Edit Room" if room_id else "Add Room",
                         height=560)
        self.on_save = on_save
        self.room_id = room_id
        self.types = fetch_all("SELECT type_id, type_name FROM room_types "
                               "ORDER BY type_id")
        type_names = [t[1] for t in self.types]

        self.num = self.field("Room Number", placeholder="e.g. 301")
        self.type = self.combo("Room Type", type_names,
                               default=type_names[0])
        self.price = self.field("Price per Night", placeholder="120.00")
        self.cap = self.field("Capacity", value="2")
        self.status = self.combo("Status",
                                 ["Available", "Occupied", "Maintenance"],
                                 default="Available")

        if room_id:
            row = fetch_one("SELECT room_number, type_id, price_per_night, "
                            "capacity, status FROM rooms WHERE room_id=%s",
                            (room_id,))
            if row:
                self.num.delete(0, "end"); self.num.insert(0, row[0])
                tn = dict((t[0], t[1]) for t in self.types).get(row[1])
                if tn: self.type.set(tn)
                self.price.delete(0, "end"); self.price.insert(0, str(row[2]))
                self.cap.delete(0, "end"); self.cap.insert(0, str(row[3]))
                self.status.set(row[4])
                self.title("Edit Room")
        self.footer("Update" if room_id else "Create", self._save)

    def _save(self):
        try:
            num = self.num.get().strip()
            price = float(self.price.get())
            cap = int(self.cap.get())
            type_name = self.type.get()
            status = self.status.get()
            if not num or price <= 0 or cap <= 0:
                raise ValueError
        except ValueError:
            return error("Invalid", "Enter valid room number, price and capacity.")
        type_id = next((t[0] for t in self.types if t[1] == type_name), None)
        if type_id is None:
            return error("Invalid Type", "Choose a room type.")
        try:
            if self.room_id:
                execute("UPDATE rooms SET room_number=%s, type_id=%s, "
                        "price_per_night=%s, capacity=%s, status=%s "
                        "WHERE room_id=%s",
                        (num, type_id, price, cap, status, self.room_id))
            else:
                execute("INSERT INTO rooms (room_number, type_id, "
                        "price_per_night, capacity, status) "
                        "VALUES (%s,%s,%s,%s,%s)",
                        (num, type_id, price, cap, status))
        except MySQLError as e:
            return error("DB Error", str(e))
        self.destroy()
        if self.on_save: self.on_save()


class CustomerDialog(BaseDialog):
    def __init__(self, parent, customer_id=None, on_save=None):
        super().__init__(parent, "Edit Customer" if customer_id
                                  else "Add Customer", height=560)
        self.on_save = on_save
        self.customer_id = customer_id

        self.name = self.field("Full Name")
        self.phone = self.field("Phone", placeholder="0300...")
        self.email = self.field("Email")
        self.addr = self.field("Address")

        if customer_id:
            row = fetch_one("SELECT full_name, phone, email, "
                            "COALESCE(address,'') FROM customers "
                            "WHERE customer_id=%s", (customer_id,))
            if row:
                self.name.insert(0, row[0])
                self.phone.insert(0, row[1])
                self.email.insert(0, row[2])
                self.addr.insert(0, row[3])
        self.footer("Update" if customer_id else "Create", self._save)

    def _save(self):
        n = self.name.get().strip()
        p = self.phone.get().strip()
        e = self.email.get().strip()
        a = self.addr.get().strip()
        if not n or not p or not e:
            return error("Missing", "Name, phone and email are required.")
        if not valid_email(e):
            return error("Invalid Email", "Enter a valid email address.")
        if not valid_phone(p):
            return error("Invalid Phone", "Enter a valid phone number.")
        try:
            if self.customer_id:
                execute("UPDATE customers SET full_name=%s, phone=%s, "
                        "email=%s, address=%s WHERE customer_id=%s",
                        (n, p, e, a, self.customer_id))
            else:
                execute("INSERT INTO customers (full_name, phone, email, "
                        "address) VALUES (%s,%s,%s,%s)", (n, p, e, a))
        except MySQLError as ex:
            return error("DB Error", str(ex))
        self.destroy()
        if self.on_save: self.on_save()


class BookingDialog(BaseDialog):
    """Creates a booking with full overlap + maintenance checks."""

    def __init__(self, parent, on_save=None):
        super().__init__(parent, "New Booking", width=520, height=620)
        self.on_save = on_save

        custs = fetch_all("SELECT customer_id, full_name FROM customers "
                          "WHERE is_active=1 ORDER BY full_name")
        self.cust_map = {f"{r[0]} - {r[1]}": r[0] for r in custs}
        self.cust = self.combo("Customer",
                               list(self.cust_map) or ["<no customers>"])

        rooms = fetch_all("""
            SELECT r.room_id, r.room_number, rt.type_name, r.price_per_night
            FROM rooms r JOIN room_types rt ON rt.type_id = r.type_id
            WHERE r.is_active=1 AND r.status <> 'Maintenance'
            ORDER BY r.room_number
        """)
        self.room_map = {
            f"{r[1]} | {r[2]} | ${float(r[3]):.2f}/night": (r[0], float(r[3]))
            for r in rooms
        }
        self.room = self.combo("Room",
                               list(self.room_map) or ["<no rooms>"])

        self.in_date = self.field("Check-in (YYYY-MM-DD)",
                                  value=dt.date.today().isoformat())
        self.out_date = self.field("Check-out (YYYY-MM-DD)",
                                   value=(dt.date.today() +
                                          dt.timedelta(days=1)).isoformat())

        self.preview = ctk.CTkLabel(self, text="Nights: —   Total: —",
                                    font=("Segoe UI", 14, "bold"),
                                    text_color=NAVY)
        self.preview.pack(pady=(12, 0))

        ctk.CTkButton(self, text="Recalculate",
                      fg_color=SLATE, hover_color=NAVY2,
                      command=self._recalc).pack(pady=6)
        self.footer("Create Booking", self._save)

    def _parse(self):
        try:
            ci = parse_date(self.in_date.get())
            co = parse_date(self.out_date.get())
        except Exception:
            return None
        if co <= ci:
            return None
        return ci, co

    def _recalc(self):
        d = self._parse()
        if not d:
            self.preview.configure(text="Nights: —   Total: —",
                                   text_color=DANGER)
            return
        ci, co = d
        rkey = self.room.get()
        if rkey not in self.room_map:
            return
        rate = self.room_map[rkey][1]
        nights = (co - ci).days
        self.preview.configure(
            text=f"Nights: {nights}   Total: {money(nights * rate)}",
            text_color=NAVY)

    def _save(self):
        ckey = self.cust.get()
        rkey = self.room.get()
        if ckey not in self.cust_map or rkey not in self.room_map:
            return error("Missing", "Select a customer and a room.")
        d = self._parse()
        if not d:
            return error("Invalid Dates",
                         "Check-out must be after check-in (YYYY-MM-DD).")
        ci, co = d
        customer_id = self.cust_map[ckey]
        room_id, rate = self.room_map[rkey]
        nights = (co - ci).days
        total = nights * rate
        try:
            with Database.cursor() as (cur, _):
                # Lock room row for concurrency safety
                cur.execute("SELECT status, is_active FROM rooms "
                            "WHERE room_id=%s FOR UPDATE", (room_id,))
                st = cur.fetchone()
                if not st or st[1] == 0 or st[0] == "Maintenance":
                    self.destroy()
                    return error("Unavailable",
                                 "Room is inactive or under maintenance.")
                # Overlap check (half-open intervals, ignore cancelled)
                cur.execute("""
                    SELECT COUNT(*) FROM bookings
                    WHERE room_id=%s
                      AND status <> 'Cancelled'
                      AND check_in_date < %s
                      AND check_out_date > %s
                """, (room_id, co, ci))
                if cur.fetchone()[0] > 0:
                    self.destroy()
                    return error("Overlap",
                                 "This room has an active booking "
                                 "overlapping your dates.")
                cur.execute("""
                    INSERT INTO bookings
                      (customer_id, room_id, check_in_date, check_out_date,
                       number_of_nights, nightly_rate, total_amount, status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'Confirmed')
                """, (customer_id, room_id, ci, co, nights, rate, total))
            self.destroy()
            info("Success", f"Booking created. Total: {money(total)}")
            if self.on_save: self.on_save()
        except MySQLError as e:
            error("DB Error", str(e))


class PaymentDialog(BaseDialog):
    def __init__(self, parent, on_save=None):
        super().__init__(parent, "Record Payment", height=460)
        self.on_save = on_save

        rows = fetch_all("""
            SELECT b.booking_id, c.full_name, r.room_number,
                   b.total_amount -
                   (SELECT COALESCE(SUM(amount),0) FROM payments
                    WHERE booking_id=b.booking_id) AS balance
            FROM bookings b
            JOIN customers c ON c.customer_id = b.customer_id
            JOIN rooms r ON r.room_id = b.room_id
            WHERE b.status <> 'Cancelled'
            HAVING balance > 0
            ORDER BY b.booking_id DESC
        """)
        self.bk_map = {
            f"#{r[0]} | {r[1]} | Room {r[2]} | due ${float(r[3]):.2f}":
                (r[0], float(r[3]))
            for r in rows
        }
        self.bk = self.combo("Booking (only with balance due)",
                             list(self.bk_map) or ["<no outstanding>"])
        self.amount = self.field("Amount", placeholder="0.00")
        self.method = self.combo("Method",
                                 ["Cash", "Credit/Debit Card",
                                  "Bank Transfer"])
        self.footer("Record", self._save)

    def _save(self):
        k = self.bk.get()
        if k not in self.bk_map:
            return error("Missing", "Select a valid booking.")
        booking_id, due = self.bk_map[k]
        try:
            amt = float(self.amount.get())
        except ValueError:
            return error("Invalid", "Enter a numeric amount.")
        if amt <= 0:
            return error("Invalid", "Amount must be positive.")
        if amt > due + 0.001:
            return error("Too Much",
                         f"Amount exceeds outstanding balance ({money(due)}).")
        try:
            execute("INSERT INTO payments (booking_id, amount, payment_method) "
                    "VALUES (%s,%s,%s)", (booking_id, amt, self.method.get()))
        except MySQLError as e:
            return error("DB Error", str(e))
        self.destroy()
        if self.on_save: self.on_save()


# ===============================================================
# ENTRY POINT
# ===============================================================
if __name__ == "__main__":
    app = HotelApp()
    app.mainloop()
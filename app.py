"""
本地单机预约管理系统
====================
基于 Flet (Flutter) + SQLite 的桌面预约应用。
运行方式: python app.py
"""

import os
import sys
try:
    import pysqlite3 as sqlite3  # type: ignore[import-untyped]
except ImportError:
    import sqlite3
from datetime import datetime, timedelta
from contextlib import contextmanager

import flet as ft

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
# 当以 PyInstaller 打包运行时，数据库放在 exe 所在目录；开发模式放在脚本目录
if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_BASE_DIR, "appointments.db")
WORK_HOURS = list(range(9, 22))  # 09:00 – 21:00
DATE_FMT = "%Y-%m-%d"
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"

# ---------------------------------------------------------------------------
# 数据库管理器 (DBManager) — 与 UI 完全解耦
# ---------------------------------------------------------------------------

class DBManager:
    """封装所有 SQLite 操作，对外暴露纯数据接口。"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    # -- 连接管理 ----------------------------------------------------------

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -- 初始化 ------------------------------------------------------------

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS technicians (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT    NOT NULL UNIQUE,
                    active      INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS services (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    name         TEXT    NOT NULL UNIQUE,
                    duration_min INTEGER NOT NULL DEFAULT 60,
                    price        REAL    NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS members (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    member_no    TEXT    NOT NULL UNIQUE,
                    name         TEXT    NOT NULL,
                    phone        TEXT    NOT NULL DEFAULT '',
                    created_at   TEXT    NOT NULL
                );

                CREATE TABLE IF NOT EXISTS member_packages (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    member_id    INTEGER NOT NULL REFERENCES members(id),
                    service_id   INTEGER NOT NULL REFERENCES services(id),
                    total_count  INTEGER NOT NULL DEFAULT 0,
                    used_count   INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(member_id, service_id)
                );

                CREATE TABLE IF NOT EXISTS appointments (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_name    TEXT    NOT NULL,
                    customer_phone   TEXT    NOT NULL DEFAULT '',
                    member_id        INTEGER REFERENCES members(id),
                    technician_id    INTEGER NOT NULL REFERENCES technicians(id),
                    service_id       INTEGER NOT NULL REFERENCES services(id),
                    appointment_date TEXT    NOT NULL,
                    appointment_hour INTEGER NOT NULL,
                    status           TEXT    NOT NULL DEFAULT 'active',
                    created_at       TEXT    NOT NULL,
                    CHECK (appointment_hour >= 9 AND appointment_hour <= 21),
                    CHECK (status IN ('active', 'cancelled', 'completed'))
                );

                CREATE UNIQUE INDEX IF NOT EXISTS uq_tech_slot
                    ON appointments(technician_id, appointment_date, appointment_hour)
                    WHERE status IN ('active', 'completed');

                CREATE TABLE IF NOT EXISTS technician_leaves (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    technician_id   INTEGER NOT NULL REFERENCES technicians(id),
                    leave_date      TEXT    NOT NULL,
                    UNIQUE(technician_id, leave_date)
                );
            """)
            # 迁移：给旧表加 member_id 列（如果不存在）
            cols = [r[1] for r in conn.execute("PRAGMA table_info(appointments)").fetchall()]
            if "member_id" not in cols:
                conn.execute("ALTER TABLE appointments ADD COLUMN member_id INTEGER REFERENCES members(id)")
            # 迁移：允许 status='completed'（旧 CHECK 约束无法 ALTER，但新建表已包含）
            # 预置数据（首次运行时插入）
            if conn.execute("SELECT COUNT(*) FROM technicians").fetchone()[0] == 0:
                conn.executemany(
                    "INSERT INTO technicians(name) VALUES(?)",
                    [("王师傅",), ("李师傅",), ("张师傅",), ("陈师傅",)],
                )
            if conn.execute("SELECT COUNT(*) FROM services").fetchone()[0] == 0:
                conn.executemany(
                    "INSERT INTO services(name, duration_min, price) VALUES(?,?,?)",
                    [("脚底按摩", 60, 128), ("背部按摩", 60, 158), ("全身按摩", 90, 258), ("肩颈推拿", 45, 98)],
                )

    # -- 技师 CRUD ---------------------------------------------------------

    def get_technicians(self, active_only: bool = True) -> list[dict]:
        with self._conn() as conn:
            sql = "SELECT * FROM technicians"
            if active_only:
                sql += " WHERE active = 1"
            sql += " ORDER BY id"
            return [dict(r) for r in conn.execute(sql).fetchall()]

    def add_technician(self, name: str) -> int:
        with self._conn() as conn:
            cur = conn.execute("INSERT INTO technicians(name) VALUES(?)", (name,))
            return cur.lastrowid

    def toggle_technician(self, tech_id: int, active: bool):
        with self._conn() as conn:
            conn.execute("UPDATE technicians SET active=? WHERE id=?", (int(active), tech_id))

    def delete_technician(self, tech_id: int) -> bool:
        """删除停用的技师。若存在关联预约则返回 False。"""
        with self._conn() as conn:
            has_appts = conn.execute(
                "SELECT 1 FROM appointments WHERE technician_id = ? LIMIT 1",
                (tech_id,),
            ).fetchone()
            if has_appts:
                return False
            conn.execute("DELETE FROM technicians WHERE id = ? AND active = 0", (tech_id,))
            return True

    # -- 技师休假 -----------------------------------------------------------

    def add_leave(self, tech_id: int, leave_date: str):
        """给技师添加某天的休假。"""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO technician_leaves(technician_id, leave_date) VALUES(?,?)",
                (tech_id, leave_date),
            )

    def remove_leave(self, tech_id: int, leave_date: str):
        """取消技师某天的休假。"""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM technician_leaves WHERE technician_id=? AND leave_date=?",
                (tech_id, leave_date),
            )

    def get_leaves_for_date(self, date_str: str) -> set[int]:
        """返回某天所有休假技师的 id 集合。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT technician_id FROM technician_leaves WHERE leave_date=?",
                (date_str,),
            ).fetchall()
            return {r["technician_id"] for r in rows}

    def get_leaves_for_tech(self, tech_id: int, start_date: str, end_date: str) -> list[str]:
        """返回某技师在日期范围内的所有休假日期列表。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT leave_date FROM technician_leaves WHERE technician_id=? AND leave_date BETWEEN ? AND ? ORDER BY leave_date",
                (tech_id, start_date, end_date),
            ).fetchall()
            return [r["leave_date"] for r in rows]

    def is_on_leave(self, tech_id: int, date_str: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM technician_leaves WHERE technician_id=? AND leave_date=?",
                (tech_id, date_str),
            ).fetchone()
            return row is not None

    # -- 服务 CRUD ---------------------------------------------------------

    def get_services(self) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM services ORDER BY id").fetchall()]

    def add_service(self, name: str, duration: int = 60, price: float = 0) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO services(name, duration_min, price) VALUES(?,?,?)",
                (name, duration, price),
            )
            return cur.lastrowid

    # -- 会员 CRUD ---------------------------------------------------------

    def add_member(self, member_no: str, name: str, phone: str = "") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO members(member_no, name, phone, created_at) VALUES(?,?,?,?)",
                (member_no, name, phone, datetime.now().strftime(DATETIME_FMT)),
            )
            return cur.lastrowid

    def get_members(self) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM members ORDER BY id DESC").fetchall()]

    def search_members(self, keyword: str) -> list[dict]:
        like = f"%{keyword}%"
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM members WHERE name LIKE ? OR member_no LIKE ? OR phone LIKE ? ORDER BY id DESC",
                (like, like, like),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_member_by_id(self, member_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
            return dict(row) if row else None

    # -- 会员套餐 -----------------------------------------------------------

    def add_package(self, member_id: int, service_id: int, count: int):
        """为会员充值某服务的次数（累加）。"""
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id, total_count FROM member_packages WHERE member_id=? AND service_id=?",
                (member_id, service_id),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE member_packages SET total_count = total_count + ? WHERE id=?",
                    (count, existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO member_packages(member_id, service_id, total_count, used_count) VALUES(?,?,?,0)",
                    (member_id, service_id, count),
                )

    def get_member_packages(self, member_id: int) -> list[dict]:
        """获取会员所有套餐及剩余次数。"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT mp.*, s.name AS service_name,
                          (mp.total_count - mp.used_count) AS remaining
                   FROM member_packages mp
                   JOIN services s ON mp.service_id = s.id
                   WHERE mp.member_id = ?
                   ORDER BY s.id""",
                (member_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def consume_package(self, member_id: int, service_id: int) -> bool:
        """消费一次套餐，成功返回 True，余量不足返回 False。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, total_count, used_count FROM member_packages WHERE member_id=? AND service_id=?",
                (member_id, service_id),
            ).fetchone()
            if not row or row["used_count"] >= row["total_count"]:
                return False
            conn.execute(
                "UPDATE member_packages SET used_count = used_count + 1 WHERE id=?",
                (row["id"],),
            )
            return True

    # -- 预约 CRUD ---------------------------------------------------------

    def create_appointment(
        self,
        customer_name: str,
        customer_phone: str,
        technician_id: int,
        service_id: int,
        appointment_date: str,
        appointment_hour: int,
        member_id: int | None = None,
    ) -> int:
        """创建预约，冲突时抛出 sqlite3.IntegrityError。"""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO appointments
                   (customer_name, customer_phone, member_id, technician_id, service_id,
                    appointment_date, appointment_hour, status, created_at)
                   VALUES (?,?,?,?,?,?,?,'active',?)""",
                (
                    customer_name,
                    customer_phone,
                    member_id,
                    technician_id,
                    service_id,
                    appointment_date,
                    appointment_hour,
                    datetime.now().strftime(DATETIME_FMT),
                ),
            )
            return cur.lastrowid

    def cancel_appointment(self, appt_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE appointments SET status='cancelled' WHERE id=? AND status='active'",
                (appt_id,),
            )

    def complete_appointment(self, appt_id: int) -> str:
        """完成预约并扣减会员套餐。返回结果消息。"""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM appointments WHERE id=?", (appt_id,)).fetchone()
            if not row or row["status"] != "active":
                return "该预约不是有效状态"
            conn.execute("UPDATE appointments SET status='completed' WHERE id=?", (appt_id,))
            if row["member_id"]:
                pkg = conn.execute(
                    "SELECT id, total_count, used_count FROM member_packages WHERE member_id=? AND service_id=?",
                    (row["member_id"], row["service_id"]),
                ).fetchone()
                if pkg and pkg["used_count"] < pkg["total_count"]:
                    conn.execute(
                        "UPDATE member_packages SET used_count = used_count + 1 WHERE id=?",
                        (pkg["id"],),
                    )
                    remaining = pkg["total_count"] - pkg["used_count"] - 1
                    return f"已完成，套餐已扣 1 次（剩余 {remaining} 次）"
                else:
                    return "已完成（该会员无此服务套餐或已用完，未扣次）"
            return "已完成（散客，无套餐扣减）"

    def get_appointments_by_date(self, date_str: str) -> list[dict]:
        """获取某天所有有效预约（含技师名和服务名）。"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT a.*, t.name AS tech_name, s.name AS service_name,
                          m.member_no, m.name AS member_name
                   FROM appointments a
                   JOIN technicians t ON a.technician_id = t.id
                   JOIN services    s ON a.service_id    = s.id
                   LEFT JOIN members m ON a.member_id = m.id
                   WHERE a.appointment_date = ? AND a.status IN ('active', 'completed')
                   ORDER BY a.appointment_hour, t.name""",
                (date_str,),
            ).fetchall()
            return [dict(r) for r in rows]

    def search_appointments(self, keyword: str) -> list[dict]:
        """按客户姓名、手机号或会员号模糊搜索。"""
        like = f"%{keyword}%"
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT a.*, t.name AS tech_name, s.name AS service_name,
                          m.member_no, m.name AS member_name
                   FROM appointments a
                   JOIN technicians t ON a.technician_id = t.id
                   JOIN services    s ON a.service_id    = s.id
                   LEFT JOIN members m ON a.member_id = m.id
                   WHERE a.customer_name LIKE ? OR a.customer_phone LIKE ?
                         OR m.member_no LIKE ?
                   ORDER BY a.appointment_date DESC, a.appointment_hour""",
                (like, like, like),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_recent_appointments(self, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT a.*, t.name AS tech_name, s.name AS service_name,
                          m.member_no, m.name AS member_name
                   FROM appointments a
                   JOIN technicians t ON a.technician_id = t.id
                   JOIN services    s ON a.service_id    = s.id
                   LEFT JOIN members m ON a.member_id = m.id
                   ORDER BY a.created_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_daily_schedule(self, date_str: str) -> dict[int, dict[int, dict]]:
        appts = self.get_appointments_by_date(date_str)
        schedule: dict[int, dict[int, dict]] = {}
        for a in appts:
            schedule.setdefault(a["technician_id"], {})[a["appointment_hour"]] = a
        return schedule


# ---------------------------------------------------------------------------
# UI 视图层
# ---------------------------------------------------------------------------

# 全局配色
PRIMARY = ft.Colors.INDIGO
BG_COLOR = "#F5F5F5"
CARD_BG = "#FFFFFF"
ACCENT = ft.Colors.INDIGO_ACCENT_400
BOOKED_COLOR = "#E8EAF6"
COMPLETED_COLOR = "#C8E6C9"
FREE_COLOR = "#FAFAFA"
LEAVE_COLOR = "#EEEEEE"

db = DBManager()


def main(page: ft.Page):
    # -- 页面基本设置 -------------------------------------------------------
    page.title = "安蒂克服务管理系统"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.fonts = {"NotoSansSC": "fonts/NotoSansSC.ttf"}
    page.theme = ft.Theme(
        color_scheme_seed=ft.Colors.INDIGO,
        font_family="NotoSansSC",
    )
    page.padding = 0
    page.window.width = 1200
    page.window.height = 780

    # -- 状态变量 -----------------------------------------------------------
    selected_date = datetime.today()

    # -- 可复用的 SnackBar 提示 --------------------------------------------
    def show_snack(msg: str, color: str = ft.Colors.GREEN_700):
        page.show_dialog(ft.SnackBar(content=ft.Text(msg, color=ft.Colors.WHITE), bgcolor=color))

    # ======================================================================
    # 视图 1: 今日排班 (Dashboard)
    # ======================================================================

    weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    def format_date_display(d):
        return f"{d.strftime('%Y 年 %m 月 %d 日')}  {weekday_cn[d.weekday()]}"

    date_label = ft.Text(
        format_date_display(selected_date),
        size=16,
        weight=ft.FontWeight.W_500,
    )

    schedule_table = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True)

    def build_schedule_table():
        nonlocal selected_date
        ds = selected_date.strftime(DATE_FMT)
        techs = db.get_technicians()
        schedule = db.get_daily_schedule(ds)
        leaves = db.get_leaves_for_date(ds)

        header_cells = [
            ft.Container(
                content=ft.Text("时间 \\ 技师", weight=ft.FontWeight.BOLD, size=12),
                width=90, height=44, alignment=ft.Alignment(0, 0),
            )
        ]
        for t in techs:
            on_leave = t["id"] in leaves
            header_cells.append(
                ft.Container(
                    content=ft.Text(
                        t["name"] + (" (休)" if on_leave else ""),
                        weight=ft.FontWeight.BOLD, size=12,
                        color=ft.Colors.GREY_400 if on_leave else None,
                    ),
                    width=130, height=44, alignment=ft.Alignment(0, 0),
                )
            )
        header_row = ft.Row(header_cells, spacing=2)
        rows = [header_row, ft.Divider(height=1)]

        for hour in WORK_HOURS:
            cells = [
                ft.Container(
                    content=ft.Text(f"{hour:02d}:00", size=12, weight=ft.FontWeight.W_500),
                    width=90, height=56, alignment=ft.Alignment(0, 0),
                )
            ]
            for t in techs:
                on_leave = t["id"] in leaves
                appt = schedule.get(t["id"], {}).get(hour)
                if on_leave and not appt:
                    cell = ft.Container(
                        content=ft.Text("休", size=12, color=ft.Colors.GREY_400),
                        width=130, height=56, bgcolor=LEAVE_COLOR, border_radius=6,
                        border=ft.Border.all(1, ft.Colors.GREY_300),
                        alignment=ft.Alignment(0, 0),
                    )
                elif appt:
                    is_done = appt["status"] == "completed"
                    bg = COMPLETED_COLOR if is_done else BOOKED_COLOR
                    border_color = ft.Colors.GREEN_300 if is_done else ft.Colors.INDIGO_200
                    label2 = appt["service_name"] + (" [已完成]" if is_done else "")
                    display_name = appt["customer_name"]
                    if appt.get("member_no"):
                        display_name = f"[{appt['member_no']}] {display_name}"
                    cell = ft.Container(
                        content=ft.Column(
                            [
                                ft.Text(display_name, size=11, weight=ft.FontWeight.W_600),
                                ft.Text(label2, size=10, color=ft.Colors.GREY_600),
                            ],
                            spacing=2,
                            alignment=ft.MainAxisAlignment.CENTER,
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        width=130, height=56, bgcolor=bg, border_radius=6,
                        border=ft.Border.all(1, border_color),
                        alignment=ft.Alignment(0, 0),
                        tooltip=f"客户: {appt['customer_name']}\n电话: {appt['customer_phone']}\n会员号: {appt.get('member_no') or '散客'}\n服务: {appt['service_name']}\n状态: {'已完成' if is_done else '待服务'}",
                        on_click=lambda e, a=appt: show_appointment_detail(a),
                    )
                else:
                    cell = ft.Container(
                        content=ft.Icon(ft.Icons.ADD, size=16, color=ft.Colors.GREY_400),
                        width=130, height=56, bgcolor=FREE_COLOR, border_radius=6,
                        border=ft.Border.all(1, ft.Colors.GREY_200),
                        alignment=ft.Alignment(0, 0),
                        on_click=lambda e, tid=t["id"], tname=t["name"], h=hour: open_new_appt_dialog(
                            prefill_tech_id=tid, prefill_tech_name=tname, prefill_hour=h
                        ),
                    )
                cells.append(cell)
            rows.append(ft.Row(cells, spacing=2))

        schedule_table.controls = rows
        date_label.value = format_date_display(selected_date)

    def change_date(delta: int):
        nonlocal selected_date
        new_date = selected_date + timedelta(days=delta)
        today = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
        max_date = today + timedelta(days=30)
        if today <= new_date <= max_date:
            selected_date = new_date
            build_schedule_table()
            page.update()

    def on_date_prev(e):
        change_date(-1)

    def on_date_next(e):
        change_date(1)

    date_nav = ft.Row(
        [
            ft.IconButton(ft.Icons.CHEVRON_LEFT, on_click=on_date_prev, tooltip="前一天"),
            date_label,
            ft.IconButton(ft.Icons.CHEVRON_RIGHT, on_click=on_date_next, tooltip="后一天"),
            ft.TextButton("今天", on_click=lambda e: jump_today()),
        ],
        alignment=ft.MainAxisAlignment.CENTER,
    )

    def jump_today():
        nonlocal selected_date
        selected_date = datetime.today()
        build_schedule_table()
        page.update()

    dashboard_view = ft.Container(
        content=ft.Column([
            ft.Text("今日排班", size=22, weight=ft.FontWeight.BOLD),
            date_nav, ft.Divider(height=1), schedule_table
        ], spacing=8),
        padding=20, expand=True,
    )

    # ======================================================================
    # 视图 2: 新增预约（支持会员选择）
    # ======================================================================

    appt_member_search = ft.TextField(label="搜索会员（姓名/会员号，留空=散客）", width=360,
                                       on_change=lambda e: on_member_search(e))
    appt_member_results = ft.ListView(spacing=2, height=0, padding=0)
    appt_selected_member = {"id": None, "display": ""}

    appt_member_display = ft.Text("当前: 散客", size=13, color=ft.Colors.GREY_700)

    def on_member_search(e):
        kw = appt_member_search.value.strip()
        appt_member_results.controls.clear()
        if not kw:
            appt_member_results.height = 0
            page.update()
            return
        members = db.search_members(kw)
        if members:
            count = min(len(members), 4)
            appt_member_results.height = count * 48
            for m in members[:4]:
                appt_member_results.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Text(f"[{m['member_no']}] {m['name']}", size=13, weight=ft.FontWeight.W_500),
                            ft.Text(m["phone"], size=11, color=ft.Colors.GREY_600) if m["phone"] else ft.Container(),
                        ], spacing=12),
                        padding=ft.Padding(left=12, top=8, right=12, bottom=8),
                        border_radius=6,
                        bgcolor="#F0F0F0",
                        on_click=lambda e, mem=m: select_member(mem),
                        ink=True,
                    )
                )
        else:
            appt_member_results.height = 36
            appt_member_results.controls.append(ft.Text("未找到会员", size=12, color=ft.Colors.GREY_500))
        page.update()

    def select_member(m: dict):
        appt_selected_member["id"] = m["id"]
        appt_selected_member["display"] = f"[{m['member_no']}] {m['name']}"
        appt_member_display.value = f"当前: {appt_selected_member['display']}"
        appt_member_display.color = PRIMARY
        appt_customer_name.value = m["name"]
        appt_customer_phone.value = m.get("phone", "")
        appt_member_search.value = ""
        appt_member_results.controls.clear()
        appt_member_results.height = 0
        page.update()

    def clear_member(e):
        appt_selected_member["id"] = None
        appt_selected_member["display"] = ""
        appt_member_display.value = "当前: 散客"
        appt_member_display.color = ft.Colors.GREY_700
        page.update()

    appt_customer_name = ft.TextField(label="客户姓名", width=260, autofocus=True)
    appt_customer_phone = ft.TextField(label="手机号", width=260)
    appt_tech_dd = ft.Dropdown(label="技师", width=260)
    appt_service_dd = ft.Dropdown(label="服务项目", width=260)
    appt_date_picker_display = ft.TextField(label="预约日期", width=260, read_only=True)
    appt_hour_dd = ft.Dropdown(
        label="预约时间", width=260,
        options=[ft.dropdown.Option(key=str(h), text=f"{h:02d}:00") for h in WORK_HOURS],
    )

    appt_selected_date_str = ""

    def populate_appt_dropdowns(prefill_tech_id=None, prefill_hour=None):
        nonlocal appt_selected_date_str
        techs = db.get_technicians()
        services = db.get_services()
        appt_tech_dd.options = [ft.dropdown.Option(key=str(t["id"]), text=t["name"]) for t in techs]
        appt_service_dd.options = [
            ft.dropdown.Option(key=str(s["id"]), text=f"{s['name']} ({s['duration_min']}分钟 ¥{s['price']})")
            for s in services
        ]
        if prefill_tech_id is not None:
            appt_tech_dd.value = str(prefill_tech_id)
        if prefill_hour is not None:
            appt_hour_dd.value = str(prefill_hour)
        appt_selected_date_str = selected_date.strftime(DATE_FMT)
        appt_date_picker_display.value = appt_selected_date_str

    def on_appt_date_pick(e):
        nonlocal appt_selected_date_str
        raw = e.control.value
        if raw:
            local_dt = raw.astimezone(tz=None)
            appt_selected_date_str = local_dt.strftime(DATE_FMT)
            appt_date_picker_display.value = appt_selected_date_str
            page.update()

    appt_date_picker = ft.DatePicker(
        first_date=datetime.today(),
        last_date=datetime.today() + timedelta(days=30),
        on_change=on_appt_date_pick,
    )

    def open_date_picker(e):
        page.show_dialog(appt_date_picker)

    appt_date_btn = ft.IconButton(ft.Icons.CALENDAR_MONTH, on_click=open_date_picker, tooltip="选择日期")
    new_appt_result = ft.Text("", size=13)

    def submit_appointment(e):
        name = appt_customer_name.value.strip()
        phone = appt_customer_phone.value.strip()
        tech = appt_tech_dd.value
        svc = appt_service_dd.value
        date_str = appt_selected_date_str
        hour = appt_hour_dd.value

        if not name:
            new_appt_result.value = "请输入客户姓名"
            new_appt_result.color = ft.Colors.RED_700
            page.update()
            return
        if not tech or not svc or not date_str or hour is None:
            new_appt_result.value = "请填写完整预约信息"
            new_appt_result.color = ft.Colors.RED_700
            page.update()
            return

        if db.is_on_leave(int(tech), date_str):
            new_appt_result.value = "该技师当天休假，无法预约！"
            new_appt_result.color = ft.Colors.RED_700
            page.update()
            return

        try:
            db.create_appointment(
                customer_name=name,
                customer_phone=phone,
                technician_id=int(tech),
                service_id=int(svc),
                appointment_date=date_str,
                appointment_hour=int(hour),
                member_id=appt_selected_member["id"],
            )
            new_appt_result.value = "预约成功！"
            new_appt_result.color = ft.Colors.GREEN_700
            appt_customer_name.value = ""
            appt_customer_phone.value = ""
            clear_member(None)
            build_schedule_table()
        except sqlite3.IntegrityError:
            new_appt_result.value = "时间冲突：该技师在此时段已有预约！"
            new_appt_result.color = ft.Colors.RED_700
        page.update()

    new_appt_view = ft.Container(
        content=ft.Column(
            [
                ft.Text("新增预约", size=22, weight=ft.FontWeight.BOLD),
                ft.Divider(height=1),
                ft.Text("会员选择（可选）", size=14, weight=ft.FontWeight.W_500, color=ft.Colors.GREY_700),
                appt_member_search,
                appt_member_results,
                ft.Row([appt_member_display, ft.TextButton("清除会员", on_click=clear_member)]),
                ft.Divider(height=1),
                appt_customer_name,
                appt_customer_phone,
                appt_tech_dd,
                appt_service_dd,
                ft.Row([appt_date_picker_display, appt_date_btn]),
                appt_hour_dd,
                ft.Container(height=8),
                ft.FilledButton(
                    "提交预约", icon=ft.Icons.CHECK,
                    bgcolor=PRIMARY, color=ft.Colors.WHITE,
                    on_click=submit_appointment, width=260, height=44,
                ),
                new_appt_result,
            ],
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
        ),
        padding=30, expand=True,
    )

    def open_new_appt_dialog(prefill_tech_id=None, prefill_tech_name=None, prefill_hour=None):
        populate_appt_dropdowns(prefill_tech_id=prefill_tech_id, prefill_hour=prefill_hour)
        new_appt_result.value = ""
        clear_member(None)
        nav_rail.selected_index = 1
        switch_view(1)
        page.update()

    # ======================================================================
    # 视图 3: 查询预约
    # ======================================================================

    search_field = ft.TextField(label="客户姓名、手机号或会员号", width=300, on_submit=lambda e: do_search(e))
    search_results = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True)

    def do_search(e):
        kw = search_field.value.strip()
        if kw:
            results = db.search_appointments(kw)
            empty_msg = "未找到匹配的预约记录。"
        else:
            results = db.get_recent_appointments(20)
            empty_msg = "暂无预约记录。"
        search_results.controls.clear()
        if not results:
            search_results.controls.append(ft.Text(empty_msg, color=ft.Colors.GREY_600))
        else:
            for r in results:
                status_map = {"cancelled": ("已取消", ft.Colors.RED_400),
                              "completed": ("已完成", ft.Colors.BLUE_700),
                              "active": ("有效", ft.Colors.GREEN_700)}
                status_text, status_color = status_map.get(r["status"], ("未知", ft.Colors.GREY_500))
                member_info = f"  会员: [{r['member_no']}]" if r.get("member_no") else ""

                action_btns = []
                if r["status"] == "active":
                    action_btns.append(ft.TextButton("取消预约", icon=ft.Icons.CANCEL,
                                                      on_click=lambda e, aid=r["id"]: confirm_cancel(aid)))
                    action_btns.append(ft.TextButton("完成消费", icon=ft.Icons.CHECK_CIRCLE,
                                                      on_click=lambda e, aid=r["id"]: do_complete_from_search(aid)))

                card = ft.Card(
                    content=ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text(f"{r['appointment_date']}  {r['appointment_hour']:02d}:00",
                                                weight=ft.FontWeight.W_600, size=14),
                                        ft.Container(
                                            content=ft.Text(status_text, size=11, color=ft.Colors.WHITE),
                                            bgcolor=status_color, border_radius=10,
                                            padding=ft.Padding(left=8, top=2, right=8, bottom=2),
                                        ),
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                                ft.Text(f"客户: {r['customer_name']}    电话: {r['customer_phone']}{member_info}", size=13),
                                ft.Text(f"技师: {r['tech_name']}    服务: {r['service_name']}", size=13),
                                ft.Row(action_btns) if action_btns else ft.Container(),
                            ],
                            spacing=4,
                        ),
                        padding=14,
                    ),
                    width=560, elevation=1,
                )
                search_results.controls.append(card)
        page.update()

    def do_complete_from_search(appt_id: int):
        msg = db.complete_appointment(appt_id)
        build_schedule_table()
        do_search(None)
        show_snack(msg)
        page.update()

    search_view = ft.Container(
        content=ft.Column(
            [
                ft.Text("查询预约", size=22, weight=ft.FontWeight.BOLD),
                ft.Divider(height=1),
                ft.Row([search_field, ft.FilledButton("搜索", icon=ft.Icons.SEARCH, on_click=do_search)]),
                search_results,
            ],
            spacing=12,
        ),
        padding=30, expand=True,
    )

    # ======================================================================
    # 视图 4: 会员管理
    # ======================================================================

    member_list_col = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True)
    mem_no_field = ft.TextField(label="会员号", width=120)
    mem_name_field = ft.TextField(label="姓名", width=120)
    mem_phone_field = ft.TextField(label="手机号", width=140)

    def refresh_member_list():
        members = db.get_members()
        member_list_col.controls.clear()
        if not members:
            member_list_col.controls.append(ft.Text("暂无会员", color=ft.Colors.GREY_500))
            return
        for m in members:
            pkgs = db.get_member_packages(m["id"])
            pkg_text = "  |  ".join([f"{p['service_name']}: 剩 {p['remaining']} 次" for p in pkgs if p["remaining"] > 0])
            if not pkg_text:
                pkg_text = "无有效套餐"

            member_list_col.controls.append(
                ft.Card(
                    content=ft.Container(
                        content=ft.Column([
                            ft.Row([
                                ft.Text(f"[{m['member_no']}]  {m['name']}", size=14, weight=ft.FontWeight.W_600),
                                ft.Text(m["phone"], size=12, color=ft.Colors.GREY_600),
                            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                            ft.Text(pkg_text, size=12, color=PRIMARY if "剩" in pkg_text else ft.Colors.GREY_500),
                            ft.Row([
                                ft.TextButton("充值套餐", icon=ft.Icons.ADD_CARD,
                                              on_click=lambda e, mid=m["id"], mname=m["name"]: open_recharge_dialog(mid, mname)),
                                ft.TextButton("查看详情", icon=ft.Icons.INFO_OUTLINE,
                                              on_click=lambda e, mid=m["id"], mname=m["name"]: open_member_detail(mid, mname)),
                            ]),
                        ], spacing=4),
                        padding=14,
                    ),
                    width=540, elevation=1,
                )
            )

    def add_member_click(e):
        no = mem_no_field.value.strip()
        name = mem_name_field.value.strip()
        phone = mem_phone_field.value.strip()
        if not no or not name:
            show_snack("会员号和姓名为必填项", ft.Colors.RED_700)
            return
        try:
            db.add_member(no, name, phone)
            mem_no_field.value = ""
            mem_name_field.value = ""
            mem_phone_field.value = ""
            refresh_member_list()
            show_snack(f"已添加会员: [{no}] {name}")
            page.update()
        except sqlite3.IntegrityError:
            show_snack("该会员号已存在！", ft.Colors.RED_700)

    def open_recharge_dialog(member_id: int, member_name: str):
        services = db.get_services()
        svc_dd = ft.Dropdown(label="服务项目", width=240,
                             options=[ft.dropdown.Option(key=str(s["id"]), text=s["name"]) for s in services])
        count_field = ft.TextField(label="充值次数", width=120, value="10")

        def do_recharge(e):
            svc = svc_dd.value
            cnt = count_field.value.strip()
            if not svc or not cnt:
                return
            try:
                db.add_package(member_id, int(svc), int(cnt))
                page.pop_dialog()
                refresh_member_list()
                show_snack(f"充值成功: {member_name} +{cnt} 次")
                page.update()
            except ValueError:
                show_snack("次数请输入数字", ft.Colors.RED_700)

        dlg = ft.AlertDialog(
            title=ft.Text(f"为 {member_name} 充值套餐"),
            content=ft.Column([svc_dd, count_field], tight=True, spacing=12),
            actions=[
                ft.TextButton("取消", on_click=lambda e: page.pop_dialog()),
                ft.TextButton("确认充值", on_click=do_recharge),
            ],
        )
        page.show_dialog(dlg)

    def open_member_detail(member_id: int, member_name: str):
        pkgs = db.get_member_packages(member_id)
        rows = []
        for p in pkgs:
            rows.append(ft.Text(
                f"{p['service_name']}:  总 {p['total_count']} 次  |  已用 {p['used_count']} 次  |  剩余 {p['remaining']} 次",
                size=13,
            ))
        if not rows:
            rows.append(ft.Text("该会员暂无套餐记录", color=ft.Colors.GREY_500))
        dlg = ft.AlertDialog(
            title=ft.Text(f"{member_name} - 套餐详情"),
            content=ft.Column(rows, tight=True, spacing=6),
            actions=[ft.TextButton("关闭", on_click=lambda e: page.pop_dialog())],
        )
        page.show_dialog(dlg)

    member_mgmt_view = ft.Container(
        content=ft.Column(
            [
                ft.Text("会员管理", size=22, weight=ft.FontWeight.BOLD),
                ft.Divider(height=1),
                ft.Row([mem_no_field, mem_name_field, mem_phone_field,
                        ft.FilledButton("添加会员", icon=ft.Icons.PERSON_ADD, on_click=add_member_click)]),
                member_list_col,
            ],
            spacing=12,
        ),
        padding=30, expand=True,
    )

    # ======================================================================
    # 视图 5: 技师管理
    # ======================================================================

    tech_list = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True)
    tech_name_field = ft.TextField(label="技师姓名", width=200)

    def refresh_tech_list():
        techs = db.get_technicians(active_only=False)
        tech_list.controls.clear()
        for t in techs:
            status_icon = ft.Icons.CHECK_CIRCLE if t["active"] else ft.Icons.CANCEL
            status_color = ft.Colors.GREEN_700 if t["active"] else ft.Colors.GREY_500
            trailing_controls = [
                ft.Switch(value=bool(t["active"]),
                          on_change=lambda e, tid=t["id"]: toggle_tech(tid, e.control.value)),
            ]
            if t["active"]:
                trailing_controls.append(
                    ft.IconButton(ft.Icons.CALENDAR_MONTH, icon_color=PRIMARY, tooltip="排休管理",
                                  on_click=lambda e, tid=t["id"], tname=t["name"]: open_leave_dialog(tid, tname))
                )
            if not t["active"]:
                trailing_controls.append(
                    ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_400, tooltip="删除技师",
                                  on_click=lambda e, tid=t["id"], tname=t["name"]: confirm_delete_tech(tid, tname))
                )
            tech_list.controls.append(
                ft.ListTile(
                    leading=ft.Icon(status_icon, color=status_color),
                    title=ft.Text(t["name"], size=14),
                    subtitle=ft.Text("在职" if t["active"] else "停用", size=12),
                    trailing=ft.Row(trailing_controls, tight=True, spacing=0),
                )
            )

    def open_leave_dialog(tech_id: int, tech_name: str):
        today = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
        start = today.strftime(DATE_FMT)
        end = (today + timedelta(days=30)).strftime(DATE_FMT)
        existing_leaves = set(db.get_leaves_for_tech(tech_id, start, end))
        weekday_names = ["一", "二", "三", "四", "五", "六", "日"]

        checkboxes: list[tuple[str, ft.Checkbox]] = []
        cols_content = []
        for i in range(31):
            d = today + timedelta(days=i)
            ds = d.strftime(DATE_FMT)
            wd = weekday_names[d.weekday()]
            label = f"{d.month}/{d.day} ({wd})"
            cb = ft.Checkbox(label=label, value=(ds in existing_leaves))
            checkboxes.append((ds, cb))
            cols_content.append(cb)

        def do_save(e):
            for ds, cb in checkboxes:
                if cb.value:
                    db.add_leave(tech_id, ds)
                else:
                    db.remove_leave(tech_id, ds)
            page.pop_dialog()
            build_schedule_table()
            show_snack(f"已更新 {tech_name} 的排休")
            page.update()

        dlg = ft.AlertDialog(
            title=ft.Text(f"{tech_name} - 排休管理（未来 30 天）"),
            content=ft.Container(
                content=ft.Column(cols_content, scroll=ft.ScrollMode.AUTO, spacing=2),
                height=400, width=280,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda e: page.pop_dialog()),
                ft.TextButton("保存", on_click=do_save),
            ],
        )
        page.show_dialog(dlg)

    def confirm_delete_tech(tid: int, tname: str):
        def do_delete(e):
            page.pop_dialog()
            ok = db.delete_technician(tid)
            if ok:
                refresh_tech_list()
                build_schedule_table()
                show_snack(f"已删除技师: {tname}")
            else:
                show_snack(f"无法删除「{tname}」：该技师存在关联的预约记录", ft.Colors.RED_700)
            page.update()

        dlg = ft.AlertDialog(
            modal=True, title=ft.Text("确认删除"),
            content=ft.Text(f"确定要删除技师「{tname}」吗？\n仅当该技师没有任何预约记录时才可删除。"),
            actions=[
                ft.TextButton("取消", on_click=lambda e: page.pop_dialog()),
                ft.TextButton("确认删除", on_click=do_delete, style=ft.ButtonStyle(color=ft.Colors.RED_700)),
            ],
        )
        page.show_dialog(dlg)

    def toggle_tech(tid, active):
        db.toggle_technician(tid, active)
        refresh_tech_list()
        build_schedule_table()
        page.update()

    def add_tech(e):
        name = tech_name_field.value.strip()
        if not name:
            return
        try:
            db.add_technician(name)
            tech_name_field.value = ""
            refresh_tech_list()
            build_schedule_table()
            show_snack(f"已添加技师: {name}")
            page.update()
        except sqlite3.IntegrityError:
            show_snack("该技师姓名已存在！", ft.Colors.RED_700)

    tech_mgmt_view = ft.Container(
        content=ft.Column(
            [
                ft.Text("技师管理", size=22, weight=ft.FontWeight.BOLD),
                ft.Divider(height=1),
                ft.Row([tech_name_field, ft.FilledButton("添加", icon=ft.Icons.PERSON_ADD, on_click=add_tech)]),
                tech_list,
            ],
            spacing=12,
        ),
        padding=30, expand=True,
    )

    # ======================================================================
    # 视图 6: 服务项目管理
    # ======================================================================

    svc_list = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True)
    svc_name_field = ft.TextField(label="服务名称", width=160)
    svc_dur_field = ft.TextField(label="时长(分钟)", width=100, value="60")
    svc_price_field = ft.TextField(label="价格", width=100, value="0")

    def refresh_svc_list():
        services = db.get_services()
        svc_list.controls.clear()
        for s in services:
            svc_list.controls.append(
                ft.ListTile(
                    leading=ft.Icon(ft.Icons.SPA, color=PRIMARY),
                    title=ft.Text(s["name"], size=14),
                    subtitle=ft.Text(f"{s['duration_min']}分钟  ¥{s['price']}", size=12),
                )
            )

    def add_svc(e):
        name = svc_name_field.value.strip()
        if not name:
            return
        try:
            dur = int(svc_dur_field.value or 60)
            price = float(svc_price_field.value or 0)
            db.add_service(name, dur, price)
            svc_name_field.value = ""
            svc_dur_field.value = "60"
            svc_price_field.value = "0"
            refresh_svc_list()
            show_snack(f"已添加服务: {name}")
            page.update()
        except sqlite3.IntegrityError:
            show_snack("该服务名称已存在！", ft.Colors.RED_700)
        except ValueError:
            show_snack("时长和价格请输入数字！", ft.Colors.RED_700)

    svc_mgmt_view = ft.Container(
        content=ft.Column(
            [
                ft.Text("服务项目管理", size=22, weight=ft.FontWeight.BOLD),
                ft.Divider(height=1),
                ft.Row([svc_name_field, svc_dur_field, svc_price_field,
                        ft.FilledButton("添加", icon=ft.Icons.ADD, on_click=add_svc)]),
                svc_list,
            ],
            spacing=12,
        ),
        padding=30, expand=True,
    )

    # ======================================================================
    # 取消预约对话框
    # ======================================================================

    def confirm_cancel(appt_id: int):
        def do_cancel(e):
            db.cancel_appointment(appt_id)
            page.pop_dialog()
            build_schedule_table()
            do_search(None)
            show_snack("预约已取消")
            page.update()

        dlg = ft.AlertDialog(
            modal=True, title=ft.Text("确认取消"),
            content=ft.Text("确定要取消此预约吗？此操作不可撤销。"),
            actions=[
                ft.TextButton("取消", on_click=lambda e: page.pop_dialog()),
                ft.TextButton("确认取消预约", on_click=do_cancel),
            ],
        )
        page.show_dialog(dlg)

    # ======================================================================
    # 预约详情弹窗（从排班表点击已预约格子触发）
    # ======================================================================

    def show_appointment_detail(appt: dict):
        status = appt["status"]
        member_label = f"会员: [{appt['member_no']}] {appt.get('member_name', '')}" if appt.get("member_no") else "散客"

        def do_cancel(e):
            db.cancel_appointment(appt["id"])
            page.pop_dialog()
            build_schedule_table()
            show_snack("预约已取消")
            page.update()

        def do_complete(e):
            msg = db.complete_appointment(appt["id"])
            page.pop_dialog()
            build_schedule_table()
            show_snack(msg)
            page.update()

        actions = [ft.TextButton("关闭", on_click=lambda e: page.pop_dialog())]
        if status == "active":
            actions.insert(0, ft.TextButton("完成消费", icon=ft.Icons.CHECK_CIRCLE,
                                             on_click=do_complete, style=ft.ButtonStyle(color=ft.Colors.GREEN_700)))
            actions.insert(0, ft.TextButton("取消预约", on_click=do_cancel,
                                             style=ft.ButtonStyle(color=ft.Colors.RED_700)))

        # 如果是会员，显示该服务剩余次数
        pkg_info = ""
        if appt.get("member_id") and status == "active":
            pkgs = db.get_member_packages(appt["member_id"])
            for p in pkgs:
                if p["service_id"] == appt["service_id"]:
                    pkg_info = f"\n该会员「{p['service_name']}」套餐剩余: {p['remaining']} 次"
                    break

        dlg = ft.AlertDialog(
            title=ft.Text("预约详情"),
            content=ft.Column(
                [
                    ft.Text(f"客户姓名: {appt['customer_name']}", size=14),
                    ft.Text(f"客户电话: {appt['customer_phone']}", size=14),
                    ft.Text(f"{member_label}", size=14),
                    ft.Text(f"技师: {appt['tech_name']}", size=14),
                    ft.Text(f"服务: {appt['service_name']}", size=14),
                    ft.Text(f"日期: {appt['appointment_date']}", size=14),
                    ft.Text(f"时间: {appt['appointment_hour']:02d}:00", size=14),
                    ft.Text(f"状态: {'待服务' if status == 'active' else '已完成'}", size=14),
                    ft.Text(pkg_info, size=13, color=PRIMARY, weight=ft.FontWeight.W_500) if pkg_info else ft.Container(),
                ],
                tight=True, spacing=6,
            ),
            actions=actions,
        )
        page.show_dialog(dlg)

    # ======================================================================
    # 侧边导航栏 & 页面路由
    # ======================================================================

    content_area = ft.Container(expand=True)

    views = [dashboard_view, new_appt_view, search_view, member_mgmt_view, tech_mgmt_view, svc_mgmt_view]

    def switch_view(index: int):
        if index == 0:
            build_schedule_table()
        elif index == 1:
            populate_appt_dropdowns()
            new_appt_result.value = ""
            clear_member(None)
        elif index == 2:
            do_search(None)
        elif index == 3:
            refresh_member_list()
        elif index == 4:
            refresh_tech_list()
        elif index == 5:
            refresh_svc_list()
        content_area.content = views[index]

    def on_nav_change(e):
        idx = e.control.selected_index
        switch_view(idx)
        page.update()

    nav_rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=80, min_extended_width=200, bgcolor=CARD_BG,
        destinations=[
            ft.NavigationRailDestination(icon=ft.Icons.DASHBOARD_OUTLINED, selected_icon=ft.Icons.DASHBOARD, label="今日排班"),
            ft.NavigationRailDestination(icon=ft.Icons.ADD_CIRCLE_OUTLINE, selected_icon=ft.Icons.ADD_CIRCLE, label="新增预约"),
            ft.NavigationRailDestination(icon=ft.Icons.SEARCH_OUTLINED, selected_icon=ft.Icons.SEARCH, label="查询预约"),
            ft.NavigationRailDestination(icon=ft.Icons.CARD_MEMBERSHIP_OUTLINED, selected_icon=ft.Icons.CARD_MEMBERSHIP, label="会员管理"),
            ft.NavigationRailDestination(icon=ft.Icons.PEOPLE_OUTLINE, selected_icon=ft.Icons.PEOPLE, label="技师管理"),
            ft.NavigationRailDestination(icon=ft.Icons.SPA_OUTLINED, selected_icon=ft.Icons.SPA, label="服务管理"),
        ],
        on_change=on_nav_change,
    )

    # -- 初始化默认视图 ----------------------------------------------------
    build_schedule_table()
    switch_view(0)

    page.add(
        ft.Row(
            [nav_rail, ft.VerticalDivider(width=1), content_area],
            expand=True,
        )
    )


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if "--web" in sys.argv:
        # 浏览器模式（开发调试用）: python app.py --web
        ft.run(main, view=ft.AppView.WEB_BROWSER, port=8550)
    else:
        # 桌面模式（默认，发布用）
        ft.run(main)

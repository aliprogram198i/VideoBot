from pathlib import Path

PATH = Path("bot.py")
MARKER = "# ALIBOT_USER_AUDIT_V1"

if not PATH.exists():
    raise SystemExit("bot.py not found")

text = PATH.read_text(encoding="utf-8")
if MARKER in text:
    print("AliBot user audit already applied")
    raise SystemExit(0)

# ------------------------------------------------------------
# Database index: cheap, non-destructive, and useful for audits.
# ------------------------------------------------------------
anchor = '''    conn.commit()\n    conn.close()\n\n    print("✅ Database initialized successfully.")\n'''
replacement = '''    cur.execute("""\n        CREATE INDEX IF NOT EXISTS idx_downloads_user_id\n        ON downloads(user_id)\n    """)\n\n    conn.commit()\n    conn.close()\n\n    print("✅ Database initialized successfully.")\n'''
if anchor not in text:
    raise RuntimeError("database commit anchor not found")
text = text.replace(anchor, replacement, 1)

# ------------------------------------------------------------
# Audit + recovery functions are inserted before admin_users.
# They never manufacture Telegram IDs: every candidate must already
# exist in an internal event table with a real user_id.
# ------------------------------------------------------------
anchor = '''async def admin_users(\n    update: Update,\n    context: ContextTypes.DEFAULT_TYPE\n):\n'''
functions = r'''def get_hidden_user_audit(page=0, per_page=8):
    """Find real Telegram IDs present in event history but absent from users.

    Sources are limited to downloads and error_logs. This intentionally
    cannot discover people who left no persisted event behind.
    """
    try:
        page = max(0, int(page))
    except (TypeError, ValueError):
        page = 0

    per_page = max(1, min(20, int(per_page)))
    offset = page * per_page

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        WITH events AS (
            SELECT
                user_id,
                username,
                created_at,
                1 AS download_count,
                0 AS error_count
            FROM downloads
            WHERE user_id IS NOT NULL

            UNION ALL

            SELECT
                user_id,
                username,
                created_at,
                0 AS download_count,
                1 AS error_count
            FROM error_logs
            WHERE user_id IS NOT NULL
        ),
        orphaned AS (
            SELECT
                e.user_id,
                MAX(NULLIF(e.username, '')) AS username,
                MIN(e.created_at) AS first_seen,
                MAX(e.created_at) AS last_seen,
                SUM(e.download_count) AS download_events,
                SUM(e.error_count) AS error_events
            FROM events e
            LEFT JOIN users u ON u.user_id = e.user_id
            WHERE u.user_id IS NULL
            GROUP BY e.user_id
        )
        SELECT
            user_id,
            username,
            first_seen,
            last_seen,
            download_events,
            error_events
        FROM orphaned
        ORDER BY last_seen DESC, user_id DESC
        LIMIT ? OFFSET ?
    """, (per_page, offset))

    rows = cur.fetchall()

    cur.execute("""
        WITH events AS (
            SELECT user_id
            FROM downloads
            WHERE user_id IS NOT NULL
            UNION
            SELECT user_id
            FROM error_logs
            WHERE user_id IS NOT NULL
        )
        SELECT COUNT(*) AS count
        FROM events e
        LEFT JOIN users u ON u.user_id = e.user_id
        WHERE u.user_id IS NULL
    """)
    total = cur.fetchone()["count"] or 0

    cur.execute("""
        SELECT
            (SELECT COUNT(*) FROM downloads d
             LEFT JOIN users u ON u.user_id = d.user_id
             WHERE d.user_id IS NOT NULL AND u.user_id IS NULL) AS orphan_downloads,
            (SELECT COUNT(*) FROM error_logs e
             LEFT JOIN users u ON u.user_id = e.user_id
             WHERE e.user_id IS NOT NULL AND u.user_id IS NULL) AS orphan_errors
    """)
    counts = cur.fetchone()

    conn.close()

    return {
        "rows": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "orphan_downloads": counts["orphan_downloads"] or 0,
        "orphan_errors": counts["orphan_errors"] or 0,
    }


def restore_hidden_user(user_id):
    """Safely reconstruct one missing users row from persisted events."""
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return False

    conn = get_db()
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            if cur.fetchone():
                return False

            cur.execute("""
                SELECT
                    MAX(NULLIF(username, '')) AS username,
                    MIN(created_at) AS first_seen,
                    MAX(created_at) AS last_seen,
                    COUNT(*) AS downloads
                FROM downloads
                WHERE user_id = ?
            """, (user_id,))
            download_row = cur.fetchone()

            cur.execute("""
                SELECT
                    MAX(NULLIF(username, '')) AS username,
                    MIN(created_at) AS first_seen,
                    MAX(created_at) AS last_seen
                FROM error_logs
                WHERE user_id = ?
            """, (user_id,))
            error_row = cur.fetchone()

            username = (
                download_row["username"]
                or error_row["username"]
                if download_row and error_row
                else (download_row["username"] if download_row else error_row["username"])
            )
            first_values = [
                row["first_seen"]
                for row in (download_row, error_row)
                if row and row["first_seen"]
            ]
            last_values = [
                row["last_seen"]
                for row in (download_row, error_row)
                if row and row["last_seen"]
            ]

            if not first_values and not last_values:
                return False

            first_seen = min(first_values) if first_values else datetime.now().isoformat()
            last_seen = max(last_values) if last_values else first_seen
            downloads = int(download_row["downloads"] or 0) if download_row else 0

            cur.execute("""
                INSERT INTO users (
                    user_id,
                    username,
                    first_seen,
                    last_seen,
                    downloads,
                    is_banned
                )
                VALUES (?, ?, ?, ?, ?, 0)
            """, (
                user_id,
                username,
                first_seen,
                last_seen,
                downloads,
            ))

            return cur.rowcount == 1
    finally:
        conn.close()


def restore_all_hidden_users():
    """Restore every currently detected orphan without overwriting users."""
    conn = get_db()
    restored = 0
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT e.user_id
                FROM (
                    SELECT user_id FROM downloads WHERE user_id IS NOT NULL
                    UNION
                    SELECT user_id FROM error_logs WHERE user_id IS NOT NULL
                ) e
                LEFT JOIN users u ON u.user_id = e.user_id
                WHERE u.user_id IS NULL
            """)
            ids = [row["user_id"] for row in cur.fetchall()]

            for user_id in ids:
                cur.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
                if cur.fetchone():
                    continue

                cur.execute("""
                    SELECT
                        MAX(NULLIF(username, '')) AS username,
                        MIN(created_at) AS first_seen,
                        MAX(created_at) AS last_seen,
                        COUNT(*) AS downloads
                    FROM downloads
                    WHERE user_id = ?
                """, (user_id,))
                d = cur.fetchone()

                cur.execute("""
                    SELECT
                        MAX(NULLIF(username, '')) AS username,
                        MIN(created_at) AS first_seen,
                        MAX(created_at) AS last_seen
                    FROM error_logs
                    WHERE user_id = ?
                """, (user_id,))
                e = cur.fetchone()

                values_first = [x["first_seen"] for x in (d, e) if x and x["first_seen"]]
                values_last = [x["last_seen"] for x in (d, e) if x and x["last_seen"]]
                if not values_first and not values_last:
                    continue

                username = (d["username"] if d and d["username"] else None) or (e["username"] if e else None)
                first_seen = min(values_first) if values_first else datetime.now().isoformat()
                last_seen = max(values_last) if values_last else first_seen
                downloads = int(d["downloads"] or 0) if d else 0

                cur.execute("""
                    INSERT INTO users (
                        user_id, username, first_seen, last_seen,
                        downloads, is_banned
                    )
                    VALUES (?, ?, ?, ?, ?, 0)
                """, (user_id, username, first_seen, last_seen, downloads))
                restored += 1

        return restored
    finally:
        conn.close()


async def admin_user_audit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    data = get_hidden_user_audit(0)
    total = data["total"]

    if total == 0:
        text = (
            "🔎 تدقيق المستخدمين\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "✅ لا توجد حالياً سجلات لمستخدمين مخفيين.\n\n"
            "تم فحص سجل التحميلات وسجل الأخطاء، وجميع Telegram IDs الموجودة فيهما مرتبطة بجدول المستخدمين."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 إعادة الفحص", callback_data="admin_user_audit")],
            [InlineKeyboardButton("🔙 المستخدمون", callback_data="admin_users_0")],
        ])
    else:
        text = (
            "🔎 المستخدمون غير المسجلين\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"⚠️ المكتشفون: {total}\n"
            f"📥 آثار تحميل: {data['orphan_downloads']}\n"
            f"🐞 آثار أخطاء: {data['orphan_errors']}\n\n"
            "هؤلاء لديهم أثر محفوظ في النظام، لكن لا يوجد لهم سجل في users.\n\n"
            "اختر مستخدماً لعرض خيارات الاستعادة."
        )
        keyboard_rows = []
        for row in data["rows"]:
            name = "@" + row["username"] if row["username"] else f"ID {row['user_id']}"
            label = f"⚠️ {name[:22]} │ 📥 {row['download_events']} │ 🐞 {row['error_events']}"
            keyboard_rows.append([
                InlineKeyboardButton(label, callback_data=f"hidden_user_{row['user_id']}")
            ])

        navigation = []
        if data["page"] > 0:
            navigation.append(InlineKeyboardButton("⬅️", callback_data=f"admin_user_audit_page_{data['page'] - 1}"))
        if (data["page"] + 1) * data["per_page"] < total:
            navigation.append(InlineKeyboardButton("➡️", callback_data=f"admin_user_audit_page_{data['page'] + 1}"))
        if navigation:
            keyboard_rows.append(navigation)

        keyboard_rows.append([InlineKeyboardButton("♻️ استعادة الكل", callback_data="admin_user_audit_restore_all")])
        keyboard_rows.append([InlineKeyboardButton("🔄 إعادة الفحص", callback_data="admin_user_audit")])
        keyboard_rows.append([InlineKeyboardButton("🔙 المستخدمون", callback_data="admin_users_0")])
        keyboard = InlineKeyboardMarkup(keyboard_rows)

    await query.edit_message_text(text, reply_markup=keyboard)


async def admin_user_audit_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    try:
        page = int(query.data.rsplit("_", 1)[1])
    except (ValueError, IndexError):
        page = 0

    data = get_hidden_user_audit(page)
    total = data["total"]
    keyboard_rows = []

    for row in data["rows"]:
        name = "@" + row["username"] if row["username"] else f"ID {row['user_id']}"
        keyboard_rows.append([
            InlineKeyboardButton(
                f"⚠️ {name[:22]} │ 📥 {row['download_events']} │ 🐞 {row['error_events']}",
                callback_data=f"hidden_user_{row['user_id']}"
            )
        ])

    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton("⬅️", callback_data=f"admin_user_audit_page_{page - 1}"))
    if (page + 1) * data["per_page"] < total:
        navigation.append(InlineKeyboardButton("➡️", callback_data=f"admin_user_audit_page_{page + 1}"))
    if navigation:
        keyboard_rows.append(navigation)

    keyboard_rows.extend([
        [InlineKeyboardButton("♻️ استعادة الكل", callback_data="admin_user_audit_restore_all")],
        [InlineKeyboardButton("🔙 المستخدمون", callback_data="admin_users_0")],
    ])

    await query.edit_message_text(
        "🔎 المستخدمون غير المسجلين\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"⚠️ المكتشفون: {total}\n\n"
        "اختر مستخدماً للاستعادة الآمنة.",
        reply_markup=InlineKeyboardMarkup(keyboard_rows)
    )


async def admin_hidden_user_details_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    try:
        user_id = int(query.data.replace("hidden_user_", ""))
    except ValueError:
        return

    data = get_hidden_user_audit(0, 1000)
    row = next((item for item in data["rows"] if item["user_id"] == user_id), None)

    # The row may be beyond the first page; query it directly to avoid false negatives.
    if row is None:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                e.user_id,
                MAX(NULLIF(e.username, '')) AS username,
                MIN(e.created_at) AS first_seen,
                MAX(e.created_at) AS last_seen,
                SUM(e.download_count) AS download_events,
                SUM(e.error_count) AS error_events
            FROM (
                SELECT user_id, username, created_at, 1 AS download_count, 0 AS error_count
                FROM downloads WHERE user_id = ?
                UNION ALL
                SELECT user_id, username, created_at, 0 AS download_count, 1 AS error_count
                FROM error_logs WHERE user_id = ?
            ) e
            LEFT JOIN users u ON u.user_id = e.user_id
            WHERE u.user_id IS NULL
            GROUP BY e.user_id
        """, (user_id, user_id))
        row = cur.fetchone()
        conn.close()

    if not row:
        await query.edit_message_text(
            "ℹ️ هذا المستخدم لم يعد ضمن قائمة المستخدمين المخفيين.\n\n"
            "ربما تمت استعادته بالفعل.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 إعادة الفحص", callback_data="admin_user_audit")],
                [InlineKeyboardButton("🔙 المستخدمون", callback_data="admin_users_0")],
            ])
        )
        return

    name = "@" + row["username"] if row["username"] else "غير معروف"
    text = (
        "⚠️ مستخدم غير مسجل\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 username: {name}\n"
        f"🆔 Telegram ID: {row['user_id']}\n\n"
        f"📥 آثار التحميل: {row['download_events']}\n"
        f"🐞 آثار الأخطاء: {row['error_events']}\n"
        f"📅 أول أثر: {row['first_seen']}\n"
        f"🕐 آخر أثر: {row['last_seen']}\n\n"
        "♻️ الاستعادة ستنشئ سجل users اعتماداً فقط على البيانات الموجودة في السجلات.\n"
        "لن يتم اختلاق الاسم أو Telegram ID أو تغيير سجل موجود."
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("♻️ استعادة المستخدم", callback_data=f"hidden_restore_{user_id}")],
            [InlineKeyboardButton("🔙 المستخدمون غير المسجلين", callback_data="admin_user_audit")],
        ])
    )


async def admin_hidden_user_restore_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    try:
        user_id = int(query.data.replace("hidden_restore_", ""))
    except ValueError:
        return

    restored = restore_hidden_user(user_id)
    await query.edit_message_text(
        "♻️ استعادة المستخدم\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        + ("✅ تمت استعادة المستخدم بنجاح." if restored else "ℹ️ لم تتم الاستعادة؛ المستخدم قد يكون موجوداً بالفعل أو لا توجد بيانات كافية.")
        + "\n\n"
        "لم يتم تعديل أي مستخدم موجود.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 إعادة الفحص", callback_data="admin_user_audit")],
            [InlineKeyboardButton("🔙 المستخدمون", callback_data="admin_users_0")],
        ])
    )


async def admin_hidden_user_restore_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    restored = restore_all_hidden_users()
    await query.edit_message_text(
        "♻️ استعادة المستخدمين\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ تمت استعادة: {restored}\n\n"
        "تمت العملية داخل معاملات SQLite، ولم يتم الكتابة فوق أي سجل موجود.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 إعادة الفحص", callback_data="admin_user_audit")],
            [InlineKeyboardButton("👥 المستخدمون", callback_data="admin_users_0")],
        ])
    )


'''
if anchor not in text:
    raise RuntimeError("admin_users anchor not found")
text = text.replace(anchor, functions + anchor, 1)

# Add the audit button to the existing users screen, immediately before search.
anchor = '''    keyboard.append([\n\n        InlineKeyboardButton(\n            "🔍 بحث عن مستخدم",\n            callback_data="admin_search"\n        )\n\n    ])\n'''
replacement = '''    keyboard.append([\n\n        InlineKeyboardButton(\n            "🔎 تدقيق المستخدمين المخفيين",\n            callback_data="admin_user_audit"\n        )\n\n    ])\n\n''' + anchor
if anchor not in text:
    raise RuntimeError("admin search button anchor not found")
text = text.replace(anchor, replacement, 1)

# Register callback handlers next to the existing admin search handler.
anchor = '''    app.add_handler(\n        CallbackQueryHandler(\n            admin_search_callback,\n            pattern=r"^admin_search$"\n        )\n    )\n'''
replacement = '''    app.add_handler(CallbackQueryHandler(admin_user_audit_callback, pattern=r"^admin_user_audit$"))\n    app.add_handler(CallbackQueryHandler(admin_user_audit_page_callback, pattern=r"^admin_user_audit_page_\\d+$"))\n    app.add_handler(CallbackQueryHandler(admin_hidden_user_details_callback, pattern=r"^hidden_user_-?\\d+$"))\n    app.add_handler(CallbackQueryHandler(admin_hidden_user_restore_callback, pattern=r"^hidden_restore_-?\\d+$"))\n    app.add_handler(CallbackQueryHandler(admin_hidden_user_restore_all_callback, pattern=r"^admin_user_audit_restore_all$"))\n\n''' + anchor
if anchor not in text:
    raise RuntimeError("admin search handler anchor not found")
text = text.replace(anchor, replacement, 1)

# The patcher uses datetime already in bot.py. Mark the generated source
# after all transformations so this patch remains idempotent at image build time.
PATH.write_text(MARKER + "\n" + text, encoding="utf-8")
print("AliBot hidden-user audit applied successfully")

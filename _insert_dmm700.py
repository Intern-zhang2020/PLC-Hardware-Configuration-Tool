import mysql.connector

conn = mysql.connector.connect(
    host="192.168.5.100", port=49155, user="root", password="123",
    database="ProductInfo", charset="utf8mb4", use_unicode=True,
)
cur = conn.cursor(dictionary=True)
cur.execute("DESCRIBE base")
for r in cur.fetchall():
    print(r["Field"], r["Type"], "NULL" if r["Null"] == "YES" else "NOTNULL", "default=", r["Default"], "extra=", r["Extra"])

cur.execute("SELECT id, module_name FROM base WHERE module_name = 'DMM700'")
existing = cur.fetchone()
if existing:
    print("DMM700 already exists:", existing)
else:
    cur.execute(
        """
        INSERT INTO base (
            module_name, module_cname, module_type, module_price,
            channel_number, redundancy_support, power, install_type,
            size, lifecycle, remark
        ) VALUES (
            'DMM700', '空槽位填充模块', '填充', 0.00,
            0, 0, 0.0, '背板安装',
            '32.5x130x86.8mm', '在产',
            '空模块，仅用于底板空槽位占位，每个占1个槽位'
        )
        """
    )
    conn.commit()
    print("inserted DMM700, id =", cur.lastrowid)

cur.execute("SELECT id, module_name, module_cname, module_type, module_price FROM base WHERE module_name = 'DMM700'")
print(cur.fetchone())
cur.close()
conn.close()

import mysql.connector
import re

LOCAL  = dict(host='localhost', user='root', password='', database='chess_enigma')
REMOTE = dict(host='8.230.105.83', port=3306, user='uj8wekkw7y3qr',
              password='SeguridadSiteDb2026..', database='db7wv1zc2zaekz')

# ── Helpers ──────────────────────────────────────────────────────────────────
def add_column(cur, table, col, definition):
    try:
        cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{col}` {definition}")
        print(f"  + {table}.{col} agregado")
    except mysql.connector.Error as e:
        if e.errno == 1060:
            print(f"  ~ {table}.{col} ya existe")
        else:
            raise

def strip_html(text):
    return re.sub(r'<[^>]+>', '', text or '').strip()

API_MAP = {
    'video':    'video',
    'pdf':      'pdf',
    'file':     'drive',
    'pgn':      'pgn',
    'image':    'image',
    'quiz':     'quiz',
    'ejercicio':'ejercicio',
    'proyecto': 'proyecto',
}

# ── 1. Conectar a local y agregar columnas nuevas ────────────────────────────
print("Conectando a DB local…")
local = mysql.connector.connect(**LOCAL)
lc    = local.cursor()

add_column(lc, 'cursos',    'tipo',          "VARCHAR(20) DEFAULT 'chess'")
add_column(lc, 'lecciones', 'video_url',     'TEXT')
add_column(lc, 'lecciones', 'drive_file_id', 'VARCHAR(255)')
local.commit()

# ── 2. Traer datos de cursara ─────────────────────────────────────────────────
print("\nConectando a cursara remota…")
remote = mysql.connector.connect(**REMOTE)
rc     = remote.cursor(dictionary=True)

rc.execute("""
    SELECT c.id, c.title, c.description, c.level, cat.name AS categoria
    FROM   courses c
    LEFT JOIN categories cat ON c.category_id = cat.id
    WHERE  c.is_published = 1
    ORDER  BY c.id
""")
cursos = rc.fetchall()
print(f"  {len(cursos)} cursos publicados encontrados")

rc.execute("SELECT id, course_id, title, `order` FROM sections ORDER BY course_id, `order`")
secciones = rc.fetchall()
print(f"  {len(secciones)} secciones")

rc.execute("""
    SELECT id, section_id, title, type, duration, `order`,
           drive_file_id, video_url
    FROM   lessons
    ORDER  BY section_id, `order`
""")
lecciones_remote = rc.fetchall()
print(f"  {len(lecciones_remote)} lecciones")

rc.close(); remote.close()

# ── 3. Indexar por FK ────────────────────────────────────────────────────────
secs_by_course  = {}
for s in secciones:
    secs_by_course.setdefault(s['course_id'], []).append(s)

lecs_by_section = {}
for l in lecciones_remote:
    lecs_by_section.setdefault(l['section_id'], []).append(l)

# ── 4. Insertar en local ─────────────────────────────────────────────────────
print("\nInsertando en DB local…")
ins_c = ins_s = ins_l = 0

for ci, c in enumerate(cursos):
    curso_id = f"cursara-{c['id']}"
    lc.execute("""
        INSERT IGNORE INTO cursos (id, nombre, descripcion, orden, tipo)
        VALUES (%s, %s, %s, %s, 'cursara')
    """, (curso_id, c['title'], strip_html(c['description']), 1000 + ci))
    if lc.rowcount: ins_c += 1

    for si, sec in enumerate(secs_by_course.get(c['id'], [])):
        sec_id = f"cursara-sec-{sec['id']}"
        lc.execute("""
            INSERT IGNORE INTO secciones (id, curso_id, nombre, orden)
            VALUES (%s, %s, %s, %s)
        """, (sec_id, curso_id, sec['title'], si))
        if lc.rowcount: ins_s += 1

        for li, lec in enumerate(lecs_by_section.get(sec['id'], [])):
            lec_id  = f"cursara-lec-{lec['id']}"
            api_val = API_MAP.get(lec['type'], lec['type'] or 'video')
            lc.execute("""
                INSERT IGNORE INTO lecciones
                    (id, seccion_id, titulo, subtitulo, api, drive_file_id, video_url, orden)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                lec_id, sec_id, lec['title'],
                lec.get('duration') or None,
                api_val,
                lec.get('drive_file_id') or None,
                lec.get('video_url') or None,
                li
            ))
            if lc.rowcount: ins_l += 1

local.commit()

print(f"\n✓ Migración completa:")
print(f"  Cursos nuevos  : {ins_c}")
print(f"  Secciones nuevas: {ins_s}")
print(f"  Lecciones nuevas: {ins_l}")

lc.close(); local.close()

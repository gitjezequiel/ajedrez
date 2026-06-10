"""
Crea tablas para Talleres de Tácticas e inserta el primer taller (Clavadas).
NO toca ninguna tabla existente.
"""
import mysql.connector

conn = mysql.connector.connect(host='localhost', user='root', password='', database='chess_enigma')
cur = conn.cursor()

# ── Tablas nuevas (IF NOT EXISTS = seguro) ───────────────────────────────────

cur.execute("""
CREATE TABLE IF NOT EXISTS practica_grupos (
    id     INT AUTO_INCREMENT PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL UNIQUE,
    orden  INT DEFAULT 0
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS practica_talleres (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    grupo_id    INT NOT NULL,
    slug        VARCHAR(100) NOT NULL UNIQUE,
    titulo      VARCHAR(200) NOT NULL,
    descripcion TEXT,
    orden       INT DEFAULT 0,
    FOREIGN KEY (grupo_id) REFERENCES practica_grupos(id) ON DELETE CASCADE
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS practica_lecciones (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    taller_id  INT NOT NULL,
    num        INT NOT NULL,
    titulo     VARCHAR(200) NOT NULL,
    study_id   VARCHAR(20) NOT NULL,
    chapter_id VARCHAR(20) NOT NULL,
    orden      INT DEFAULT 0,
    FOREIGN KEY (taller_id) REFERENCES practica_talleres(id) ON DELETE CASCADE
)
""")

conn.commit()
print("Tablas creadas.")

# ── Grupo: Taller de Tácticas ────────────────────────────────────────────────

cur.execute("SELECT id FROM practica_grupos WHERE nombre = %s", ('Taller de Tácticas',))
row = cur.fetchone()
if row:
    grupo_id = row[0]
    print(f"Grupo ya existe (id={grupo_id}), saltando.")
else:
    cur.execute("INSERT INTO practica_grupos (nombre, orden) VALUES (%s, %s)",
                ('Taller de Tácticas', 1))
    conn.commit()
    grupo_id = cur.lastrowid
    print(f"Grupo insertado (id={grupo_id}).")

# ── Taller 1: Clavadas ───────────────────────────────────────────────────────

cur.execute("SELECT id FROM practica_talleres WHERE slug = %s", ('clavadas',))
row = cur.fetchone()
if row:
    taller_id = row[0]
    print(f"Taller 'clavadas' ya existe (id={taller_id}), saltando.")
else:
    cur.execute("""
        INSERT INTO practica_talleres (grupo_id, slug, titulo, descripcion, orden)
        VALUES (%s, %s, %s, %s, %s)
    """, (grupo_id, 'clavadas', 'Clavadas',
          '3 estudios sobre el arte de la clavada (pin)', 1))
    conn.commit()
    taller_id = cur.lastrowid
    print(f"Taller insertado (id={taller_id}).")

# ── Lecciones del taller Clavadas ────────────────────────────────────────────

cur.execute("SELECT COUNT(*) FROM practica_lecciones WHERE taller_id = %s", (taller_id,))
count = cur.fetchone()[0]
if count == 0:
    LECCIONES = [
        (1, 'Ejercicio 6 — Clavada',          'TUsg6DaQ', 'YwFkPk8B', 1),
        (2, 'Ejercicios de clavada — Cap. 2',  '3m8mhJQp', 'oFs50pTU', 2),
        (3, 'Ejemplo de clavada',              'QXNvS4ek', 'lS9QQ2ou', 3),
    ]
    cur.executemany("""
        INSERT INTO practica_lecciones (taller_id, num, titulo, study_id, chapter_id, orden)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, [(taller_id, num, titulo, sid, cid, orden)
          for num, titulo, sid, cid, orden in LECCIONES])
    conn.commit()
    print(f"Insertadas {len(LECCIONES)} lecciones.")
else:
    print(f"Lecciones ya existen ({count}), saltando.")

cur.close()
conn.close()
print("Listo. Tablas existentes no fueron modificadas.")

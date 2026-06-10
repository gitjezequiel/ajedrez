"""
SOLO crea tablas NUEVAS e inserta datos NUEVOS.
No toca ninguna tabla existente.
"""
import mysql.connector

conn = mysql.connector.connect(host='localhost', user='root', password='', database='chess_enigma')
cur = conn.cursor()

# ── Crear tablas nuevas (IF NOT EXISTS = no hace nada si ya existen) ────────

cur.execute("""
CREATE TABLE IF NOT EXISTS practica_sets (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    slug        VARCHAR(100) NOT NULL UNIQUE,
    titulo      VARCHAR(200) NOT NULL,
    descripcion TEXT,
    study_id    VARCHAR(20),
    orden       INT DEFAULT 0
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS practica_patrones (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    set_id              INT NOT NULL,
    num                 INT NOT NULL,
    nombre              VARCHAR(100) NOT NULL,
    capitulo_teoria     VARCHAR(20),
    capitulo_ejercicio  VARCHAR(20),
    orden               INT DEFAULT 0,
    FOREIGN KEY (set_id) REFERENCES practica_sets(id) ON DELETE CASCADE
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS practica_progreso (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    patron_id   INT NOT NULL,
    completado  TINYINT(1) DEFAULT 1,
    UNIQUE KEY uk_patron (patron_id),
    FOREIGN KEY (patron_id) REFERENCES practica_patrones(id) ON DELETE CASCADE
)
""")

conn.commit()
print("Tablas creadas.")

# ── Insertar set (solo si no existe) ────────────────────────────────────────

cur.execute("SELECT id FROM practica_sets WHERE slug = %s", ('mates-30',))
row = cur.fetchone()
if row:
    set_id = row[0]
    print(f"Set ya existe (id={set_id}), saltando.")
else:
    cur.execute("""
        INSERT INTO practica_sets (slug, titulo, descripcion, study_id, orden)
        VALUES (%s, %s, %s, %s, %s)
    """, (
        'mates-30',
        '30 Patrones de Jaque Mate',
        'Aprende y practica los 30 patrones de jaque mate más importantes con Chess-Enigma.',
        'uYSaJEgT',
        1
    ))
    conn.commit()
    set_id = cur.lastrowid
    print(f"Set insertado (id={set_id}).")

# ── Insertar patrones (solo si no existen) ───────────────────────────────────

PATRONES = [
    ( 1, 'Anastasia',             'M2KYXpQ9', 'ywyheGP5'),
    ( 2, 'Anderssen',             '7kYfC10H', 'AehBI4fY'),
    ( 3, 'Árabe',                 'bhuDBN53',  'nQgt4DWv'),
    ( 4, 'Asfixia',               'd7pIqPDT',  'nZLZZ9bt'),
    ( 5, 'Ballesta',              'b6bNcQBH',  'IVkari5P'),
    ( 6, 'Barrido',               'OfAbNQU5',  '5JUB0SGz'),
    ( 7, 'Blackburne',            'iRCP9NO0',  'EEssNhsu'),
    ( 8, 'Boden',                 'B7T4ng0O',  '3oTrwRQT'),
    ( 9, 'Caja de la muerte',     'e8j9Ipio',  'Ir1ZCwfN'),
    (10, 'Callejón de la muerte', 'JKMi9LC2',  'rmxhFHVV'),
    (11, 'Cerdo ciego',           'oih5ketA',  'zpZgyfDK'),
    (12, 'Cola de golondrina',    'pdTcBDKW',  'R8nbaQey'),
    (13, 'Coz',                   'DtmIXz6n',  'Y3TmhJFP'),
    (14, 'Cozio',                 'vrBgHOmq',  'pj4vNXLL'),
    (15, 'Damiano',               'wrmNklaQ',  'liZ8u4vp'),
    (16, 'David y Goliat',        'ExZTdSYd',  'MUgbEtO2'),
    (17, 'Diagonal de la muerte', '0VgfRmcG',  'BVmrxXNJ'),
    (18, 'Escalera',              'baRUtnpa',  'cIyHjR6A'),
    (19, 'Gancho',                'rkMV5pLU',  'q9VAkmXe'),
    (20, 'Greco',                 'MzoTCwaq',  '7NdHiCKQ'),
    (21, 'Hombreras',             'MVZiljGR',  'xH6FJFqr'),
    (22, 'Lolli',                 '6v50IXEc',  'fSMA8TEV'),
    (23, 'Max Lange',             'HGscutTU',  'DeVG2dgN'),
    (24, 'Mayet',                 'y4MQWEMX',  'fV7IQnM6'),
    (25, 'Morphy',                'bddpPDf6',  'VBs1uEtG'),
    (26, 'Ópera',                 'muBdcddu',  'J7UdlnM0'),
    (27, 'Pasillo',               '1laSrHgj',  'k1du74Un'),
    (28, 'Pillsbury',             'm4OLrHvS',  'C59XdDuq'),
    (29, 'Triángulo',             'jFrW3d0Y',  'yE9vHX4U'),
    (30, 'Vukovic',               'IXbbaK1T',  '8gy1Tf1v'),
]

cur.execute("SELECT COUNT(*) FROM practica_patrones WHERE set_id = %s", (set_id,))
count = cur.fetchone()[0]
if count == 0:
    cur.executemany("""
        INSERT INTO practica_patrones (set_id, num, nombre, capitulo_teoria, capitulo_ejercicio, orden)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, [(set_id, num, nombre, teoria, ejercicio, num)
          for num, nombre, teoria, ejercicio in PATRONES])
    conn.commit()
    print(f"Insertados {len(PATRONES)} patrones.")
else:
    print(f"Patrones ya existen ({count}), saltando.")

cur.close()
conn.close()
print("Listo. Tablas existentes no fueron modificadas.")

import mysql.connector

conn = mysql.connector.connect(host='localhost', user='root', password='', database='chess_enigma')
cur = conn.cursor()

# ── 1. Crear tablas separadas para libros ────────────────────────────────────
cur.execute("""
CREATE TABLE IF NOT EXISTS libros (
    id VARCHAR(100) PRIMARY KEY,
    nombre VARCHAR(255) NOT NULL,
    descripcion TEXT,
    orden INT DEFAULT 0
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS libros_secciones (
    id VARCHAR(100) PRIMARY KEY,
    libro_id VARCHAR(100) NOT NULL,
    nombre VARCHAR(255) NOT NULL,
    orden INT DEFAULT 0,
    FOREIGN KEY (libro_id) REFERENCES libros(id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS libros_lecciones (
    id VARCHAR(100) PRIMARY KEY,
    seccion_id VARCHAR(100) NOT NULL,
    titulo VARCHAR(255) NOT NULL,
    subtitulo VARCHAR(255),
    api VARCHAR(50),
    study_id VARCHAR(100),
    chapter_index INT,
    orden INT DEFAULT 0,
    FOREIGN KEY (seccion_id) REFERENCES libros_secciones(id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS libros_sublecciones (
    pk INT AUTO_INCREMENT PRIMARY KEY,
    id VARCHAR(100) NOT NULL,
    leccion_id VARCHAR(100) NOT NULL,
    study_id VARCHAR(100),
    api VARCHAR(50) DEFAULT 'lichess',
    titulo VARCHAR(255) NOT NULL,
    orden INT DEFAULT 0,
    FOREIGN KEY (leccion_id) REFERENCES libros_lecciones(id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
""")
print("✓ Tablas creadas")

# ── 2. Copiar datos desde tablas actuales ────────────────────────────────────
cur.execute("SELECT COUNT(*) FROM cursos WHERE id = 'libros'")
if cur.fetchone()[0] == 0:
    print("✗ No se encontró entrada con id='libros' en la tabla cursos")
    conn.close()
    exit(1)

cur.execute("""
    INSERT IGNORE INTO libros (id, nombre, descripcion, orden)
    SELECT id, nombre, descripcion, orden FROM cursos WHERE id = 'libros'
""")
print(f"  libros: {cur.rowcount} fila(s)")

cur.execute("""
    INSERT IGNORE INTO libros_secciones (id, libro_id, nombre, orden)
    SELECT id, curso_id, nombre, orden FROM secciones WHERE curso_id = 'libros'
""")
n_secs = cur.rowcount
print(f"  libros_secciones: {n_secs} fila(s)")

cur.execute("""
    INSERT IGNORE INTO libros_lecciones (id, seccion_id, titulo, subtitulo, api, study_id, chapter_index, orden)
    SELECT l.id, l.seccion_id, l.titulo, l.subtitulo, l.api, l.study_id, l.chapter_index, l.orden
    FROM lecciones l
    WHERE l.seccion_id IN (SELECT id FROM secciones WHERE curso_id = 'libros')
""")
n_lecs = cur.rowcount
print(f"  libros_lecciones: {n_lecs} fila(s)")

cur.execute("""
    INSERT IGNORE INTO libros_sublecciones (id, leccion_id, study_id, api, titulo, orden)
    SELECT s.id, s.leccion_id, s.study_id, s.api, s.titulo, s.orden
    FROM sublecciones s
    WHERE s.leccion_id IN (
        SELECT l.id FROM lecciones l
        WHERE l.seccion_id IN (SELECT id FROM secciones WHERE curso_id = 'libros')
    )
""")
n_subs = cur.rowcount
print(f"  libros_sublecciones: {n_subs} fila(s)")

conn.commit()
print("✓ Datos copiados")

# ── 3. Eliminar libros de las tablas originales ──────────────────────────────
cur.execute("""
    DELETE FROM sublecciones
    WHERE leccion_id IN (
        SELECT id FROM lecciones
        WHERE seccion_id IN (SELECT id FROM secciones WHERE curso_id = 'libros')
    )
""")
print(f"  sublecciones eliminadas: {cur.rowcount}")

cur.execute("DELETE FROM lecciones WHERE seccion_id IN (SELECT id FROM secciones WHERE curso_id = 'libros')")
print(f"  lecciones eliminadas: {cur.rowcount}")

cur.execute("DELETE FROM secciones WHERE curso_id = 'libros'")
print(f"  secciones eliminadas: {cur.rowcount}")

cur.execute("DELETE FROM cursos WHERE id = 'libros'")
print(f"  cursos eliminadas: {cur.rowcount}")

conn.commit()
print("✓ Datos originales eliminados")

cur.close()
conn.close()
print("\n✓ Migración completada")

import os
import re
import subprocess
import glob
import json
import io
import base64
import chess
import PIL.Image
import mysql.connector
from datetime import date, timedelta
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder='.')
CORS(app)

STOCKFISH_PATH = "C:/laragon/stockfish/stockfish-windows-x86-64-avx2.exe"

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/health')
def health():
    return jsonify({"status": "ok", "message": "Chess Assistant Backend is running"})

def find_stockfish():
    if os.path.exists(STOCKFISH_PATH):
        return STOCKFISH_PATH
    folder = os.path.dirname(STOCKFISH_PATH)
    exes = glob.glob(os.path.join(folder, "*.exe"))
    return exes[0] if exes else None

def decode_image(image_base64):
    img_data = image_base64.split(',')[1] if image_base64.startswith('data:') else image_base64
    return PIL.Image.open(io.BytesIO(base64.b64decode(img_data)))

@app.route('/analyze', methods=['POST'])
def analyze():
    data = request.json
    fen = data.get('fen')

    if not fen:
        return jsonify({"errors": "FEN es requerido"}), 400

    try:
        chess.Board(fen)
    except ValueError:
        return jsonify({"errors": "FEN inválido"}), 400

    engine_path = find_stockfish()
    if not engine_path:
        return jsonify({"errors": "Motor Stockfish no encontrado"}), 500

    try:
        process = subprocess.Popen(
            engine_path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )

        def send_command(cmd):
            process.stdin.write(cmd + "\n")
            process.stdin.flush()

        send_command("uci")
        send_command("isready")

        while True:
            line = process.stdout.readline()
            if "readyok" in line:
                break

        level = data.get('level', 'normal')
        movetime_map = {'rapido': 1000, 'normal': 3000, 'profundo': 8000, 'maximo': 20000}
        movetime = movetime_map.get(level, 3000)

        send_command(f"position fen {fen}")
        send_command(f"go movetime {movetime}")

        analysis = {"bestmove": None, "depth": 0, "score_cp": 0, "pv": ""}

        while True:
            line = process.stdout.readline()
            if not line:
                break

            if line.startswith("info"):
                parts = line.split()
                if "depth" in parts:
                    analysis["depth"] = int(parts[parts.index("depth") + 1])
                if "score" in parts:
                    idx = parts.index("score")
                    if parts[idx + 1] == "cp":
                        analysis["score_cp"] = int(parts[idx + 2])
                    elif parts[idx + 1] == "mate":
                        analysis["score_cp"] = 10000 if int(parts[idx + 2]) > 0 else -10000
                if "pv" in parts:
                    pv_idx = parts.index("pv")
                    analysis["pv"] = " ".join(parts[pv_idx + 1:])

            if line.startswith("bestmove"):
                analysis["bestmove"] = line.split()[1]
                break

        process.terminate()
        return jsonify(analysis)

    except Exception as e:
        return jsonify({"errors": str(e)}), 500

import re

def normalize_fen(fen):
    """Intenta reparar FENs con campos faltantes."""
    parts = fen.strip().split()
    if len(parts) == 6:
        return fen  # ya completo
    if len(parts) == 5:
        # Detectar si falta castling o en passant
        # Un en passant válido es una casilla tipo a3, e6, etc.
        ep_pattern = re.compile(r'^[a-h][36]$')
        # Si parts[2] parece en passant, falta castling antes
        if ep_pattern.match(parts[2]) or parts[2] == '-':
            parts.insert(2, '-')  # insertar castling vacío
        else:
            # parts[2] es castling, falta en passant
            parts.insert(3, '-')
    elif len(parts) == 4:
        parts.insert(2, '-')
        parts.insert(3, '-')
    elif len(parts) == 3:
        parts += ['-', '0', '1']
    elif len(parts) == 2:
        parts += ['-', '-', '0', '1']
    # Asegurar halfmove y fullmove
    if len(parts) == 5:
        parts += ['1']
    return ' '.join(parts)


def validate_fen_placement(placement):
    """Verifica que cada fila tenga exactamente 8 casillas. Devuelve (ok, detalle_error)."""
    ranks = placement.split('/')
    if len(ranks) != 8:
        return False, f"Se esperaban 8 filas, hay {len(ranks)}"
    for i, rank in enumerate(ranks):
        count = sum(int(c) if c.isdigit() else 1 for c in rank)
        if count != 8:
            return False, f"Fila {8 - i} tiene {count} casillas en vez de 8 ('{rank}')"
    return True, None


PROMPT_FEN = (
    "Eres un experto en ajedrez. Analiza la imagen de este tablero y devuelve ÚNICAMENTE "
    "el FEN completo de 6 campos separados por espacios.\n"
    "Formato: <piezas> <turno> <enroque> <en_passant> <semijugadas> <jugada>\n"
    "Reglas IMPORTANTES:\n"
    "- Cada una de las 8 filas debe sumar exactamente 8 casillas. "
    "Cuenta con cuidado: una letra = 1 casilla, un dígito = N casillas.\n"
    "- Mayúsculas = piezas blancas (K Q R B N P), minúsculas = negras (k q r b n p).\n"
    "- Las blancas están abajo salvo que sea evidente lo contrario.\n"
    "- Si no hay enroque disponible usa '-'; si no hay en passant usa '-'.\n"
    "Ejemplo válido: rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1\n"
    "No escribas NADA más que el FEN. Si no puedes determinarlo, escribe exactamente: error"
)


@app.route('/image-to-fen', methods=['POST'])
def image_to_fen():
    data = request.json
    image_base64 = data.get('image_base64')
    api_key = data.get('api_key') or os.environ.get('GEMINI_API_KEY')

    if not image_base64:
        return jsonify({"errors": "Imagen requerida"}), 400
    if not api_key:
        return jsonify({"errors": "API Key de Gemini no configurada"}), 400

    try:
        client = genai.Client(api_key=api_key)
        img = decode_image(image_base64)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        image_part = types.Part(inline_data=types.Blob(mime_type='image/png', data=buf.getvalue()))

        last_error = "No se pudo generar un FEN válido"

        for attempt in range(3):
            prompt = PROMPT_FEN if attempt == 0 else (
                f"{PROMPT_FEN}\n\nINTENTO ANTERIOR FALLIDO: '{last_error}'. "
                "Por favor cuenta las casillas de cada fila con mucho cuidado antes de responder."
            )

            try:
                gen_config = types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_budget=0)
                )
            except Exception:
                gen_config = None

            response = client.models.generate_content(
                model='gemini-2.5-flash',
                **({"config": gen_config} if gen_config else {}),
                contents=[types.Content(role='user', parts=[
                    types.Part(text=prompt),
                    image_part
                ])]
            )

            text = response.text.strip()
            # Strip markdown code fences (```fen ... ```)
            text = re.sub(r'```[^\n]*', '', text).strip()
            # Find first line that looks like a FEN placement (contains '/')
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            raw = next((l for l in lines if '/' in l and len(l) >= 15), lines[0] if lines else '')

            if raw.lower() == 'error':
                return jsonify({"errors": "No se pudo determinar la posición del tablero"}), 400

            fen = normalize_fen(raw)
            placement = fen.split()[0]
            ok, detail = validate_fen_placement(placement)

            if not ok:
                last_error = detail
                continue

            try:
                chess.Board(fen)
                return jsonify({"fen": fen})
            except ValueError as e:
                last_error = str(e)

        return jsonify({"errors": f"No se pudo generar un FEN válido tras 3 intentos: {last_error}"}), 400

    except Exception as e:
        return jsonify({"errors": str(e)}), 500


@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    fen = data.get('fen')
    analysis = data.get('analysis')
    messages = data.get('messages', [])
    image_base64 = data.get('image_base64')
    api_key = data.get('api_key') or os.environ.get('GEMINI_API_KEY')

    if not api_key:
        return jsonify({"errors": "API Key de Gemini no configurada"}), 400

    try:
        client = genai.Client(api_key=api_key)

        context_parts = ["Eres un entrenador de ajedrez experto. El usuario tiene aproximadamente 700 ELO. "
            "Tu trabajo es: 1) Explicar la mejor jugada en términos simples usando coordenadas del tablero, "
            "2) Analizar la línea principal en lenguaje sencillo, "
            "3) Dar consejos concretos adaptados a nivel 700 ELO, "
            "4) Identificar errores comunes en esa posición. "
            "Responde siempre en español."]

        if fen:
            context_parts.append(f"\nPosición actual (FEN): {fen}")
        if analysis:
            context_parts.append(f"\nÚltimo análisis de Stockfish:\n{json.dumps(analysis, indent=2)}")

        system_prompt = "\n".join(context_parts)

        if not messages:
            messages = [{"role": "user", "content": "Explica la posición actual."}]

        # Build Gemini history (all messages except the last)
        history_msgs = messages[:-1]
        current_msg = messages[-1]

        gemini_history = [
            types.Content(
                role=m['role'],
                parts=[types.Part(text=m['content'])]
            )
            for m in history_msgs
        ]

        current_parts = [types.Part(text=current_msg['content'])]
        if image_base64:
            img = decode_image(image_base64)
            # Convert PIL image to bytes for Gemini
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            current_parts.append(types.Part(
                inline_data=types.Blob(
                    mime_type='image/png',
                    data=buf.getvalue()
                )
            ))

        gemini_history.append(types.Content(role='user', parts=current_parts))

        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=gemini_history,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.7
            )
        )

        return jsonify({"response": response.text})

    except Exception as e:
        return jsonify({"errors": str(e)}), 500

@app.route('/coach-report', methods=['POST'])
def coach_report():
    data = request.json
    fen          = data.get('fen', '')
    analysis     = data.get('analysis')
    moves        = data.get('moves', [])
    stats        = data.get('stats', {})
    api_key      = data.get('api_key') or os.environ.get('GEMINI_API_KEY')

    if not api_key:
        return jsonify({"errors": "API Key de Gemini no configurada"}), 400

    w = stats.get('w', {}); b = stats.get('b', {})
    w_avg = (w.get('totalLoss', 0) / w['moves']) if w.get('moves') else 0
    b_avg = (b.get('totalLoss', 0) / b['moves']) if b.get('moves') else 0

    moves_text = ', '.join(
        f"{m['notation']}({'★' if m['loss']==0 else '!!' if m['loss']<=10 else '?!' if m['loss']<=100 else '?' if m['loss']<=200 else '??'})"
        for m in moves
    ) if moves else 'Sin jugadas registradas'

    prompt = f"""Eres un entrenador de ajedrez analizando la sesión de un jugador de ~700 ELO.

POSICIÓN ACTUAL (FEN): {fen}
ANÁLISIS STOCKFISH: mejor jugada={analysis.get('bestmove','?') if analysis else '?'}, evaluación={analysis.get('score_cp',0)/100 if analysis else 0:+.2f}, profundidad={analysis.get('depth',0) if analysis else 0}
JUGADAS REGISTRADAS: {moves_text}
ESTADÍSTICAS:
  Blancas — {w.get('moves',0)} jugadas, pérdida promedio {w_avg:.0f} cp
  Negras  — {b.get('moves',0)} jugadas, pérdida promedio {b_avg:.0f} cp

Genera un INFORME DE ENTRENADOR detallado en español. Usa EXACTAMENTE estas secciones con estos encabezados:

## EVALUACIÓN GENERAL
## JUGADAS DESTACADAS
## ERRORES COMETIDOS
## PATRONES DETECTADOS
## PLAN DE MEJORA

Sé concreto: usa coordenadas de jugadas, explica con lenguaje simple para 700 ELO. Máximo 5 puntos por sección."""

    try:
        client = genai.Client(api_key=api_key)
        try:
            cfg = types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_budget=0),
                temperature=0.7
            )
        except Exception:
            cfg = types.GenerateContentConfig(temperature=0.7)

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[types.Content(role='user', parts=[types.Part(text=prompt)])],
            config=cfg
        )
        return jsonify({"report": response.text})
    except Exception as e:
        return jsonify({"errors": str(e)}), 500


def get_db():
    return mysql.connector.connect(host='localhost', user='root', password='', database='chess_enigma')

@app.route('/api/progreso')
def get_progreso():
    try:
        conn = get_db()
        cur  = conn.cursor(dictionary=True)
        cur.execute("SELECT leccion_id FROM progreso WHERE visto = 1")
        ids  = [r['leccion_id'] for r in cur.fetchall()]
        cur.close(); conn.close()
        return jsonify(ids)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/progreso/<leccion_id>', methods=['POST'])
def toggle_progreso(leccion_id):
    try:
        conn = get_db()
        cur  = conn.cursor(dictionary=True)
        cur.execute("SELECT visto FROM progreso WHERE leccion_id = %s", (leccion_id,))
        row  = cur.fetchone()
        if row:
            nuevo = 0 if row['visto'] else 1
            cur.execute("UPDATE progreso SET visto = %s WHERE leccion_id = %s", (nuevo, leccion_id))
        else:
            nuevo = 1
            cur.execute("INSERT INTO progreso (leccion_id, visto) VALUES (%s, 1)", (leccion_id,))
        conn.commit()
        cur.close(); conn.close()
        return jsonify({'visto': bool(nuevo)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/catalogo')
def api_catalogo():
    try:
        conn = get_db()
        cur  = conn.cursor(dictionary=True)
        cur.execute("SELECT id, nombre, descripcion FROM cursos WHERE tipo='cursara' ORDER BY orden")
        cursos = cur.fetchall()
        cur.execute("""
            SELECT id, curso_id, nombre FROM secciones
            WHERE curso_id IN (SELECT id FROM cursos WHERE tipo='cursara') ORDER BY orden
        """)
        secciones = cur.fetchall()
        cur.execute("""
            SELECT id, seccion_id, titulo, subtitulo, api, drive_file_id, video_url FROM lecciones
            WHERE seccion_id IN (
                SELECT id FROM secciones WHERE curso_id IN (SELECT id FROM cursos WHERE tipo='cursara')
            ) ORDER BY orden
        """)
        lecciones = cur.fetchall()
        cur.close(); conn.close()

        lecs_by_sec = {}
        for l in lecciones:
            lecs_by_sec.setdefault(l['seccion_id'], []).append({
                'id': l['id'], 'titulo': l['titulo'], 'subtitulo': l['subtitulo'],
                'api': l['api'], 'drive_file_id': l['drive_file_id'], 'video_url': l['video_url']
            })
        secs_by_curso = {}
        for s in secciones:
            secs_by_curso.setdefault(s['curso_id'], []).append({
                'id': s['id'], 'nombre': s['nombre'],
                'lecciones': lecs_by_sec.get(s['id'], [])
            })
        result = [{'id': c['id'], 'nombre': c['nombre'], 'descripcion': c['descripcion'],
                   'secciones': secs_by_curso.get(c['id'], [])} for c in cursos]
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/notas/<leccion_id>')
def get_notas(leccion_id):
    try:
        conn = get_db()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, exercise_index, titulo, color, nota FROM notas WHERE leccion_id = %s ORDER BY id", (leccion_id,))
        rows = cur.fetchall()
        cur.close(); conn.close()
        result = {}
        for r in rows:
            idx = r['exercise_index']
            result.setdefault(idx, []).append({'id': r['id'], 'titulo': r['titulo'], 'color': r['color'], 'nota': r['nota']})
        return jsonify(result)
    except Exception as e:
        return jsonify({"errors": str(e)}), 500

@app.route('/api/notas', methods=['POST'])
def create_nota():
    try:
        data           = request.json
        leccion_id     = data.get('leccion_id')
        exercise_index = data.get('exercise_index')
        titulo         = data.get('titulo', '').strip()
        color          = data.get('color', '#d4a800')
        nota           = data.get('nota', '').strip()
        if not leccion_id or exercise_index is None:
            return jsonify({'error': 'Faltan campos'}), 400
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO notas (leccion_id, exercise_index, titulo, color, nota) VALUES (%s,%s,%s,%s,%s)",
                    (leccion_id, exercise_index, titulo, color, nota))
        conn.commit()
        new_id = cur.lastrowid
        cur.close(); conn.close()
        return jsonify({'ok': True, 'id': new_id})
    except Exception as e:
        return jsonify({"errors": str(e)}), 500

@app.route('/api/notas/<int:note_id>', methods=['PUT'])
def update_nota(note_id):
    try:
        data   = request.json
        titulo = data.get('titulo', '').strip()
        color  = data.get('color', '#d4a800')
        nota   = data.get('nota', '').strip()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE notas SET titulo=%s, color=%s, nota=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                    (titulo, color, nota, note_id))
        conn.commit()
        cur.close(); conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({"errors": str(e)}), 500

@app.route('/api/notas/<int:note_id>', methods=['DELETE'])
def delete_nota(note_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM notas WHERE id=%s", (note_id,))
        conn.commit()
        cur.close(); conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({"errors": str(e)}), 500

# ═══════════════════════════════════════════════════
#   LIBRETAS (Notas con secciones y sub-secciones)
# ═══════════════════════════════════════════════════

@app.route('/api/libretas', methods=['GET'])
def get_libretas():
    try:
        conn = get_db(); cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM libretas ORDER BY updated_at DESC")
        rows = cur.fetchall()
        for r in rows:
            r['created_at'] = r['created_at'].isoformat()
            r['updated_at'] = r['updated_at'].isoformat()
        cur.close(); conn.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/libretas', methods=['POST'])
def create_libreta():
    try:
        data = request.get_json()
        conn = get_db(); cur = conn.cursor(dictionary=True)
        cur.execute("INSERT INTO libretas (titulo, color) VALUES (%s,%s)",
                    (data.get('titulo', 'Nueva nota'), data.get('color', '#00b894')))
        conn.commit()
        lid = cur.lastrowid
        cur.execute("SELECT * FROM libretas WHERE id=%s", (lid,))
        row = cur.fetchone()
        row['created_at'] = row['created_at'].isoformat()
        row['updated_at'] = row['updated_at'].isoformat()
        cur.close(); conn.close()
        return jsonify(row), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/libretas/<int:lid>', methods=['PUT'])
def update_libreta(lid):
    try:
        data = request.get_json()
        conn = get_db(); cur = conn.cursor()
        cur.execute("UPDATE libretas SET titulo=%s, color=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                    (data.get('titulo'), data.get('color'), lid))
        conn.commit()
        cur.close(); conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/libretas/<int:lid>', methods=['DELETE'])
def delete_libreta(lid):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("DELETE FROM libretas WHERE id=%s", (lid,))
        conn.commit()
        cur.close(); conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/libretas/<int:lid>/secciones', methods=['GET'])
def get_lib_secciones(lid):
    try:
        conn = get_db(); cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM libretas_secciones WHERE libreta_id=%s ORDER BY orden, id", (lid,))
        rows = cur.fetchall()
        top = [r for r in rows if r['parent_id'] is None]
        for s in top:
            s['subs'] = [r for r in rows if r['parent_id'] == s['id']]
            for sub in s['subs']:
                sub['created_at'] = sub['created_at'].isoformat()
                sub['updated_at'] = sub['updated_at'].isoformat()
            s['created_at'] = s['created_at'].isoformat()
            s['updated_at'] = s['updated_at'].isoformat()
        cur.close(); conn.close()
        return jsonify(top)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/libretas/<int:lid>/secciones', methods=['POST'])
def add_lib_seccion(lid):
    try:
        data = request.get_json()
        conn = get_db(); cur = conn.cursor(dictionary=True)
        cur.execute("INSERT INTO libretas_secciones (libreta_id,parent_id,titulo,contenido,orden) VALUES (%s,%s,%s,%s,%s)",
                    (lid, data.get('parent_id'), data.get('titulo','Nueva sección'), data.get('contenido',''), data.get('orden',0)))
        conn.commit()
        sid = cur.lastrowid
        cur.execute("UPDATE libretas SET updated_at=CURRENT_TIMESTAMP WHERE id=%s", (lid,))
        conn.commit()
        cur.execute("SELECT * FROM libretas_secciones WHERE id=%s", (sid,))
        row = cur.fetchone()
        row['subs'] = []
        row['created_at'] = row['created_at'].isoformat()
        row['updated_at'] = row['updated_at'].isoformat()
        cur.close(); conn.close()
        return jsonify(row), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/lib_secciones/<int:sid>', methods=['PUT'])
def update_lib_seccion(sid):
    try:
        data = request.get_json()
        conn = get_db(); cur = conn.cursor()
        cur.execute("UPDATE libretas_secciones SET titulo=%s, contenido=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                    (data.get('titulo'), data.get('contenido',''), sid))
        cur.execute("""UPDATE libretas l JOIN libretas_secciones s ON s.libreta_id=l.id
                       SET l.updated_at=CURRENT_TIMESTAMP WHERE s.id=%s""", (sid,))
        conn.commit()
        cur.close(); conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/lib_secciones/<int:sid>', methods=['DELETE'])
def delete_lib_seccion(sid):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("DELETE FROM libretas_secciones WHERE id=%s OR parent_id=%s", (sid, sid))
        conn.commit()
        cur.close(); conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/cursos')
def api_cursos():
    try:
        conn = get_db()
        cur = conn.cursor(dictionary=True)

        cur.execute("SELECT id, nombre, descripcion FROM cursos ORDER BY orden")
        cursos = cur.fetchall()

        cur.execute("SELECT id, curso_id, nombre FROM secciones ORDER BY orden")
        secciones = cur.fetchall()

        cur.execute("SELECT id, seccion_id, titulo, subtitulo, api, study_id, chapter_index FROM lecciones ORDER BY orden")
        lecciones = cur.fetchall()

        cur.execute("SELECT id, leccion_id, study_id, api, titulo FROM sublecciones ORDER BY orden")
        sublecciones = cur.fetchall()

        cur.close()
        conn.close()

        subs_by_lec = {}
        for s in sublecciones:
            subs_by_lec.setdefault(s['leccion_id'], []).append({
                'id': s['id'],
                'studyId': s['study_id'],
                'api': s['api'],
                'titulo': s['titulo'],
            })

        lecs_by_sec = {}
        for l in lecciones:
            obj = {'id': l['id'], 'titulo': l['titulo']}
            if l['subtitulo']:
                obj['subtitulo'] = l['subtitulo']
            if l['api']:
                obj['api'] = l['api']
            if l['study_id']:
                obj['studyId'] = l['study_id']
            if l['chapter_index'] is not None:
                obj['chapterIndex'] = l['chapter_index']
            subs = subs_by_lec.get(l['id'])
            if subs:
                obj['sublecciones'] = subs
            lecs_by_sec.setdefault(l['seccion_id'], []).append(obj)

        secs_by_curso = {}
        for s in secciones:
            secs_by_curso.setdefault(s['curso_id'], []).append({
                'id': s['id'],
                'nombre': s['nombre'],
                'lecciones': lecs_by_sec.get(s['id'], []),
            })

        result = []
        for c in cursos:
            result.append({
                'id': c['id'],
                'nombre': c['nombre'],
                'descripcion': c['descripcion'] or '',
                'secciones': secs_by_curso.get(c['id'], []),
            })

        return jsonify(result)

    except Exception as e:
        return jsonify({"errors": str(e)}), 500


@app.route('/api/libros')
def api_libros():
    try:
        conn = get_db()
        cur = conn.cursor(dictionary=True)

        cur.execute("SELECT id, nombre, descripcion FROM libros ORDER BY orden")
        libros = cur.fetchall()

        cur.execute("SELECT id, libro_id, nombre FROM libros_secciones ORDER BY orden")
        secciones = cur.fetchall()

        cur.execute("SELECT id, seccion_id, titulo, subtitulo, api, study_id, chapter_index FROM libros_lecciones ORDER BY orden")
        lecciones = cur.fetchall()

        cur.execute("SELECT id, leccion_id, study_id, api, titulo FROM libros_sublecciones ORDER BY orden")
        sublecciones = cur.fetchall()

        cur.close()
        conn.close()

        subs_by_lec = {}
        for s in sublecciones:
            subs_by_lec.setdefault(s['leccion_id'], []).append({
                'id': s['id'],
                'studyId': s['study_id'],
                'api': s['api'],
                'titulo': s['titulo'],
            })

        lecs_by_sec = {}
        for l in lecciones:
            obj = {'id': l['id'], 'titulo': l['titulo']}
            if l['subtitulo']:
                obj['subtitulo'] = l['subtitulo']
            if l['api']:
                obj['api'] = l['api']
            if l['study_id']:
                obj['studyId'] = l['study_id']
            if l['chapter_index'] is not None:
                obj['chapterIndex'] = l['chapter_index']
            subs = subs_by_lec.get(l['id'])
            if subs:
                obj['sublecciones'] = subs
            lecs_by_sec.setdefault(l['seccion_id'], []).append(obj)

        secs_by_libro = {}
        for s in secciones:
            secs_by_libro.setdefault(s['libro_id'], []).append({
                'id': s['id'],
                'nombre': s['nombre'],
                'lecciones': lecs_by_sec.get(s['id'], []),
            })

        result = []
        for l in libros:
            result.append({
                'id': l['id'],
                'nombre': l['nombre'],
                'descripcion': l['descripcion'] or '',
                'secciones': secs_by_libro.get(l['id'], []),
            })

        return jsonify(result)

    except Exception as e:
        return jsonify({"errors": str(e)}), 500


@app.route('/api/practicas/talleres')
def api_practicas_talleres():
    try:
        conn = get_db(); cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, nombre FROM practica_grupos ORDER BY orden")
        grupos = cur.fetchall()
        result = []
        for g in grupos:
            cur.execute("""SELECT id, slug, titulo, descripcion, orden
                           FROM practica_talleres WHERE grupo_id=%s ORDER BY orden""", (g['id'],))
            talleres = cur.fetchall()
            for t in talleres:
                cur.execute("""SELECT num, titulo, study_id, chapter_id
                               FROM practica_lecciones WHERE taller_id=%s ORDER BY orden""", (t['id'],))
                t['lecciones'] = cur.fetchall()
                t['grupo_nombre'] = g['nombre']
                t['grupo_id']    = g['id']
            result.extend(talleres)
        cur.close(); conn.close()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/proxy/study/<study_id>')
def proxy_study(study_id):
    import urllib.request
    url = f'https://test.chessenigma.com/api/v1/studies/{study_id}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    return jsonify(data)

@app.route('/proxy/study-prod/<study_id>')
def proxy_study_prod(study_id):
    import urllib.request
    url = f'https://api.chessenigma.com/api/v1/studies/{study_id}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    return jsonify(data)

@app.route('/api/lichess-study/<study_id>/chapters')
def lichess_study_chapters(study_id):
    import urllib.request
    url = f'https://lichess.org/study/{study_id}.pgn'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0',
        'Accept': 'application/x-chess-pgn'
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            pgn = resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        return jsonify({'error': str(e)}), 502
    chapters = []
    chapter_name = None
    for line in pgn.splitlines():
        line = line.strip()
        if line.startswith('[ChapterName "'):
            chapter_name = line[14:-2]
        elif line.startswith('[Event "') and chapter_name is None:
            chapter_name = line[8:-2]
        elif line.startswith('[ChapterURL "'):
            chapter_url = line[13:-2]
            chapter_id = chapter_url.rstrip('/').split('/')[-1]
            if len(chapter_id) >= 6:
                chapters.append({
                    'num':    len(chapters) + 1,
                    'id':     chapter_id,
                    'titulo': chapter_name or f'Capítulo {len(chapters) + 1}'
                })
                chapter_name = None
    return jsonify(chapters)

@app.route('/api/practicas/grupos/<int:grupo_id>/talleres', methods=['POST'])
def crear_taller_en_grupo(grupo_id):
    data = request.json or {}
    nombre   = data.get('nombre', '').strip()
    lecciones = data.get('lecciones', [])
    if not nombre:
        return jsonify({'error': 'nombre requerido'}), 400
    slug = re.sub(r'[^a-z0-9]+', '-', nombre.lower()).strip('-')
    conn = get_db(); cur = conn.cursor(dictionary=True)
    base_slug, suffix = slug, 1
    while True:
        cur.execute("SELECT id FROM practica_talleres WHERE slug=%s", (slug,))
        if not cur.fetchone(): break
        slug = f"{base_slug}-{suffix}"; suffix += 1
    cur.execute("SELECT COALESCE(MAX(orden),0)+1 AS n FROM practica_talleres WHERE grupo_id=%s", (grupo_id,))
    orden = cur.fetchone()['n']
    cur.execute("INSERT INTO practica_talleres (grupo_id, slug, titulo, descripcion, orden) VALUES (%s,%s,%s,%s,%s)",
                (grupo_id, slug, nombre, '', orden))
    conn.commit()
    taller_id = cur.lastrowid
    for i, lec in enumerate(lecciones):
        cur.execute("INSERT INTO practica_lecciones (taller_id, num, titulo, study_id, chapter_id, orden) VALUES (%s,%s,%s,%s,%s,%s)",
                    (taller_id, i+1, lec['titulo'], lec['study_id'], lec['chapter_id'], i+1))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'id': taller_id, 'slug': slug})

@app.route('/api/practicas/talleres/<int:taller_id>/lecciones', methods=['POST'])
def agregar_lecciones_a_taller(taller_id):
    lecciones = (request.json or {}).get('lecciones', [])
    if not lecciones:
        return jsonify({'error': 'lecciones requeridas'}), 400
    conn = get_db(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT COALESCE(MAX(num),0) AS mn, COALESCE(MAX(orden),0) AS mo FROM practica_lecciones WHERE taller_id=%s", (taller_id,))
    row = cur.fetchone()
    base_num, base_ord = row['mn'], row['mo']
    for i, lec in enumerate(lecciones):
        cur.execute("INSERT INTO practica_lecciones (taller_id, num, titulo, study_id, chapter_id, orden) VALUES (%s,%s,%s,%s,%s,%s)",
                    (taller_id, base_num+i+1, lec['titulo'], lec['study_id'], lec['chapter_id'], base_ord+i+1))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'added': len(lecciones)})

def _pgn_cache_get(ref_key):
    try:
        conn = get_db(); cur = conn.cursor(dictionary=True)
        cur.execute("SELECT pgn FROM pgn_cache WHERE ref_key=%s", (ref_key,))
        row = cur.fetchone()
        cur.close(); conn.close()
        return row['pgn'] if row else None
    except Exception:
        return None

def _pgn_cache_set(ref_key, pgn):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""INSERT INTO pgn_cache (ref_key, pgn) VALUES (%s, %s)
                       ON DUPLICATE KEY UPDATE pgn=%s, updated_at=CURRENT_TIMESTAMP""",
                    (ref_key, pgn, pgn))
        conn.commit(); cur.close(); conn.close()
    except Exception:
        pass

@app.route('/proxy/lichess-study/<study_id>')
def proxy_lichess_study(study_id):
    import urllib.request
    key = f'study:{study_id}'
    cached = _pgn_cache_get(key)
    if cached:
        return jsonify({'data': {'pgn': cached}})
    url = f'https://lichess.org/study/{study_id}.pgn'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/x-chess-pgn'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        pgn = resp.read().decode('utf-8')
    _pgn_cache_set(key, pgn)
    return jsonify({'data': {'pgn': pgn}})

@app.route('/proxy/lichess-chapter/<study_id>/<chapter_id>')
def proxy_lichess_chapter(study_id, chapter_id):
    import urllib.request
    key = f'chapter:{study_id}/{chapter_id}'
    cached = _pgn_cache_get(key)
    if cached:
        return jsonify({'data': {'pgn': cached}})
    url = f'https://lichess.org/study/{study_id}/{chapter_id}.pgn'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/x-chess-pgn'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        pgn = resp.read().decode('utf-8')
    _pgn_cache_set(key, pgn)
    return jsonify({'data': {'pgn': pgn}})

@app.route('/api/pgn-cache/<path:ref_key>', methods=['DELETE'])
def pgn_cache_delete(ref_key):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("DELETE FROM pgn_cache WHERE ref_key=%s", (ref_key,))
        conn.commit(); cur.close(); conn.close()
        return jsonify({'deleted': cur.rowcount > 0})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =====================================================================
# TRACKER — Mi Año de 12 Semanas
# =====================================================================

def _jd(obj):
    """Convierte date objects a ISO strings para JSON (recursivo)."""
    if isinstance(obj, date):   return obj.isoformat()
    if isinstance(obj, dict):   return {k: _jd(v) for k, v in obj.items()}
    if isinstance(obj, list):   return [_jd(x) for x in obj]
    return obj

def _ciclo_actual(cur, ciclo_id=None):
    if ciclo_id:
        cur.execute("SELECT * FROM ciclos WHERE id=%s", (ciclo_id,))
    else:
        cur.execute("SELECT * FROM ciclos ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    if row:
        row['semanas_totales'] = max(1, ((row['fecha_fin'] - row['fecha_inicio']).days + 6) // 7)
    return row

def _week_score(cur, ciclo_id, fi, n):
    start = fi + timedelta(days=(n-1)*7)
    end   = fi + timedelta(days=n*7 - 1)
    cur.execute("""
        SELECT t.frecuencia_semanal, COUNT(e.id) as c
        FROM tacticas t
        JOIN metas m ON t.meta_id = m.id
        LEFT JOIN ejecuciones e ON e.tactica_id = t.id AND e.fecha BETWEEN %s AND %s
        WHERE m.ciclo_id = %s AND m.activa = 1
        GROUP BY t.id, t.frecuencia_semanal
    """, (start, end, ciclo_id))
    rows = cur.fetchall()
    if not rows: return 0.0
    esp   = sum(r['frecuencia_semanal'] for r in rows)
    hecho = sum(min(r['c'], r['frecuencia_semanal']) for r in rows)
    return round(hecho / esp * 100, 2) if esp else 0.0

def _streak(cur):
    hoy = date.today()
    streak, d = 0, hoy
    for _ in range(365):
        cur.execute("SELECT COUNT(*) as c FROM ejecuciones WHERE fecha = %s", (d,))
        if cur.fetchone()['c'] == 0: break
        streak += 1
        d -= timedelta(days=1)
    return streak

def _seed_tracker(cur, conn):
    cur.execute("""INSERT INTO ciclos (nombre, fecha_inicio, fecha_fin, vision) VALUES (%s,%s,%s,%s)""",
        ('Primer Año de 12 Semanas', '2026-06-10', '2026-09-01',
         'Certificado CCSA en mano + base sólida de ajedrez + Nahual avanzando'))
    conn.commit()
    cid = cur.lastrowid

    def meta(nombre, nivel, resultado, flim=None):
        cur.execute("INSERT INTO metas (ciclo_id,nombre,nivel,resultado_medible,fecha_limite) VALUES (%s,%s,%s,%s,%s)",
                    (cid, nombre, nivel, resultado, flim))
        conn.commit()
        return cur.lastrowid

    def tacs(mid, lst):
        cur.executemany("INSERT INTO tacticas (meta_id,descripcion,frecuencia_semanal) VALUES (%s,%s,%s)",
                        [(mid, d, f) for d, f in lst])
        conn.commit()

    m1 = meta('Certificación CCSA R82', 'compromiso',
               'Aprobar examen el 15 de julio 2026', '2026-07-15')
    tacs(m1, [('Sesión de estudio CCSA 1 hora', 4), ('Simulacro de examen', 1)])

    m2 = meta('Ajedrez 700→850 ELO', 'compromiso',
               'Llegar a 850 ELO y terminar 10 partidas de Logical Chess')
    tacs(m2, [('Puzzles diarios 15-20 min', 6), ('Partida de Chernev estudiada', 2),
               ('Clase de ajedrez + repaso de apuntes', 1), ('Partida propia analizada', 1)])

    m3 = meta('Nahual: Espíritu Protector', 'coccion', '12 capítulos revisados y publicados')
    tacs(m3, [('Capítulo revisado y publicado', 1)])

def init_tracker_db():
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("""CREATE TABLE IF NOT EXISTS ciclos (
        id INT AUTO_INCREMENT PRIMARY KEY,
        nombre VARCHAR(200), fecha_inicio DATE, fecha_fin DATE, vision TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS metas (
        id INT AUTO_INCREMENT PRIMARY KEY,
        ciclo_id INT, nombre VARCHAR(200), descripcion TEXT,
        nivel ENUM('compromiso','coccion','hobby'),
        resultado_medible TEXT, fecha_limite DATE NULL,
        activa BOOLEAN DEFAULT TRUE,
        FOREIGN KEY (ciclo_id) REFERENCES ciclos(id)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS tacticas (
        id INT AUTO_INCREMENT PRIMARY KEY,
        meta_id INT, descripcion VARCHAR(300), frecuencia_semanal INT,
        FOREIGN KEY (meta_id) REFERENCES metas(id)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS ejecuciones (
        id INT AUTO_INCREMENT PRIMARY KEY,
        tactica_id INT, fecha DATE, completada BOOLEAN DEFAULT TRUE,
        UNIQUE KEY uk_tac_fecha (tactica_id, fecha),
        FOREIGN KEY (tactica_id) REFERENCES tacticas(id)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS revision_semanal (
        id INT AUTO_INCREMENT PRIMARY KEY,
        ciclo_id INT, numero_semana INT,
        puntaje DECIMAL(5,2), notas TEXT,
        FOREIGN KEY (ciclo_id) REFERENCES ciclos(id)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS proximos (
        id INT AUTO_INCREMENT PRIMARY KEY,
        descripcion VARCHAR(300), fecha_creacion DATE
    )""")
    conn.commit()
    cur.execute("SELECT COUNT(*) as c FROM ciclos")
    if cur.fetchone()['c'] == 0:
        _seed_tracker(cur, conn)
    cur.close(); conn.close()

@app.route('/tracker/ciclos')
def tracker_lista_ciclos():
    try:
        conn = get_db(); cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, nombre, fecha_inicio, fecha_fin FROM ciclos ORDER BY id DESC")
        rows = cur.fetchall()
        for r in rows:
            r['fecha_inicio'] = r['fecha_inicio'].isoformat()
            r['fecha_fin']    = r['fecha_fin'].isoformat()
        cur.close(); conn.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/tracker/ciclo/actual')
def tracker_ciclo_actual():
    try:
        conn = get_db(); cur = conn.cursor(dictionary=True)
        ciclo = _ciclo_actual(cur)
        if not ciclo: return jsonify(None)
        cur.execute("SELECT * FROM metas WHERE ciclo_id=%s AND activa=1 ORDER BY id", (ciclo['id'],))
        metas = cur.fetchall()
        for m in metas:
            cur.execute("SELECT id,descripcion,frecuencia_semanal FROM tacticas WHERE meta_id=%s ORDER BY id", (m['id'],))
            m['tacticas'] = cur.fetchall()
        ciclo['metas'] = metas
        cur.close(); conn.close()
        return jsonify(_jd(ciclo))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/tracker/ciclo', methods=['POST'])
def tracker_crear_ciclo():
    try:
        d = request.json; conn = get_db(); cur = conn.cursor(dictionary=True)
        cur.execute("INSERT INTO ciclos (nombre,fecha_inicio,fecha_fin,vision) VALUES (%s,%s,%s,%s)",
                    (d['nombre'], d['fecha_inicio'], d['fecha_fin'], d.get('vision','')))
        conn.commit(); cid = cur.lastrowid; cur.close(); conn.close()
        return jsonify({'id': cid})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/tracker/meta', methods=['POST'])
def tracker_crear_meta():
    try:
        d = request.json; conn = get_db(); cur = conn.cursor(dictionary=True)
        cur.execute("INSERT INTO metas (ciclo_id,nombre,nivel,resultado_medible,fecha_limite) VALUES (%s,%s,%s,%s,%s)",
                    (d['ciclo_id'], d['nombre'], d['nivel'], d.get('resultado_medible',''), d.get('fecha_limite')))
        conn.commit(); mid = cur.lastrowid; cur.close(); conn.close()
        return jsonify({'id': mid})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/tracker/tactica', methods=['POST'])
def tracker_crear_tactica():
    try:
        d = request.json; conn = get_db(); cur = conn.cursor(dictionary=True)
        cur.execute("INSERT INTO tacticas (meta_id,descripcion,frecuencia_semanal) VALUES (%s,%s,%s)",
                    (d['meta_id'], d['descripcion'], d['frecuencia_semanal']))
        conn.commit(); tid = cur.lastrowid; cur.close(); conn.close()
        return jsonify({'id': tid})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/tracker/ejecucion', methods=['POST'])
def tracker_ejecucion():
    try:
        d = request.json; tid, fecha = d['tactica_id'], d['fecha']
        conn = get_db(); cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id FROM ejecuciones WHERE tactica_id=%s AND fecha=%s", (tid, fecha))
        row = cur.fetchone()
        if row:
            cur.execute("DELETE FROM ejecuciones WHERE id=%s", (row['id'],)); completada = False
        else:
            cur.execute("INSERT INTO ejecuciones (tactica_id,fecha) VALUES (%s,%s)", (tid, fecha)); completada = True
        conn.commit(); cur.close(); conn.close()
        return jsonify({'completada': completada})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/tracker/semana/<int:numero>')
def tracker_semana(numero):
    try:
        ciclo_id = request.args.get('ciclo_id', type=int)
        conn = get_db(); cur = conn.cursor(dictionary=True)
        ciclo = _ciclo_actual(cur, ciclo_id)
        if not ciclo: return jsonify({'error': 'Sin ciclo'}), 404
        fi    = ciclo['fecha_inicio']
        start = fi + timedelta(days=(numero-1)*7)
        end   = fi + timedelta(days=numero*7 - 1)
        dias  = [(start + timedelta(days=i)).isoformat() for i in range(7)]

        cur.execute("""
            SELECT t.id, t.descripcion, t.frecuencia_semanal,
                   m.id as meta_id, m.nombre as meta_nombre, m.nivel as meta_nivel
            FROM tacticas t JOIN metas m ON t.meta_id = m.id
            WHERE m.ciclo_id=%s AND m.activa=1 ORDER BY m.id, t.id
        """, (ciclo['id'],))
        tacs = cur.fetchall()

        cur.execute("""
            SELECT e.tactica_id, e.fecha FROM ejecuciones e
            JOIN tacticas t ON e.tactica_id = t.id
            JOIN metas m    ON t.meta_id    = m.id
            WHERE m.ciclo_id=%s AND e.fecha BETWEEN %s AND %s
        """, (ciclo['id'], start, end))
        exec_set = {(r['tactica_id'], r['fecha'].isoformat()) for r in cur.fetchall()}

        total_esp = total_hecho = 0
        result = []
        for t in tacs:
            ejs = [d for d in dias if (t['id'], d) in exec_set]
            c, f = len(ejs), t['frecuencia_semanal']
            total_esp += f; total_hecho += min(c, f)
            result.append({**t, 'completadas': c, 'ejecuciones': ejs})

        puntaje = round(total_hecho / total_esp * 100, 2) if total_esp else 0.0
        cur.close(); conn.close()
        return jsonify({'semana': numero, 'fecha_inicio': start.isoformat(),
                        'fecha_fin': end.isoformat(), 'dias': dias,
                        'puntaje': puntaje, 'tacticas': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/tracker/dashboard')
def tracker_dashboard():
    try:
        ciclo_id = request.args.get('ciclo_id', type=int)
        conn = get_db(); cur = conn.cursor(dictionary=True)
        ciclo = _ciclo_actual(cur, ciclo_id)
        if not ciclo: return jsonify({'error': 'Sin ciclo'}), 404
        fi, ff = ciclo['fecha_inicio'], ciclo['fecha_fin']
        hoy = date.today()
        semana_actual = max(1, min(12, (hoy - fi).days // 7 + 1))
        dias_ciclo    = max(0, (ff - hoy).days)

        cur.execute("""SELECT nombre, fecha_limite FROM metas
            WHERE ciclo_id=%s AND activa=1 AND fecha_limite IS NOT NULL
            ORDER BY fecha_limite LIMIT 1""", (ciclo['id'],))
        dl = cur.fetchone()
        dias_deadline = max(0, (dl['fecha_limite'] - hoy).days) if dl else None
        meta_deadline = dl['nombre'] if dl else None

        puntaje_semana = _week_score(cur, ciclo['id'], fi, semana_actual)
        scores   = [_week_score(cur, ciclo['id'], fi, w) for w in range(1, semana_actual + 1)]
        promedio = round(sum(scores) / len(scores), 2) if scores else 0.0
        racha    = _streak(cur)
        cur.close(); conn.close()
        return jsonify({
            'ciclo_id': ciclo['id'],
            'semana_actual': semana_actual, 'total_semanas': ciclo['semanas_totales'],
            'dias_restantes_ciclo': dias_ciclo,
            'dias_para_deadline': dias_deadline,
            'meta_deadline_nombre': meta_deadline,
            'puntaje_semana': puntaje_semana,
            'puntaje_promedio': promedio,
            'racha': racha,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/tracker/proximos', methods=['GET'])
def tracker_proximos_get():
    try:
        conn = get_db(); cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, descripcion, fecha_creacion FROM proximos ORDER BY id DESC")
        rows = cur.fetchall(); hoy = date.today()
        for r in rows:
            r['dias'] = (hoy - r['fecha_creacion']).days
            r['fecha_creacion'] = r['fecha_creacion'].isoformat()
        cur.close(); conn.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/tracker/proximos', methods=['POST'])
def tracker_proximos_add():
    try:
        d = request.json; conn = get_db(); cur = conn.cursor(dictionary=True)
        cur.execute("INSERT INTO proximos (descripcion,fecha_creacion) VALUES (%s,%s)",
                    (d['descripcion'], date.today().isoformat()))
        conn.commit(); pid = cur.lastrowid; cur.close(); conn.close()
        return jsonify({'id': pid})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/tracker/proximos/<int:pid>', methods=['DELETE'])
def tracker_proximos_del(pid):
    try:
        conn = get_db(); cur = conn.cursor(dictionary=True)
        cur.execute("DELETE FROM proximos WHERE id=%s", (pid,))
        conn.commit(); cur.close(); conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/tracker/revision', methods=['GET'])
def tracker_revision_get():
    try:
        ciclo_id = request.args.get('ciclo_id', type=int)
        conn = get_db(); cur = conn.cursor(dictionary=True)
        ciclo = _ciclo_actual(cur, ciclo_id)
        if not ciclo: return jsonify([])
        fi = ciclo['fecha_inicio']; hoy = date.today()
        semana_actual = max(1, min(ciclo['semanas_totales'], (hoy - fi).days // 7 + 1))
        rows = []
        for w in range(1, semana_actual + 1):
            puntaje = _week_score(cur, ciclo['id'], fi, w)
            cur.execute("SELECT notas FROM revision_semanal WHERE ciclo_id=%s AND numero_semana=%s",
                        (ciclo['id'], w))
            rev = cur.fetchone()
            rows.append({'numero': w, 'score': puntaje, 'notas': rev['notas'] if rev else ''})
        cur.close(); conn.close()
        return jsonify({'ciclo_id': ciclo['id'], 'semanas': rows})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/tracker/revision', methods=['POST'])
def tracker_revision():
    try:
        d = request.json; conn = get_db(); cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id FROM revision_semanal WHERE ciclo_id=%s AND numero_semana=%s",
                    (d['ciclo_id'], d['numero_semana']))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE revision_semanal SET puntaje=%s,notas=%s WHERE id=%s",
                        (d['puntaje'], d.get('notas',''), row['id']))
        else:
            cur.execute("INSERT INTO revision_semanal (ciclo_id,numero_semana,puntaje,notas) VALUES (%s,%s,%s,%s)",
                        (d['ciclo_id'], d['numero_semana'], d['puntaje'], d.get('notas','')))
        conn.commit(); cur.close(); conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def init_pgn_cache_db():
    conn = get_db(); cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS pgn_cache (
        id INT AUTO_INCREMENT PRIMARY KEY,
        ref_key VARCHAR(200) NOT NULL UNIQUE,
        pgn MEDIUMTEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )""")
    conn.commit(); cur.close(); conn.close()

def init_libretas_db():
    conn = get_db(); cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS libretas (
        id INT AUTO_INCREMENT PRIMARY KEY,
        titulo VARCHAR(255) NOT NULL DEFAULT 'Nueva nota',
        color VARCHAR(20) DEFAULT '#00b894',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS libretas_secciones (
        id INT AUTO_INCREMENT PRIMARY KEY,
        libreta_id INT NOT NULL,
        parent_id INT DEFAULT NULL,
        titulo VARCHAR(255) NOT NULL DEFAULT 'Nueva sección',
        contenido TEXT,
        orden INT DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (libreta_id) REFERENCES libretas(id) ON DELETE CASCADE,
        FOREIGN KEY (parent_id) REFERENCES libretas_secciones(id) ON DELETE CASCADE
    )""")
    conn.commit()
    cur.close(); conn.close()

if __name__ == '__main__':
    init_tracker_db()
    init_libretas_db()
    init_pgn_cache_db()
    print("Servidor de Ajedrez con Gemini iniciado en http://localhost:5000")
    app.run(host='0.0.0.0', debug=True, port=5000)

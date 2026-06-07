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

        send_command(f"position fen {fen}")
        send_command("go movetime 3000")

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

@app.route('/proxy/lichess-study/<study_id>')
def proxy_lichess_study(study_id):
    import urllib.request
    url = f'https://lichess.org/study/{study_id}.pgn'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/x-chess-pgn'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        pgn = resp.read().decode('utf-8')
    return jsonify({'data': {'pgn': pgn}})

@app.route('/proxy/lichess-chapter/<study_id>/<chapter_id>')
def proxy_lichess_chapter(study_id, chapter_id):
    import urllib.request
    url = f'https://lichess.org/study/{study_id}/{chapter_id}.pgn'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/x-chess-pgn'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        pgn = resp.read().decode('utf-8')
    return jsonify({'data': {'pgn': pgn}})


if __name__ == '__main__':
    print("Servidor de Ajedrez con Gemini iniciado en http://localhost:5000")
    app.run(host='0.0.0.0', debug=True, port=5000)

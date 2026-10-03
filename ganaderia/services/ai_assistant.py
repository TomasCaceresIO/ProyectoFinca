import io
import re
import json
import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from django.conf import settings
from django.utils import timezone
from groq import Groq
from pypdf import PdfReader

logger = logging.getLogger(__name__)

GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"
DEFAULT_GROQ_MODEL = GROQ_DEFAULT_MODEL
CANDIDATE_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]


def get_groq_client() -> Optional[Groq]:
    """Inicializa y retorna la instancia de cliente oficial de Groq."""
    api_key = getattr(settings, "GROQ_API_KEY", "").strip()
    if not api_key:
        logger.error("GROQ_API_KEY no está configurada en settings.")
        return None
    return Groq(api_key=api_key)


def extraer_texto_de_pdf(archivo_bytes: bytes) -> str:
    """Extrae el contenido de texto plano de todas las páginas de un PDF en memoria."""
    texto_acumulado = []
    try:
        reader = PdfReader(io.BytesIO(archivo_bytes))
        for idx, page in enumerate(reader.pages):
            contenido = page.extract_text() or ""
            if contenido.strip():
                texto_acumulado.append(f"--- PÁGINA {idx + 1} ---\n{contenido}")
        return "\n\n".join(texto_acumulado)
    except Exception as e:
        logger.error(f"Error extrayendo texto del PDF con pypdf: {e}")
        return ""


SYSTEM_PROMPT_CENSO = """
Eres un asistente veterinario experto en auditoría de libros oficiales de explotación ganadera bovina (SITRAN / OCA / DIB).
Tu tarea es analizar el texto extraído de un documento de censo o lote y devolver una lista estructurada de animales.

IMPORTANTE SOBRE LAS COLUMNAS Y DATOS:
- Las tablas pueden venir en cualquier orden de columnas.
- NUNCA uses el año de nacimiento (ej. 2018, 2022, 2026) ni el número de orden de fila (01, 02... 92) como crotal.
- El Crotal es el identificador visual de 4 dígitos numéricos asignado al animal (ej. 1001, 1012, 4088).
- Fecha de nacimiento: Debe respetar la fecha que figura en el documento convertida a formato ISO YYYY-MM-DD. Si no tiene fecha, asigna null. NUNCA pongas la fecha de hoy por defecto.
- Crotal madre: Si la fila dice 'Fundadora', 'Sin madre', '-' o está vacía, asigna null (será un animal fundador). Si especifica un número de crotal (ej. 1001), extrae esos 4 dígitos.
- Sexo: Debe ser estrictamente 'H' (Hembra) o 'M' (Macho).
- Raza: Texto de la raza indicada (ej. 'Limusina', 'Charolesa', 'Retinta', 'Cruzada'). Si no figura, usa 'Limusina'.
- Sub-ubicación: Debe ser 'PASTO', 'CEBADERO', 'APARTADO' o 'BAJA'. Si no se menciona o dice campo/libre, usa 'PASTO'.

DEBES RESPONDER EXCLUSIVAMENTE CON UN OBJETO JSON VÁLIDO con la siguiente estructura:
{
  "finca_nombre": null,
  "animales": [
    {
      "crotal": "1001",
      "sexo": "H",
      "fecha_nacimiento": "2018-03-12",
      "raza": "Limusina",
      "crotal_madre": null,
      "sub_ubicacion": "PASTO"
    }
  ]
}
"""

PALABRAS_DIGITOS = {
    'cero': '0', 'uno': '1', 'un': '1', 'una': '1', 'dos': '2', 'tres': '3',
    'cuatro': '4', 'cinco': '5', 'seis': '6', 'siete': '7', 'ocho': '8', 'nueve': '9',
}

PALABRAS_COMPUESTAS = {
    'diez': 10, 'once': 11, 'doce': 12, 'trece': 13, 'catorce': 14, 'quince': 15,
    'dieciseis': 16, 'dieciséis': 16, 'diecisiete': 17, 'dieciocho': 18, 'diecinueve': 19,
    'veinte': 20, 'veintiuno': 21, 'veintidos': 22, 'veintidós': 22, 'veintitres': 23,
    'veintitrés': 23, 'veinticuatro': 24, 'veinticinco': 25, 'veintiseis': 26,
    'veintiséis': 26, 'veintisiete': 27, 'veintiocho': 28, 'veintinueve': 29,
    'treinta': 30, 'cuarenta': 40, 'cincuenta': 50, 'sesenta': 60,
    'setenta': 70, 'ochenta': 80, 'noventa': 90, 'cien': 100, 'ciento': 100,
}

MESES_ESP = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
    'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
    'septiembre': 9, 'setiembre': 9, 'octubre': 10,
    'noviembre': 11, 'diciembre': 12
}


def normalizar_crotal(valor: Optional[str]) -> Optional[str]:
    """
    Normaliza un crotal detectado por voz o texto:
    - Convierte palabras numéricas en español (ej. 'tres' -> '0003', 'cero cero tres' -> '0003',
      'treinta' -> '0030', 'cuarenta y dos' -> '0042', '5' -> '0005', '42' -> '0042').
    - Rellena con ceros a la izquierda hasta 4 dígitos (.zfill(4)).
    - Retorna una cadena de 4 dígitos o None si no es procesable.
    """
    if valor is None:
        return None

    val_str = str(valor).strip().lower()
    if not val_str:
        return None

    if val_str.isdigit():
        if len(val_str) <= 4:
            return val_str.zfill(4)
        return val_str

    val_str = re.sub(r'^(?:#|n[ºo]|número|crotal)\s*', '', val_str).strip()

    if val_str.isdigit():
        if len(val_str) <= 4:
            return val_str.zfill(4)
        return val_str

    tokens = val_str.replace('-', ' ').split()

    if all(t in PALABRAS_DIGITOS for t in tokens):
        cadena_digitos = "".join(PALABRAS_DIGITOS[t] for t in tokens)
        return cadena_digitos.zfill(4)

    total = 0
    i = 0
    es_compuesto = True
    while i < len(tokens):
        t = tokens[i]
        if t == 'y':
            i += 1
            continue
        if t in PALABRAS_COMPUESTAS:
            total += PALABRAS_COMPUESTAS[t]
        elif t in PALABRAS_DIGITOS:
            total += int(PALABRAS_DIGITOS[t])
        elif t.isdigit():
            total += int(t)
        else:
            es_compuesto = False
            break
        i += 1

    if es_compuesto and total > 0:
        return str(total).zfill(4)

    return None


def _extraer_comando_parto_regex(texto: str, fincas_disponibles: list = None) -> dict:
    """
    Parser simulado / fallback basado en reglas y expresiones regulares
    para extraer datos de registro de parto a partir de lenguaje natural.
    """
    texto_lower = texto.lower()
    hoy = timezone.now().date()

    # 1. Determinar fecha
    fecha_parto = None
    if "anteayer" in texto_lower:
        fecha_parto = hoy - timedelta(days=2)
    elif "ayer" in texto_lower:
        fecha_parto = hoy - timedelta(days=1)
    elif "pasado mañana" in texto_lower or "pasado manana" in texto_lower:
        fecha_parto = hoy + timedelta(days=2)
    elif "mañana" in texto_lower or "manana" in texto_lower:
        fecha_parto = hoy + timedelta(days=1)
    elif "hoy" in texto_lower or "ha nacido hoy" in texto_lower or "nacio hoy" in texto_lower or "nació hoy" in texto_lower:
        fecha_parto = hoy
    else:
        m_texto_fecha = re.search(
            r'\b(\d{1,2})\s+de\s+([a-záéíóú]+)(?:\s+(?:de|del))?\s+(\d{4})\b',
            texto_lower
        )
        if m_texto_fecha:
            d_str, mes_nombre, y_str = m_texto_fecha.groups()
            mes_num = MESES_ESP.get(mes_nombre)
            if mes_num:
                try:
                    fecha_parto = date(int(y_str), mes_num, int(d_str))
                except ValueError:
                    pass

        if not fecha_parto:
            m_fecha = re.search(r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b', texto)
            if m_fecha:
                d, m, y = map(int, m_fecha.groups())
                try:
                    fecha_parto = date(y, m, d)
                except ValueError:
                    pass

        if not fecha_parto:
            m_iso = re.search(r'\b(\d{4})[/-](\d{1,2})[/-](\d{1,2})\b', texto)
            if m_iso:
                y, m, d = map(int, m_iso.groups())
                try:
                    fecha_parto = date(y, m, d)
                except ValueError:
                    pass

    if not fecha_parto:
        fecha_parto = hoy

    # 2. Determinar sexo de la cría
    sexo = "H"
    if any(w in texto_lower for w in ["ternero", "becerro", "macho", "choto", "torito"]):
        sexo = "M"
    elif any(w in texto_lower for w in ["ternera", "becerra", "hembra", "novilla"]):
        sexo = "H"

    # 3. Determinar raza
    razas_comunes = [
        ("limusina", "Limusina"),
        ("limosina", "Limusina"),
        ("limusin", "Limusina"),
        ("limosin", "Limusina"),
        ("charolais", "Charolais"),
        ("charolesa", "Charolais"),
        ("charol", "Charolais"),
        ("avileña", "Avileña-Negra Ibérica"),
        ("avilena", "Avileña-Negra Ibérica"),
        ("retinta", "Retinta"),
        ("morucha", "Morucha"),
        ("aberdeen angus", "Aberdeen Angus"),
        ("angus", "Aberdeen Angus"),
        ("rubia gallega", "Rubia Gallega"),
        ("berrenda en colorado", "Berrenda en Colorado"),
        ("berrenda", "Berrenda en Colorado"),
    ]
    raza = "Retinta"
    for k, v in razas_comunes:
        if k in texto_lower:
            raza = v
            break

    # 4. Determinar recinto
    recinto = "CEBADERO" if "cebadero" in texto_lower else "PASTO"

    # 5. Determinar finca si se menciona explícitamente
    finca_detectada = None
    if fincas_disponibles:
        for f_nom in fincas_disponibles:
            if f_nom.lower() in texto_lower:
                finca_detectada = f_nom
                break
    if not finca_detectada:
        m_finca = re.search(r'\b(?:en\s+la\s+finca|en\s+finca|finca)\s+([a-záéíóú0-9_\-]+(?:\s+[a-záéíóú0-9_\-]+)?)\b', texto_lower)
        if m_finca:
            finca_detectada = m_finca.group(1).title()

    # 6. Extraer crotales de forma estricta evitando números de año
    anios_fecha = set()
    for m in re.finditer(r'(?:de|del|en|[/-]|\b)\s*(\d{4})\b', texto_lower):
        posible_anio = m.group(1)
        sub_ant = texto_lower[max(0, m.start() - 25):m.start()]
        if any(mes in sub_ant for mes in MESES_ESP.keys()) or any(p in sub_ant for p in ["año", "ano", "del", "de", "en", "el"]):
            if posible_anio.startswith(('19', '20')):
                anios_fecha.add(posible_anio)

    crotal_madre = None
    cria_crotal = None

    m_madre = re.search(r'(?:vaca|madre|la|el|hembra)\s*(?:#|n[ºo]|número)?\s*([a-záéíóú0-9\s]+?)(?=\s+(?:pari[oó]|tuvo|dio|con|y|el|en|a|de|\d{4}|$))', texto, re.IGNORECASE)
    m_cria = re.search(r'(?:terner[ao]|becerr[ao]|cría|cria|hijo|hija|crotal)\s*(?:#|n[ºo]|número)?\s*([a-záéíóú0-9\s]+?)(?=\s+(?:con|en|el|de|del|raza|nacid[ao]|$))', texto, re.IGNORECASE)

    m_madre_num = re.search(r'(?:vaca|madre|la|el|hembra)\s*(?:#|n[ºo]|número)?\s*(\d{1,4})\b', texto, re.IGNORECASE)
    m_cria_num = re.search(r'(?:terner[ao]|becerr[ao]|cría|cria|hijo|hija|crotal)\s*(?:#|n[ºo]|número)?\s*(\d{1,4})\b', texto, re.IGNORECASE)

    if m_madre_num:
        crotal_madre = normalizar_crotal(m_madre_num.group(1))
    elif m_madre:
        c_norm = normalizar_crotal(m_madre.group(1))
        if c_norm:
            crotal_madre = c_norm

    if m_cria_num:
        cria_crotal = normalizar_crotal(m_cria_num.group(1))
    elif m_cria:
        c_norm = normalizar_crotal(m_cria.group(1))
        if c_norm:
            cria_crotal = c_norm

    todos_nums = [n for n in re.findall(r'\b\d{1,4}\b', texto)]
    crotales_candidatos = []
    for num in todos_nums:
        idx = texto.find(num)
        es_crotal_explicito = False
        if idx != -1:
            segmento_previo = texto[max(0, idx - 15):idx].lower()
            if "crotal" in segmento_previo:
                es_crotal_explicito = True
        if len(num) == 4 and num in anios_fecha and not es_crotal_explicito:
            continue
        if len(num) <= 2:
            sub_post = texto_lower[idx:min(len(texto_lower), idx + 20)]
            if any(f"de {mes}" in sub_post for mes in MESES_ESP.keys()):
                continue
        norm = normalizar_crotal(num)
        if norm:
            crotales_candidatos.append(norm)

    candidatos_restantes = [c for c in crotales_candidatos if c != cria_crotal and c != crotal_madre]

    if not crotal_madre:
        if len(candidatos_restantes) >= 1 and not cria_crotal:
            crotal_madre = candidatos_restantes.pop(0)

    if not cria_crotal:
        if len(candidatos_restantes) >= 1:
            cria_crotal = candidatos_restantes.pop(0)

    return {
        "intencion": "REGISTRAR_PARTO",
        "crotal_madre": crotal_madre,
        "fecha_parto": fecha_parto.strftime('%Y-%m-%d') if fecha_parto else hoy.strftime('%Y-%m-%d'),
        "cria_crotal": cria_crotal,
        "cria_sexo": sexo,
        "cria_raza": raza,
        "cria_recinto": recinto,
        "cria_finca": finca_detectada,
    }


def _llamar_groq_parto(texto: str, fincas_disponibles: list = None, model: str = None) -> dict:
    """Llama a la API oficial de Groq para extraer datos estructurados de parto con JSON mode nativo."""
    client = get_groq_client()
    if client is None:
        raise ValueError("Clave GROQ_API_KEY no configurada o vacía.")

    modelo_a_usar = model or GROQ_DEFAULT_MODEL
    hoy = timezone.now().date()
    hoy_iso = hoy.strftime('%Y-%m-%d')
    hoy_es = hoy.strftime('%d/%m/%Y')
    fincas_str = ", ".join(fincas_disponibles) if fincas_disponibles else "no especificadas"

    system_instruction = (
        f"Eres el Asistente de Inteligencia Artificial de un sistema de gestión ganadera bovina. "
        f"La fecha de hoy de referencia es {hoy_iso} ({hoy_es}). "
        f"Las fincas disponibles en la explotación son: [{fincas_str}]. "
        f"Tu cometido es extraer con exactitud los datos para registrar un parto de vaca y su cría.\n\n"
        f"Reglas estrictas de extracción:\n"
        f"- crotal_madre: Crotal obligatorio de exactamente 4 dígitos de la vaca madre (ej. '3014', '0001'). "
        f"Si se dice en palabras (ej. 'tres', 'cero cero tres') normalízalo a 4 dígitos con ceros a la izquierda (ej. '0003'). "
        f"Si no se especifica de forma explícita en el mensaje o falta, DEBES devolver null.\n"
        f"- cria_crotal: Crotal obligatorio de exactamente 4 dígitos asignado a la cría nacida (ej. '5012', '0003'). "
        f"Si se dice en palabras (ej. 'cuarenta y dos') normalízalo a 4 dígitos (ej. '0042'). "
        f"PROHIBIDO TERMINANTEMENTE asignar números de año (ej. 19xx, 20xx) ni el crotal de la madre como cria_crotal. "
        f"Si no se indica explícitamente un crotal para la cría, DEBES devolver null.\n"
        f"- fecha_parto: Fecha del parto en formato YYYY-MM-DD. Si el usuario indica una fecha explícita pasada o histórica "
        f"(ej. '10 de octubre de 2022', '10/10/2022'), DEBES respetarla y parsearla fielmente ('2022-10-10'). "
        f"Solo debes usar la fecha de hoy ({hoy_iso}) si el usuario dice 'hoy', 'ha nacido hoy', o no aporta indicación temporal.\n"
        f"- cria_sexo: 'H' para hembra/ternera/becerra, 'M' para macho/ternero/becerro.\n"
        f"- cria_raza: raza de la cría (ej. 'Limusina', 'Retinta', 'Charolais'). Si no se menciona, usa 'Retinta'.\n"
        f"- cria_recinto: 'PASTO' o 'CEBADERO' (por defecto 'PASTO').\n"
        f"- cria_finca: nombre exacto de la finca mencionada para ubicar la cría si el usuario la nombra. Si no, null.\n"
        f"- intencion: 'REGISTRAR_PARTO' si describe un parto o nacimiento.\n\n"
        f"DEBES RESPONDER EXCLUSIVAMENTE CON UN OBJETO JSON VÁLIDO con las claves anteriores."
    )

    chat_completion = client.chat.completions.create(
        messages=[
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": texto}
        ],
        model=modelo_a_usar,
        response_format={"type": "json_object"},
        temperature=0.1,
        max_completion_tokens=1024,
    )

    res_raw = chat_completion.choices[0].message.content
    res_dict = json.loads(res_raw)
    if res_dict.get('crotal_madre'):
        res_dict['crotal_madre'] = normalizar_crotal(res_dict['crotal_madre'])
    if res_dict.get('cria_crotal'):
        res_dict['cria_crotal'] = normalizar_crotal(res_dict['cria_crotal'])
    return res_dict


def procesar_comando_parto(
    texto_o_audio,
    audio_content_type: str = 'audio/webm',
    fincas_disponibles: list = None
) -> dict:
    """
    Procesa un comando de voz o texto para registrar un parto.
    Intenta procesar mediante Groq Llama-3.3-70B con modo JSON nativo.
    Si la llamada falla, agota timeout o no hay clave API configurada, conmuta transparentemente a regex.
    """
    api_key = getattr(settings, 'GROQ_API_KEY', '').strip()
    if api_key and isinstance(texto_o_audio, str):
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    _llamar_groq_parto,
                    texto_o_audio,
                    fincas_disponibles=fincas_disponibles,
                    model=GROQ_DEFAULT_MODEL
                )
                return future.result(timeout=10)
        except Exception as e:
            logger.warning(f"Error procesando comando de parto con Groq: {e}. Conmutando a fallback local.")
            return _extraer_comando_parto_regex(texto_o_audio, fincas_disponibles=fincas_disponibles)

    # Modo Simulado / Fallback sin API key
    if isinstance(texto_o_audio, str):
        return _extraer_comando_parto_regex(texto_o_audio, fincas_disponibles=fincas_disponibles)

    return {
        "intencion": "REGISTRAR_PARTO",
        "crotal_madre": None,
        "fecha_parto": timezone.now().date().strftime('%Y-%m-%d'),
        "cria_crotal": None,
        "cria_sexo": "H",
        "cria_raza": "Retinta",
        "cria_recinto": "PASTO",
        "cria_finca": None,
        "error": "El análisis de audio directo requiere configurar la variable GROQ_API_KEY."
    }


def _extraer_tabla_pdf_dinamica(texto: str, fincas_disponibles: list = None) -> list:
    """
    Parser heurístico dinámico para tablas de documentos PDF de censo con columnas en orden variable.
    Detecta la cabecera por palabras clave y mapea la posición/índice de cada columna.
    Extrae fila a fila evitando solapamientos entre crotales, fechas, órdenes o razas.
    Retorna una lista de diccionarios de animales.
    """
    lineas = [ln.strip() for ln in texto.splitlines() if ln.strip()]
    if not lineas:
        return []

    header_idx = -1
    col_map = {}

    for i, linea in enumerate(lineas):
        linea_lower = linea.lower()
        kw_hits = sum(1 for kw in ["crotal", "identificador", "chapa", "sexo", "sex", "nacimiento", "f. nac", "fecha", "raza", "madre", "ubicación", "recinto"] if kw in linea_lower)
        if kw_hits >= 2 and ("crotal" in linea_lower or "identificador" in linea_lower or "chapa" in linea_lower):
            partes = [p.strip() for p in re.split(r'\||\t|\s{2,}', linea) if p.strip()]
            if len(partes) >= 2:
                temp_map = {}
                for idx, p in enumerate(partes):
                    p_l = p.lower()
                    if any(k in p_l for k in ["crotal", "identificador", "chapa"]):
                        temp_map['crotal'] = idx
                    elif any(k in p_l for k in ["sexo", "sex"]):
                        temp_map['sexo'] = idx
                    elif any(k in p_l for k in ["nacimiento", "f. nac", "fecha nac", "f.nac"]):
                        temp_map['fecha_nacimiento'] = idx
                    elif "fecha" in p_l and 'fecha_nacimiento' not in temp_map:
                        temp_map['fecha_nacimiento'] = idx
                    elif any(k in p_l for k in ["raza", "breed"]):
                        temp_map['raza'] = idx
                    elif any(k in p_l for k in ["madre", "genealogía", "genealogia", "dam"]):
                        temp_map['crotal_madre'] = idx
                    elif any(k in p_l for k in ["ubicación", "ubicacion", "recinto", "destino"]):
                        temp_map['sub_ubicacion'] = idx

                if 'crotal' in temp_map:
                    header_idx = i
                    col_map = temp_map
                    break

    if header_idx == -1 or 'crotal' not in col_map:
        return []

    animales = []
    c_idx = col_map['crotal']
    s_idx = col_map.get('sexo')
    f_idx = col_map.get('fecha_nacimiento')
    r_idx = col_map.get('raza')
    m_idx = col_map.get('crotal_madre')
    u_idx = col_map.get('sub_ubicacion')

    for linea in lineas[header_idx + 1:]:
        if any(h in linea.lower() for h in ["total", "página", "pagina", "firma", "titular", "explotación", "explotacion"]):
            continue

        partes = [p.strip() for p in re.split(r'\||\t|\s{2,}', linea) if p.strip()]
        if len(partes) <= c_idx:
            continue

        raw_crotal = partes[c_idx]
        crotal_limpio = normalizar_crotal(raw_crotal)
        if not crotal_limpio or len(crotal_limpio) != 4 or not crotal_limpio.isdigit():
            m_c = re.search(r'\b\d{4}\b', raw_crotal)
            if m_c:
                crotal_limpio = m_c.group(0)
            else:
                continue

        sexo = 'H'
        if s_idx is not None and s_idx < len(partes):
            s_val = partes[s_idx].upper().strip()
            if s_val == 'M' or 'MACHO' in s_val or s_val.startswith('M'):
                sexo = 'M'
            elif s_val == 'H' or 'HEMBRA' in s_val or s_val.startswith('H'):
                sexo = 'H'

        fecha_nac = None
        if f_idx is not None and f_idx < len(partes):
            raw_f = partes[f_idx]
            m_d = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', raw_f)
            if m_d:
                d, m, y = map(int, m_d.groups())
                try:
                    fecha_nac = date(y, m, d).strftime('%Y-%m-%d')
                except ValueError:
                    pass
            elif re.search(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', raw_f):
                m_d2 = re.search(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', raw_f)
                y, m, d = map(int, m_d2.groups())
                try:
                    fecha_nac = date(y, m, d).strftime('%Y-%m-%d')
                except ValueError:
                    pass

        raza = 'Limusina'
        if r_idx is not None and r_idx < len(partes):
            r_val = partes[r_idx]
            if len(r_val) >= 3 and not r_val.isdigit():
                raza = r_val.title()

        crotal_madre = None
        if m_idx is not None and m_idx < len(partes):
            raw_m = partes[m_idx]
            if raw_m.lower() not in ['fundadora', 'sin madre', '-', '', 'ninguna', 'none', 'null']:
                m_norm = normalizar_crotal(raw_m)
                if m_norm and len(m_norm) == 4 and m_norm.isdigit():
                    crotal_madre = m_norm

        sub_ubicacion = 'PASTO'
        if u_idx is not None and u_idx < len(partes):
            u_val = partes[u_idx].upper()
            if 'CEB' in u_val:
                sub_ubicacion = 'CEBADERO'
            elif 'APA' in u_val:
                sub_ubicacion = 'APARTADO'
            elif 'BAJ' in u_val or 'DEF' in u_val:
                sub_ubicacion = 'BAJA'

        animales.append({
            'crotal': crotal_limpio,
            'sexo': sexo,
            'raza': raza,
            'fecha_nacimiento': fecha_nac,
            'crotal_madre': crotal_madre,
            'sub_ubicacion': sub_ubicacion,
        })

    return animales


def _extraer_lote_regex(texto: str, fincas_disponibles: list = None) -> dict:
    """
    Parser heurístico regex para bloques de texto o tablas en lote.
    Retorna un diccionario con formato:
    {'intencion': 'IMPORTAR_LOTE', 'finca_nombre': ..., 'animales': [...]}
    """
    tabla_dinamica = _extraer_tabla_pdf_dinamica(texto, fincas_disponibles)
    if tabla_dinamica:
        return {
            "intencion": "IMPORTAR_LOTE",
            "finca_nombre": None,
            "animales": tabla_dinamica,
        }

    texto_lower = texto.lower()

    # 1. Finca detectada si viene
    finca_detectada = None
    if fincas_disponibles:
        for f_nom in fincas_disponibles:
            if f_nom.lower() in texto_lower:
                finca_detectada = f_nom
                break
    if not finca_detectada:
        m_f = re.search(r'\b(?:en\s+la\s+finca|en\s+finca|finca)\s+([a-záéíóú0-9_\-]+(?:\s+[a-záéíóú0-9_\-]+)?)\b', texto_lower)
        if m_f:
            finca_detectada = m_f.group(1).title()

    # 2. Recinto global
    recinto_global = "PASTO"
    if "cebadero" in texto_lower:
        recinto_global = "CEBADERO"
    elif "apartado" in texto_lower:
        recinto_global = "APARTADO"

    # 3. Detectar fecha/año general por si los animales nacieron en un año concreto
    anio_nacimiento = None
    m_anio = re.search(r'\bnacid[ao]s?\s+(?:en\s+)?(?:el\s+año\s+)?(\d{4})\b', texto_lower)
    if m_anio:
        anio_nacimiento = m_anio.group(1)

    animales_extraidos = []

    candidatos = list(re.finditer(r'\b(\d{1,4})\b', texto))
    posiciones = []
    for c in candidatos:
        val = c.group(1)
        if anio_nacimiento and val == anio_nacimiento:
            continue
        posiciones.append((c.start(), c.end(), val))

    for idx, (start, end, crotal_raw) in enumerate(posiciones):
        next_start = posiciones[idx + 1][0] if idx + 1 < len(posiciones) else len(texto)
        ventana = texto[start:next_start].lower()

        c_norm = normalizar_crotal(crotal_raw)
        if not c_norm:
            continue

        sexo = "H"
        if any(w in ventana for w in ["macho", "ternero", "becerro", "toro"]):
            sexo = "M"
        elif any(w in ventana for w in ["hembra", "ternera", "becerra", "vaca"]):
            sexo = "H"

        raza = "Limusina"
        for r_key, r_nom in [
            ("limusin", "Limusina"), ("limosina", "Limusina"), ("charol", "Charolesa"),
            ("retinta", "Retinta"), ("morucha", "Morucha"), ("angus", "Angus"),
            ("frisona", "Frisona"), ("avileña", "Avileña-Negra Ibérica"), ("cruzad", "Cruzado")
        ]:
            if r_key in ventana:
                raza = r_nom
                break

        crotal_madre = None
        m_madre = re.search(r'(?:madre|hij[ao]\s+de)\s*(?:#|n[ºo])?\s*(\d{1,4})\b', ventana)
        if m_madre:
            crotal_madre = normalizar_crotal(m_madre.group(1))

        fnac = None
        m_fecha = re.search(r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b', ventana)
        if m_fecha:
            d, m, y = map(int, m_fecha.groups())
            try:
                fnac = date(y, m, d).strftime('%Y-%m-%d')
            except ValueError:
                pass
        elif anio_nacimiento:
            fnac = f"{anio_nacimiento}-01-01"

        animales_extraidos.append({
            "crotal": c_norm,
            "sexo": sexo,
            "raza": raza,
            "fecha_nacimiento": fnac,
            "crotal_madre": crotal_madre,
            "sub_ubicacion": recinto_global,
        })

    return {
        "intencion": "IMPORTAR_LOTE",
        "finca_nombre": finca_detectada,
        "animales": animales_extraidos,
    }


def procesar_importacion_lote(
    texto: Optional[str] = None,
    archivo_bytes: Optional[bytes] = None,
    mime_type: str = "application/pdf",
    fincas_disponibles: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Procesa un PDF o texto de entrada mediante Groq Llama-3.3-70B y retorna los animales parseados."""
    texto_extraido_pdf = ""
    if archivo_bytes:
        texto_extraido_pdf = extraer_texto_de_pdf(archivo_bytes)
        if not texto_extraido_pdf.strip():
            return {
                "intencion": "IMPORTAR_LOTE",
                "finca_nombre": None,
                "error": "No se pudo extraer texto digital del documento PDF adjunto. Compruebe que no sea un archivo escaneado sin OCR.",
                "animales": []
            }

    texto_a_procesar = texto_extraido_pdf if texto_extraido_pdf else (texto.strip() if texto else "")

    if not texto_a_procesar:
        return {
            "intencion": "IMPORTAR_LOTE",
            "finca_nombre": None,
            "error": "No se proporcionó ningún texto o documento para procesar.",
            "animales": []
        }

    client = get_groq_client()
    if not client:
        res_local = _extraer_lote_regex(texto_a_procesar, fincas_disponibles=fincas_disponibles)
        if res_local.get("animales"):
            return {
                "intencion": "IMPORTAR_LOTE",
                "finca_nombre": res_local.get("finca_nombre"),
                "animales": res_local["animales"],
                "error": None,
                "aviso_banner": "El censo fue procesado mediante el analizador local."
            }
        return {
            "intencion": "IMPORTAR_LOTE",
            "finca_nombre": None,
            "error": "El servicio de IA requiere configurar la variable GROQ_API_KEY en el entorno.",
            "animales": []
        }

    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_CENSO},
                {
                    "role": "user",
                    "content": f"Extrae todos los animales del siguiente contenido respetando rigurosamente las instrucciones:\n\n{texto_a_procesar}"
                }
            ],
            model=GROQ_DEFAULT_MODEL,
            response_format={"type": "json_object"},
            temperature=0.1,
            max_completion_tokens=4096,
        )

        respuesta_raw = chat_completion.choices[0].message.content
        datos = json.loads(respuesta_raw)
        animales_extraidos = datos.get("animales", [])

        animales_limpios = []
        for a in animales_extraidos:
            crotal_str = str(a.get("crotal", "")).strip().zfill(4)
            if len(crotal_str) == 4 and crotal_str.isdigit():
                a["crotal"] = crotal_str
                if a.get("crotal_madre"):
                    madre_str = str(a["crotal_madre"]).strip().zfill(4)
                    a["crotal_madre"] = madre_str if (len(madre_str) == 4 and madre_str.isdigit()) else None
                else:
                    a["crotal_madre"] = None

                sexo_raw = str(a.get("sexo", "H")).upper()
                a["sexo"] = "M" if "M" in sexo_raw else "H"

                sub = str(a.get("sub_ubicacion", "PASTO")).upper()
                if "CEB" in sub:
                    a["sub_ubicacion"] = "CEBADERO"
                elif "APA" in sub:
                    a["sub_ubicacion"] = "APARTADO"
                elif "BAJ" in sub or "DEF" in sub:
                    a["sub_ubicacion"] = "BAJA"
                else:
                    a["sub_ubicacion"] = "PASTO"

                animales_limpios.append(a)

        return {
            "intencion": "IMPORTAR_LOTE",
            "finca_nombre": datos.get("finca_nombre"),
            "animales": animales_limpios,
            "error": None
        }

    except Exception as e:
        logger.exception("Error procesando solicitud en Groq API")
        if texto_a_procesar:
            res_local = _extraer_lote_regex(texto_a_procesar, fincas_disponibles=fincas_disponibles)
            if res_local.get("animales"):
                return {
                    "intencion": "IMPORTAR_LOTE",
                    "finca_nombre": res_local.get("finca_nombre"),
                    "animales": res_local["animales"],
                    "error": None,
                    "aviso_banner": "El censo fue procesado mediante el analizador local debido a una saturación temporal de la IA."
                }

        return {
            "intencion": "IMPORTAR_LOTE",
            "finca_nombre": None,
            "error": f"Error en el motor de IA (Groq): {str(e)}",
            "animales": []
        }

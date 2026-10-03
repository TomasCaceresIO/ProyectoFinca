import re
import json
import logging
from datetime import date, timedelta
from typing import Optional, Literal
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from django.conf import settings
from django.utils import timezone
from pydantic import BaseModel, Field

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

logger = logging.getLogger(__name__)


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

    # Si ya son dígitos puros
    if val_str.isdigit():
        if len(val_str) <= 4:
            return val_str.zfill(4)
        return val_str

    # Quitar posibles prefijos como '#', 'nº', 'número', 'crotal'
    val_str = re.sub(r'^(?:#|n[ºo]|número|crotal)\s*', '', val_str).strip()

    if val_str.isdigit():
        if len(val_str) <= 4:
            return val_str.zfill(4)
        return val_str

    tokens = val_str.replace('-', ' ').split()

    # Caso A: Secuencia de dígitos individuales hablados (ej. "cero cero cero tres", "cero cuatro dos")
    if all(t in PALABRAS_DIGITOS for t in tokens):
        cadena_digitos = "".join(PALABRAS_DIGITOS[t] for t in tokens)
        return cadena_digitos.zfill(4)

    # Caso B: Número compuesto (ej. "cuarenta y dos", "treinta y cinco", "veintitrés", "quince")
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


class ComandoPartoOutput(BaseModel):
    intencion: Literal["REGISTRAR_PARTO", "DESCONOCIDO"] = Field(
        default="REGISTRAR_PARTO",
        description="Intención identificada del usuario. 'REGISTRAR_PARTO' si describe un parto o cría nacida."
    )
    crotal_madre: Optional[str] = Field(
        default=None,
        description="Código o crotal de 4 dígitos de la vaca madre (ej. '3014')."
    )
    fecha_parto: Optional[str] = Field(
        default=None,
        description="Fecha del parto en formato YYYY-MM-DD. Resolver fechas relativas como hoy o ayer."
    )
    cria_crotal: Optional[str] = Field(
        default=None,
        description="Código o crotal de 4 dígitos de la cría nacida (ej. '5012')."
    )
    cria_sexo: Optional[Literal["M", "H"]] = Field(
        default="H",
        description="Sexo de la cría: 'M' para macho/ternero, 'H' para hembra/ternera."
    )
    cria_raza: Optional[str] = Field(
        default="Retinta",
        description="Raza de la cría (ej. Limusina, Retinta, Charolais, Avileña)."
    )
    cria_recinto: Optional[str] = Field(
        default="PASTO",
        description="Recinto asignado a la cría: 'PASTO' o 'CEBADERO'."
    )
    cria_finca: Optional[str] = Field(
        default=None,
        description="Nombre de la finca de la explotación donde se ubicará la cría (ej. 'Finca Montealto'). Null si no se indica."
    )


MESES_ESP = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
    'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
    'septiembre': 9, 'setiembre': 9, 'octubre': 10,
    'noviembre': 11, 'diciembre': 12
}


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
        # Buscar formato textual en español: "10 de octubre de 2022" o "10 de octubre del 2022" o "el 10 de octubre 2022"
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

        # Buscar formato DD/MM/YYYY o DD-MM-YYYY
        if not fecha_parto:
            m_fecha = re.search(r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b', texto)
            if m_fecha:
                d, m, y = map(int, m_fecha.groups())
                try:
                    fecha_parto = date(y, m, d)
                except ValueError:
                    pass

        # Buscar formato YYYY-MM-DD
        if not fecha_parto:
            m_iso = re.search(r'\b(\d{4})[/-](\d{1,2})[/-](\d{1,2})\b', texto)
            if m_iso:
                y, m, d = map(int, m_iso.groups())
                try:
                    fecha_parto = date(y, m, d)
                except ValueError:
                    pass

    # Si no se detectó ninguna fecha temporal explícita ni relativa, se asume hoy por defecto
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
        # Detectar patrones como "en finca X", "en la finca X", "finca X"
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

    # Intentar capturar por contexto sintáctico explícito (dígitos o palabras)
    # Patrones para crotales que pueden ser números o palabras habladas
    m_madre = re.search(r'(?:vaca|madre|la|el|hembra)\s*(?:#|n[ºo]|número)?\s*([a-záéíóú0-9\s]+?)(?=\s+(?:pari[oó]|tuvo|dio|con|y|el|en|a|de|\d{4}|$))', texto, re.IGNORECASE)
    m_cria = re.search(r'(?:terner[ao]|becerr[ao]|cría|cria|hijo|hija|crotal)\s*(?:#|n[ºo]|número)?\s*([a-záéíóú0-9\s]+?)(?=\s+(?:con|en|el|de|del|raza|nacid[ao]|$))', texto, re.IGNORECASE)

    # 1. Probar captura directa de 4 dígitos clásica
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

    # Extraer todos los números de 1 a 4 dígitos
    todos_nums = [n for n in re.findall(r'\b\d{1,4}\b', texto)]
    crotales_candidatos = []
    for num in todos_nums:
        # Evitar números de 1 o 2 dígitos que pertenezcan a la fecha
        idx = texto.find(num)
        es_crotal_explicito = False
        if idx != -1:
            segmento_previo = texto[max(0, idx - 15):idx].lower()
            if "crotal" in segmento_previo:
                es_crotal_explicito = True
        if len(num) == 4 and num in anios_fecha and not es_crotal_explicito:
            continue
        # Si tiene 1 o 2 dígitos y forma parte de una fecha textual (ej. "el 10 de octubre"), ignorar si coincide con el día o mes
        if len(num) <= 2:
            sub_post = texto_lower[idx:min(len(texto_lower), idx + 20)]
            if any(f"de {mes}" in sub_post for mes in MESES_ESP.keys()):
                continue
        # Normalizar a 4 dígitos
        norm = normalizar_crotal(num)
        if norm:
            crotales_candidatos.append(norm)

    # Si se capturó cria_crotal sintácticamente pero no crotal_madre:
    # no podemos reusar cria_crotal como crotal_madre
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


def _llamar_gemini(texto_o_audio, audio_content_type: str = 'audio/webm', fincas_disponibles: list = None) -> dict:
    """Llama a la API oficial de Google GenAI con structured outputs."""
    if genai is None or types is None:
        raise ImportError("El paquete google-genai no está disponible.")

    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    client = genai.Client(api_key=api_key)

    hoy = timezone.now().date()
    hoy_iso = hoy.strftime('%Y-%m-%d')
    hoy_es = hoy.strftime('%d/%m/%Y')
    fincas_str = ", ".join(fincas_disponibles) if fincas_disponibles else "no especificadas"

    system_instruction = (
        f"Eres el Asistente de Inteligencia Artificial de un sistema de gestión ganadera bovina. "
        f"La fecha de hoy de referencia es {hoy_iso} ({hoy_es}). "
        f"Las fincas disponibles en la explotación son: [{fincas_str}]. "
        f"Tu cometido es extraer con exactitud los datos para registrar un parto de vaca y su cría. "
        f"Reglas estrictas de extracción:\n"
        f"- crotal_madre: Crotal obligatorio de exactamente 4 dígitos de la vaca madre (ej. '3014', '0001'). "
        f"Si se dice en palabras (ej. 'tres', 'cero cero tres') normalízalo a 4 dígitos con ceros a la izquierda (ej. '0003'). "
        f"Si no se especifica de forma explícita en el mensaje o falta, DEBES devolver null.\n"
        f"- cria_crotal: Crotal obligatorio de exactamente 4 dígitos asignado a la cría nacida (ej. '5012', '0003'). "
        f"Si se dice en palabras (ej. 'cuarenta y dos') normalízalo a 4 dígitos (ej. '0042'). "
        f"PROHIBIDO TERMINANTEMENTE asignar números de año (ej. 19xx, 20xx, como 2022, 2024, etc.) como cria_crotal, "
        f"a menos que el usuario diga explícitamente 'con crotal 2022'. Si no se indica explícitamente un crotal para la cría, "
        f"DEBES devolver null.\n"
        f"- fecha_parto: Fecha del parto en formato YYYY-MM-DD. Si el usuario indica una fecha explícita pasada o histórica "
        f"(ej. '10 de octubre de 2022', '10/10/2022'), DEBES respetarla y parsearla fielmente (ej. '2022-10-10'). "
        f"Solo debes usar la fecha de hoy ({hoy_iso}) si el usuario dice 'hoy', 'ha nacido hoy', o no aporta ninguna indicación temporal.\n"
        f"- cria_sexo: 'H' para hembra/ternera/becerra, 'M' para macho/ternero/becerro.\n"
        f"- cria_raza: raza de la cría (ej. 'Limusina', 'Retinta', 'Charolais'). Si no se menciona, usa la más probable o 'Retinta'.\n"
        f"- cria_recinto: 'PASTO' o 'CEBADERO' (por defecto 'PASTO').\n"
        f"- cria_finca: nombre exacto de la finca mencionada para ubicar la cría si el usuario la nombra (ej. '{fincas_disponibles[0] if fincas_disponibles else 'Finca Principal'}'). Si no menciona finca, devolver null.\n"
        f"- intencion: 'REGISTRAR_PARTO' si describe un parto o nacimiento."
    )

    if isinstance(texto_o_audio, (bytes, bytearray)):
        part = types.Part.from_bytes(data=bytes(texto_o_audio), mime_type=audio_content_type)
        contents = [part, "Extrae los datos de registro de parto a partir de este audio."]
    elif hasattr(texto_o_audio, 'read'):
        audio_bytes = texto_o_audio.read()
        part = types.Part.from_bytes(data=audio_bytes, mime_type=audio_content_type)
        contents = [part, "Extrae los datos de registro de parto a partir de este audio."]
    else:
        contents = str(texto_o_audio)

    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type='application/json',
            response_schema=ComandoPartoOutput,
            temperature=0.1,
        )
    )

    res_dict = json.loads(response.text)
    if res_dict.get('crotal_madre'):
        res_dict['crotal_madre'] = normalizar_crotal(res_dict['crotal_madre'])
    if res_dict.get('cria_crotal'):
        res_dict['cria_crotal'] = normalizar_crotal(res_dict['cria_crotal'])
    return res_dict


def procesar_comando_parto(texto_o_audio, audio_content_type: str = 'audio/webm', fincas_disponibles: list = None) -> dict:
    """
    Punto de entrada principal para el procesamiento de comandos de parto (texto o audio).
    Utiliza Gemini 2.5 Flash si GEMINI_API_KEY está presente, con un timeout estricto de 8 segundos.
    Si se agota el tiempo de espera o falla la conexión, conmuta silenciosamente al modo fallback sin error 500.
    """
    api_key = getattr(settings, 'GEMINI_API_KEY', '') or ''
    
    if api_key.strip():
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_llamar_gemini, texto_o_audio, audio_content_type, fincas_disponibles)
                return future.result(timeout=8.0)
        except FuturesTimeoutError:
            logger.warning("Timeout (8s) al invocar Gemini API. Conmutando a fallback regex.")
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
                "error": "El servicio de IA ha tardado demasiado en responder. Inténtelo de nuevo o use la entrada de texto."
            }
        except Exception as e:
            logger.warning(f"Error al invocar Gemini API ({e}). Usando fallback regex.")
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
                "error": f"Error al comunicar con Gemini: {str(e)}"
            }

    # Modo Simulado / Fallback sin API key
    if isinstance(texto_o_audio, str):
        return _extraer_comando_parto_regex(texto_o_audio, fincas_disponibles=fincas_disponibles)
    
    # Audio recibido sin API key configurada
    return {
        "intencion": "REGISTRAR_PARTO",
        "crotal_madre": None,
        "fecha_parto": timezone.now().date().strftime('%Y-%m-%d'),
        "cria_crotal": None,
        "cria_sexo": "H",
        "cria_raza": "Retinta",
        "cria_recinto": "PASTO",
        "cria_finca": None,
        "error": "El análisis de audio directo requiere configurar la variable GEMINI_API_KEY."
    }

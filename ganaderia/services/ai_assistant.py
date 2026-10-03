import time
import re
import json
import logging
from datetime import date, timedelta
from typing import Optional, Literal, List
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from django.conf import settings
from django.utils import timezone
from pydantic import BaseModel, Field

try:
    from google import genai
    from google.genai import types
    from google.genai import errors as genai_errors
except ImportError:
    genai = None
    types = None
    genai_errors = None

import io
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
CANDIDATE_MODELS = [
    "gemini-3.8-flash",
]


def get_genai_client():
    """
    Inicializa el cliente oficial de Google GenAI flexibilizando la clave de API.
    Acepta tanto las API Keys estándar (ej. AIza...) como los nuevos tokens con prefijo 'AQ.'
    sin comprobaciones rígidas de longitud o formato.
    """
    if genai is None:
        return None
    api_key = getattr(settings, 'GEMINI_API_KEY', '').strip()
    if not api_key:
        return None
    return genai.Client(api_key=api_key)


class AnimalImportItem(BaseModel):
    crotal: str = Field(description="Crotal obligatorio de 4 dígitos numéricos (ej. '4001', '0015').")
    sexo: str = Field(default="H", description="Sexo del animal: 'H' (hembra) o 'M' (macho).")
    raza: Optional[str] = Field(default="Limusina", description="Raza del animal (ej. Limusina, Retinta, Charolesa, etc.).")
    fecha_nacimiento: Optional[str] = Field(default=None, description="Fecha de nacimiento en formato YYYY-MM-DD o null si no se conoce.")
    crotal_madre: Optional[str] = Field(default=None, description="Crotal de 4 dígitos de la madre o null si no se conoce / es fundador.")
    sub_ubicacion: Optional[str] = Field(default="PASTO", description="Recinto: 'PASTO', 'CEBADERO' o 'APARTADO'.")


class LoteImportOutput(BaseModel):
    intencion: str = Field(default="IMPORTAR_LOTE", description="Intención del comando.")
    finca_nombre: Optional[str] = Field(default=None, description="Nombre de la finca indicada en el lote o documento, o null.")
    animales: List[AnimalImportItem] = Field(default_factory=list, description="Lista de animales a importar.")


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


def _llamar_gemini(texto_o_audio, audio_content_type: str = 'audio/webm', fincas_disponibles: list = None, model: str = None) -> dict:
    """Llama a la API oficial de Google GenAI con structured outputs."""
    if genai is None or types is None:
        raise ImportError("El paquete google-genai no está disponible.")

    client = get_genai_client()
    if client is None:
        raise ValueError("Clave GEMINI_API_KEY no configurada o vacía.")

    modelo_a_usar = model or DEFAULT_GEMINI_MODEL
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
        model=modelo_a_usar,
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


def _clasificar_error_gemini(exc) -> tuple[bool, bool]:
    """
    Clasifica la excepción en (es_503, es_404_o_descartar).
    - es_503: error 503 UNAVAILABLE / saturación (debe reintentarse hasta 3 veces con [2.0, 4.0, 6.0]s).
    - es_404_o_descartar: error 404 NOT_FOUND u otro no reintentable en el mismo modelo (salto inmediato al siguiente).
    """
    code = getattr(exc, 'code', None)
    msg = str(exc).lower()

    if (code == 503) or any(term in msg for term in ("503", "high demand", "unavailable", "overloaded", "resource exhausted")):
        return True, False
    if (code == 404) or ("404" in msg and "not found" in msg):
        return False, True
    if code == 429:
        return True, False
    return False, False


def procesar_comando_parto(texto_o_audio, audio_content_type: str = 'audio/webm', fincas_disponibles: list = None) -> dict:
    """
    Punto de entrada principal para el procesamiento de comandos de parto (texto o audio).
    Itera sobre la escalera de modelos (DEFAULT_GEMINI_MODEL + CANDIDATE_MODELS).
    - Ante 503 UNAVAILABLE: hasta 3 reintentos con esperas progresivas [2.0, 4.0, 6.0]s antes de abandonar el modelo.
    - Ante 404 NOT_FOUND: no reintenta; salta inmediatamente al siguiente candidato.
    - Si todos fallan, conmuta limpiamente al fallback regex local o devuelve mensaje amigable.
    """
    api_key = getattr(settings, 'GEMINI_API_KEY', '') or ''
    
    if api_key.strip():
        modelos_a_probar = [DEFAULT_GEMINI_MODEL] + [m for m in CANDIDATE_MODELS if m != DEFAULT_GEMINI_MODEL]
        last_exception = None

        for idx_m, modelo in enumerate(modelos_a_probar):
            # 1 llamada inicial + hasta 3 reintentos en 503
            delays_503 = [2.0, 4.0, 6.0]
            max_intentos = 1 + len(delays_503)

            for intento in range(max_intentos):
                try:
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(
                            _llamar_gemini,
                            texto_o_audio,
                            audio_content_type,
                            fincas_disponibles,
                            model=modelo
                        )
                        return future.result(timeout=8.0)
                except FuturesTimeoutError as e:
                    logger.warning(f"Timeout (8s) con modelo {modelo} en intento {intento + 1}.")
                    last_exception = e
                    break  # En timeout pasamos al siguiente candidato
                except Exception as e:
                    last_exception = e
                    es_503, es_404 = _clasificar_error_gemini(e)

                    if es_404:
                        logger.warning(f"Modelo {modelo} no encontrado (404 NOT_FOUND). Saltando inmediatamente al siguiente.")
                        break

                    if es_503 and intento < len(delays_503):
                        espera = delays_503[intento]
                        logger.warning(
                            f"Servidor sobrecargado (503/429) en modelo {modelo} (intento {intento + 1}). "
                            f"Reintentando en {espera}s..."
                        )
                        time.sleep(espera)
                        continue
                    elif es_503:
                        logger.warning(f"Modelo {modelo} agotó los 3 reintentos en 503. Saltando al siguiente modelo candidato.")
                        time.sleep(0.5)
                        break
                    else:
                        logger.warning(f"Error no recuperable en modelo {modelo} ({e}). Saltando al siguiente candidato.")
                        break

        # Si todos los modelos de la escalera han fallado
        logger.warning(f"Todos los modelos de Gemini fallaron. Último error: {last_exception}. Conmutando a fallback.")
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
            "error": "Los servidores de Google AI están experimentando un pico de saturación temporal. Por favor, reintenta en unos instantes."
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


def _extraer_tabla_pdf_dinamica(texto: str, fincas_disponibles: list = None) -> list:
    """
    Parser heurístico dinámico para tablas de documentos PDF de censo con columnas en orden variable.
    Detecta la cabecera por palabras clave y mapea la posición/índice de cada columna.
    Extrae fila a fila evitando solapamientos entre crotales, fechas, órdenes o razas.
    """
    lineas = [ln.strip() for ln in texto.splitlines() if ln.strip()]
    if not lineas:
        return []

    # 1. Buscar la fila de encabezados
    header_idx = -1
    col_map = {}  # 'crotal': idx, 'sexo': idx, ...

    for i, linea in enumerate(lineas):
        linea_lower = linea.lower()
        # Una línea de cabecera debe contener al menos dos de estas palabras clave
        kw_hits = sum(1 for kw in ["crotal", "identificador", "chapa", "sexo", "sex", "nacimiento", "f. nac", "fecha", "raza", "madre", "ubicación", "recinto"] if kw in linea_lower)
        if kw_hits >= 2 and ("crotal" in linea_lower or "identificador" in linea_lower or "chapa" in linea_lower):
            # Posible fila de cabecera
            # Dividir por separadores: '|', '\t', o 2 o más espacios
            partes = [p.strip() for p in re.split(r'\||\t|\s{2,}', linea) if p.strip()]
            if len(partes) >= 2:
                # Mapear cada columna por nombre
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

    animales_extraidos = []

    # 2. Iterar filas siguientes
    for linea in lineas[header_idx + 1:]:
        linea_l = linea.lower()
        # Ignorar líneas de pie de página, totales o cabeceras repetidas
        if any(term in linea_l for term in ["página", "pagina", "total", "resumen", "diputación", "diputacion", "explotación", "explotacion"]):
            continue
        # Ignorar si es otra cabecera repetida
        if "crotal" in linea_l and ("sexo" in linea_l or "raza" in linea_l):
            continue

        # Dividir celdas con el mismo criterio
        celdas = [c.strip() for c in re.split(r'\||\t|\s{2,}', linea) if c.strip()]
        if not celdas:
            continue

        # Extraer crotal según el índice mapeado
        idx_crotal = col_map.get('crotal')
        if idx_crotal is None or idx_crotal >= len(celdas):
            continue

        val_crotal_raw = celdas[idx_crotal]
        # Buscar el token de 4 dígitos numéricos en la celda del crotal
        m_crotal = re.search(r'\b(\d{4})\b', val_crotal_raw)
        if not m_crotal:
            # Reintentar si tiene 1-3 dígitos y rellenar con ceros
            m_crotal = re.search(r'\b(\d{1,4})\b', val_crotal_raw)
            if not m_crotal:
                continue
            crotal = normalizar_crotal(m_crotal.group(1))
        else:
            crotal = m_crotal.group(1)

        if not crotal or len(crotal) != 4 or not crotal.isdigit():
            continue

        # Sexo
        sexo = 'H'
        idx_sexo = col_map.get('sexo')
        if idx_sexo is not None and idx_sexo < len(celdas):
            val_sexo = celdas[idx_sexo].strip().lower()
            if any(s in val_sexo for s in ["hembra", "vaca", "novilla"]) or val_sexo == "h":
                sexo = 'H'
            elif any(s in val_sexo for s in ["macho", "toro", "buey"]) or val_sexo == "m":
                sexo = 'M'
            elif "h" in val_sexo and "m" not in val_sexo:
                sexo = 'H'
            elif "m" in val_sexo and "h" not in val_sexo:
                sexo = 'M'

        # Raza
        raza = 'Limusina'
        idx_raza = col_map.get('raza')
        if idx_raza is not None and idx_raza < len(celdas):
            val_raza = celdas[idx_raza].lower()
            for r_key, r_nom in [
                ("limusin", "Limusina"), ("limosina", "Limusina"), ("charol", "Charolesa"),
                ("retinta", "Retinta"), ("morucha", "Morucha"), ("angus", "Angus"),
                ("frisona", "Frisona"), ("avileña", "Avileña-Negra Ibérica"), ("cruzad", "Cruzado")
            ]:
                if r_key in val_raza:
                    raza = r_nom
                    break

        # Fecha de nacimiento
        fecha_nac = None
        idx_fnac = col_map.get('fecha_nacimiento')
        if idx_fnac is not None and idx_fnac < len(celdas):
            val_fnac = celdas[idx_fnac]
            m_f = re.search(r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b', val_fnac)
            if m_f:
                d, m, y = map(int, m_f.groups())
                try:
                    fecha_nac = date(y, m, d).strftime('%Y-%m-%d')
                except ValueError:
                    fecha_nac = None

        # Madre
        crotal_madre = None
        idx_madre = col_map.get('crotal_madre')
        if idx_madre is not None and idx_madre < len(celdas):
            val_madre = celdas[idx_madre].strip().lower()
            if not any(f in val_madre for f in ["fundador", "fundadora", "sin madre", "-", "null", "none"]):
                m_mad = re.search(r'\b(\d{1,4})\b', val_madre)
                if m_mad:
                    crotal_madre = normalizar_crotal(m_mad.group(1))

        # Recinto / Sub-ubicación
        sub_ubicacion = 'PASTO'
        idx_ubic = col_map.get('sub_ubicacion')
        if idx_ubic is not None and idx_ubic < len(celdas):
            val_ubic = celdas[idx_ubic].upper()
            if "CEBADERO" in val_ubic:
                sub_ubicacion = 'CEBADERO'
            elif "APARTADO" in val_ubic:
                sub_ubicacion = 'APARTADO'
            elif "BAJA" in val_ubic:
                sub_ubicacion = 'BAJA'

        animales_extraidos.append({
            "crotal": crotal,
            "sexo": sexo,
            "raza": raza,
            "fecha_nacimiento": fecha_nac,
            "crotal_madre": crotal_madre,
            "sub_ubicacion": sub_ubicacion,
        })

    return animales_extraidos


def _extraer_lote_regex(texto: str, fincas_disponibles: list = None) -> dict:
    """
    Parser simulado / fallback para importar lotes de animales a partir de texto o dictado.
    Primero intenta analizar como tabla estructurada dinámica por encabezados.
    Si no detecta tabla, recurre al parser de lenguaje natural / regex libre.
    """
    texto_lower = texto.lower()

    # 1. Detectar finca si se menciona
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

    # Intentar extracción tabular dinámica por encabezados
    animales_tabla = _extraer_tabla_pdf_dinamica(texto, fincas_disponibles=fincas_disponibles)
    if animales_tabla:
        return {
            "intencion": "IMPORTAR_LOTE",
            "finca_nombre": finca_detectada,
            "animales": animales_tabla,
        }

    # Recinto global
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
    
    # Encontrar todas las menciones a números candidatos
    candidatos = re.finditer(r'\b(\d{1,4})\b', texto)
    posiciones = []
    for c in candidatos:
        val = c.group(1)
        # Ignorar si es el año de nacimiento
        if anio_nacimiento and val == anio_nacimiento:
            continue
        posiciones.append((c.start(), c.end(), val))

    for idx, (start, end, crotal_raw) in enumerate(posiciones):
        next_start = posiciones[idx + 1][0] if idx + 1 < len(posiciones) else len(texto)
        ventana = texto[start:next_start].lower()

        c_norm = normalizar_crotal(crotal_raw)
        if not c_norm:
            continue

        # Sexo
        sexo = "H"
        if any(w in ventana for w in ["macho", "ternero", "becerro", "toro"]):
            sexo = "M"
        elif any(w in ventana for w in ["hembra", "ternera", "becerra", "vaca"]):
            sexo = "H"

        # Raza
        raza = "Limusina"
        for r_key, r_nom in [
            ("limusin", "Limusina"), ("limosina", "Limusina"), ("charol", "Charolesa"),
            ("retinta", "Retinta"), ("morucha", "Morucha"), ("angus", "Angus"),
            ("frisona", "Frisona"), ("avileña", "Avileña-Negra Ibérica"), ("cruzad", "Cruzado")
        ]:
            if r_key in ventana:
                raza = r_nom
                break

        # Madre: buscar si se menciona "hijo de XXXX" o "madre XXXX"
        crotal_madre = None
        m_madre = re.search(r'(?:madre|hij[ao]\s+de)\s*(?:#|n[ºo])?\s*(\d{1,4})\b', ventana)
        if m_madre:
            crotal_madre = normalizar_crotal(m_madre.group(1))

        # Fecha nacimiento
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


def _llamar_gemini_lote(texto=None, archivo_bytes=None, mime_type='application/pdf', fincas_disponibles: list = None, model: str = None) -> dict:
    """Invoca Gemini oficial para extraer estructuradamente un lote de animales desde PDF o texto."""
    if genai is None or types is None:
        raise ImportError("El paquete google-genai no está disponible.")

    client = get_genai_client()
    if client is None:
        raise ValueError("Clave GEMINI_API_KEY no configurada o vacía.")

    modelo_a_usar = model or DEFAULT_GEMINI_MODEL
    hoy = timezone.now().date()
    hoy_iso = hoy.strftime('%Y-%m-%d')
    fincas_str = ", ".join(fincas_disponibles) if fincas_disponibles else "no especificadas"

    system_instruction = (
        f"Eres el Asistente Experto en Gestión Ganadera Bovina para importación de censos. "
        f"Fecha actual: {hoy_iso}. Fincas disponibles en la explotación: [{fincas_str}]. "
        f"Tu tarea es analizar el documento PDF oficial o el texto/orden proporcionado y extraer la lista completa de animales a censar. "
        f"Reglas estrictas:\n"
        f"1. crotal: Obligatorio. Formato de 4 dígitos numéricos (ej. '4001', '0015'). Normaliza números enteros a 4 dígitos rellenando con ceros si es preciso.\n"
        f"2. sexo: 'H' para hembra/vaca/ternera/novilla, 'M' para macho/toro/buey/ternero/becerro.\n"
        f"3. raza: Limusina, Retinta, Charolesa, etc. (por defecto 'Limusina' si no se precisa).\n"
        f"4. fecha_nacimiento: YYYY-MM-DD si figura en el documento o se deduce del texto; null si no se conoce.\n"
        f"5. crotal_madre: Crotal de 4 dígitos de la vaca madre si se indica explícitamente. Si no figura o el animal es fundador / sin madre, DEBES devolver null.\n"
        f"6. sub_ubicacion: 'PASTO', 'CEBADERO' o 'APARTADO' (por defecto 'PASTO').\n"
        f"7. finca_nombre: Nombre de la finca de la explotación si se menciona en el documento o texto; null si no figura."
    )

    contents = []
    if archivo_bytes:
        part = types.Part.from_bytes(data=bytes(archivo_bytes), mime_type=mime_type)
        contents.append(part)
        contents.append("Extrae todos los animales del documento oficial adjunto siguiendo el esquema estructurado.")
    elif texto:
        contents.append(str(texto))

    response = client.models.generate_content(
        model=modelo_a_usar,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type='application/json',
            response_schema=LoteImportOutput,
            temperature=0.1,
        )
    )

    res_dict = json.loads(response.text)
    # Sanitizar y normalizar crotales
    if res_dict.get('animales'):
        for item in res_dict['animales']:
            if item.get('crotal'):
                item['crotal'] = normalizar_crotal(item['crotal'])
            if item.get('crotal_madre'):
                item['crotal_madre'] = normalizar_crotal(item['crotal_madre'])
    return res_dict


def procesar_importacion_lote(texto=None, archivo_bytes=None, mime_type='application/pdf', fincas_disponibles: list = None) -> dict:
    """
    Punto de entrada principal para importar lotes de animales (PDF oficial o texto/dictado).
    Estrategia Local-First y resiliencia de API:
    1. Si el archivo PDF contiene texto digital extraíble (pypdf.extract_text()):
       Se procesa directamente con el parser local de encabezados dinámicos (_extraer_tabla_pdf_dinamica).
       Si detecta animales, devuelve el resultado de inmediato con alta velocidad y cero dependencia de cuota externa.
    2. Si el PDF no contiene texto plano (es un documento escaneado/imagen) o la extracción devuelve 0 animales:
       Invoca la API de Gemini multimodal con los bytes del archivo.
    3. Si la llamada a Gemini arroja 503 UNAVAILABLE: no bloquear la interfaz; muestra mensaje descriptivo y
       da la opción de contingencia.
    """
    api_key = getattr(settings, 'GEMINI_API_KEY', '') or ''

    # 1. Extractor local de texto como primera opción si se recibe PDF (Estrategia Local-First)
    texto_extraido_pdf = ""
    if archivo_bytes and PdfReader is not None:
        try:
            reader = PdfReader(io.BytesIO(archivo_bytes))
            for page in reader.pages:
                texto_extraido_pdf += (page.extract_text() or "") + "\n"
            texto_extraido_pdf = texto_extraido_pdf.strip()
            if texto_extraido_pdf:
                logger.info(f"Texto digital extraído del PDF ({len(texto_extraido_pdf)} caracteres). Intentando procesamiento Local-First.")
                # Procesar directamente con el parser local dinámico
                res_local = _extraer_lote_regex(texto_extraido_pdf, fincas_disponibles=fincas_disponibles)
                if res_local.get('animales') and len(res_local['animales']) > 0:
                    logger.info(f"Extracción local completada exitosamente: {len(res_local['animales'])} animales detectados.")
                    return res_local
        except Exception as e:
            logger.warning(f"No se pudo extraer texto plano del PDF: {e}")

    # Si es texto plano directo del usuario (dictado o texto libre)
    if texto and not archivo_bytes:
        # Intentar parser local dinámico / regex primero si no hay API key o como fallback rápido
        if not api_key.strip():
            return _extraer_lote_regex(texto, fincas_disponibles=fincas_disponibles)

    # 2. Solo si el PDF no contiene texto plano (documento escaneado/imagen) o es texto/dictado complejo:
    llm_texto = texto or texto_extraido_pdf
    llm_archivo_bytes = archivo_bytes if not texto_extraido_pdf else None

    texto_para_fallback = texto or texto_extraido_pdf

    if api_key.strip():
        modelos_a_probar = [DEFAULT_GEMINI_MODEL] + [m for m in CANDIDATE_MODELS if m != DEFAULT_GEMINI_MODEL]
        last_exception = None

        for idx_m, modelo in enumerate(modelos_a_probar):
            delays_503 = [2.0, 4.0, 6.0]
            max_intentos = 1 + len(delays_503)

            for intento in range(max_intentos):
                try:
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(
                            _llamar_gemini_lote,
                            texto=llm_texto,
                            archivo_bytes=llm_archivo_bytes,
                            mime_type=mime_type,
                            fincas_disponibles=fincas_disponibles,
                            model=modelo
                        )
                        return future.result(timeout=15.0)
                except FuturesTimeoutError as e:
                    logger.warning(f"Timeout (15s) en importación de lote con modelo {modelo} (intento {intento + 1}).")
                    last_exception = e
                    break
                except Exception as e:
                    last_exception = e
                    es_503, es_404 = _clasificar_error_gemini(e)

                    if es_404:
                        logger.warning(f"Modelo {modelo} no encontrado (404 NOT_FOUND). Saltando inmediatamente al siguiente.")
                        break

                    if es_503 and intento < len(delays_503):
                        espera = delays_503[intento]
                        logger.warning(
                            f"Servidor sobrecargado (503/429) en importación con modelo {modelo} (intento {intento + 1}). "
                            f"Reintentando en {espera}s..."
                        )
                        time.sleep(espera)
                        continue
                    elif es_503:
                        logger.warning(f"Modelo {modelo} agotó los 3 reintentos en 503. Saltando al siguiente modelo.")
                        time.sleep(0.5)
                        break
                    else:
                        logger.warning(f"Error no recuperable en modelo {modelo} ({e}). Saltando al siguiente candidato.")
                        break

        # Fallback si todos los modelos fallaron
        logger.warning(f"Todos los modelos fallaron para importación de lote. Último error: {last_exception}.")
        if texto_para_fallback:
            res_local = _extraer_lote_regex(texto_para_fallback, fincas_disponibles=fincas_disponibles)
            if res_local.get('animales'):
                res_local['aviso_banner'] = (
                    "⚠️ El censo fue procesado mediante el analizador local debido a una saturación temporal "
                    "en los servidores de IA. Revise la lista antes de guardar."
                )
                return res_local
        return {
            "intencion": "IMPORTAR_LOTE",
            "finca_nombre": None,
            "animales": [],
            "error": "Los servidores de Google AI están experimentando un pico de saturación temporal. Por favor, reintenta la subida del documento en unos instantes."
        }

    # Modo sin API key
    if texto_para_fallback:
        return _extraer_lote_regex(texto_para_fallback, fincas_disponibles=fincas_disponibles)

    return {
        "intencion": "IMPORTAR_LOTE",
        "finca_nombre": None,
        "animales": [],
        "error": "El análisis de documentos PDF requiere configurar la variable GEMINI_API_KEY."
    }

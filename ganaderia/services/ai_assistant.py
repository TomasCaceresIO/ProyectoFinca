import re
import json
import logging
from datetime import date, timedelta
from typing import Optional, Literal
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


MESES_ESP = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
    'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
    'septiembre': 9, 'setiembre': 9, 'octubre': 10,
    'noviembre': 11, 'diciembre': 12
}


def _extraer_comando_parto_regex(texto: str) -> dict:
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

    # 5. Extraer crotales de forma estricta evitando números de año
    # Identificar si un número de 4 dígitos forma parte de una fecha o año
    # Detectamos años de 4 dígitos (19xx o 20xx) en contextos temporales como "de 2022", "en 2022", "/2022", "-2022"
    anios_fecha = set()
    for m in re.finditer(r'(?:de|del|en|[/-]|\b)\s*(\d{4})\b', texto_lower):
        posible_anio = m.group(1)
        sub_ant = texto_lower[max(0, m.start() - 25):m.start()]
        if any(mes in sub_ant for mes in MESES_ESP.keys()) or any(p in sub_ant for p in ["año", "ano", "del", "de", "en", "el"]):
            if posible_anio.startswith(('19', '20')):
                anios_fecha.add(posible_anio)

    crotal_madre = None
    cria_crotal = None

    # Intentar capturar por contexto sintáctico explícito
    m_madre = re.search(r'(?:vaca|madre|la|el|hembra)\s*(?:#|n[ºo]|número)?\s*(\d{4})\b', texto, re.IGNORECASE)
    m_cria = re.search(r'(?:terner[ao]|becerr[ao]|cría|cria|hijo|hija|crotal)\s*(?:#|n[ºo]|número)?\s*(\d{4})\b', texto, re.IGNORECASE)

    if m_madre:
        crotal_madre = m_madre.group(1)

    if m_cria:
        cria_crotal = m_cria.group(1)

    # Extraer todos los números de 4 dígitos
    todos_4d = [n for n in re.findall(r'\b\d{4}\b', texto)]
    # Filtrar aquellos que sean años de fecha detectados, SALVO que estuvieran explícitamente precedidos de 'crotal'
    crotales_candidatos = []
    for num in todos_4d:
        # Comprobar si num está precedido directamente por "crotal"
        idx = texto.find(num)
        es_crotal_explicito = False
        if idx != -1:
            segmento_previo = texto[max(0, idx - 15):idx].lower()
            if "crotal" in segmento_previo:
                es_crotal_explicito = True
        if num in anios_fecha and not es_crotal_explicito:
            continue
        crotales_candidatos.append(num)

    # Si se capturó cria_crotal sintácticamente pero no crotal_madre:
    # no podemos reusar cria_crotal como crotal_madre
    candidatos_restantes = [c for c in crotales_candidatos if c != cria_crotal and c != crotal_madre]

    if not crotal_madre:
        if m_madre:
            crotal_madre = m_madre.group(1)
        elif len(candidatos_restantes) >= 1 and not cria_crotal:
            # Solo si no hay sintaxis explícita, se toma el primero como madre
            crotal_madre = candidatos_restantes.pop(0)

    if not cria_crotal:
        if m_cria:
            cria_crotal = m_cria.group(1)
        elif len(candidatos_restantes) >= 1:
            cria_crotal = candidatos_restantes.pop(0)

    return {
        "intencion": "REGISTRAR_PARTO",
        "crotal_madre": crotal_madre,
        "fecha_parto": fecha_parto.strftime('%Y-%m-%d') if fecha_parto else hoy.strftime('%Y-%m-%d'),
        "cria_crotal": cria_crotal,
        "cria_sexo": sexo,
        "cria_raza": raza,
        "cria_recinto": recinto,
    }


def _llamar_gemini(texto_o_audio, audio_content_type: str = 'audio/webm') -> dict:
    """Llama a la API oficial de Google GenAI con structured outputs."""
    if genai is None or types is None:
        raise ImportError("El paquete google-genai no está disponible.")

    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    client = genai.Client(api_key=api_key)

    hoy = timezone.now().date()
    hoy_iso = hoy.strftime('%Y-%m-%d')
    hoy_es = hoy.strftime('%d/%m/%Y')

    system_instruction = (
        f"Eres el Asistente de Inteligencia Artificial de un sistema de gestión ganadera bovina. "
        f"La fecha de hoy de referencia es {hoy_iso} ({hoy_es}). "
        f"Tu cometido es extraer con exactitud los datos para registrar un parto de vaca y su cría. "
        f"Reglas estrictas de extracción:\n"
        f"- crotal_madre: Crotal obligatorio de exactamente 4 dígitos de la vaca madre (ej. '3014', '0001'). "
        f"Si no se especifica de forma explícita en el mensaje o falta, DEBES devolver null.\n"
        f"- cria_crotal: Crotal obligatorio de exactamente 4 dígitos asignado a la cría nacida (ej. '5012', '0003'). "
        f"PROHIBIDO TERMINANTEMENTE asignar números de año (ej. 19xx, 20xx, como 2022, 2024, etc.) como cria_crotal, "
        f"a menos que el usuario diga explícitamente 'con crotal 2022'. Si no se indica explícitamente un crotal para la cría, "
        f"DEBES devolver null.\n"
        f"- fecha_parto: Fecha del parto en formato YYYY-MM-DD. Si el usuario indica una fecha explícita pasada o histórica "
        f"(ej. '10 de octubre de 2022', '10/10/2022'), DEBES respetarla y parsearla fielmente (ej. '2022-10-10'). "
        f"Solo debes usar la fecha de hoy ({hoy_iso}) si el usuario dice 'hoy', 'ha nacido hoy', o no aporta ninguna indicación temporal.\n"
        f"- cria_sexo: 'H' para hembra/ternera/becerra, 'M' para macho/ternero/becerro.\n"
        f"- cria_raza: raza de la cría (ej. 'Limusina', 'Retinta', 'Charolais'). Si no se menciona, usa la más probable o 'Retinta'.\n"
        f"- cria_recinto: 'PASTO' o 'CEBADERO' (por defecto 'PASTO').\n"
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

    return json.loads(response.text)


def procesar_comando_parto(texto_o_audio, audio_content_type: str = 'audio/webm') -> dict:
    """
    Punto de entrada principal para el procesamiento de comandos de parto (texto o audio).
    Utiliza Gemini 2.5 Flash si GEMINI_API_KEY está presente, o conmuta a modo simulado/fallback con regex.
    """
    api_key = getattr(settings, 'GEMINI_API_KEY', '') or ''
    
    if api_key.strip():
        try:
            return _llamar_gemini(texto_o_audio, audio_content_type)
        except Exception as e:
            logger.warning(f"Error al invocar Gemini API ({e}). Usando fallback regex.")
            if isinstance(texto_o_audio, str):
                return _extraer_comando_parto_regex(texto_o_audio)
            raise e

    # Modo Simulado / Fallback sin API key
    if isinstance(texto_o_audio, str):
        return _extraer_comando_parto_regex(texto_o_audio)
    
    # Audio recibido sin API key configurada
    return {
        "intencion": "REGISTRAR_PARTO",
        "crotal_madre": None,
        "fecha_parto": timezone.now().date().strftime('%Y-%m-%d'),
        "cria_crotal": None,
        "cria_sexo": "H",
        "cria_raza": "Retinta",
        "cria_recinto": "PASTO",
        "error": "El análisis de audio directo requiere configurar la variable GEMINI_API_KEY."
    }

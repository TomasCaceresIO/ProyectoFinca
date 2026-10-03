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


def _extraer_comando_parto_regex(texto: str) -> dict:
    """
    Parser simulado / fallback basado en reglas y expresiones regulares
    para extraer datos de registro de parto a partir de lenguaje natural.
    """
    texto_lower = texto.lower()
    hoy = timezone.now().date()
    
    # 1. Determinar fecha
    fecha_parto = hoy
    if "anteayer" in texto_lower:
        fecha_parto = hoy - timedelta(days=2)
    elif "ayer" in texto_lower:
        fecha_parto = hoy - timedelta(days=1)
    elif "pasado mañana" in texto_lower or "pasado manana" in texto_lower:
        fecha_parto = hoy + timedelta(days=2)
    elif "mañana" in texto_lower or "manana" in texto_lower:
        fecha_parto = hoy + timedelta(days=1)
    elif "hoy" in texto_lower:
        fecha_parto = hoy
    else:
        # Buscar formato DD/MM/YYYY o DD-MM-YYYY
        m_fecha = re.search(r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b', texto)
        if m_fecha:
            d, m, y = map(int, m_fecha.groups())
            try:
                fecha_parto = date(y, m, d)
            except ValueError:
                fecha_parto = hoy
        else:
            # Buscar formato YYYY-MM-DD
            m_iso = re.search(r'\b(\d{4})[/-](\d{1,2})[/-](\d{1,2})\b', texto)
            if m_iso:
                y, m, d = map(int, m_iso.groups())
                try:
                    fecha_parto = date(y, m, d)
                except ValueError:
                    fecha_parto = hoy

    # 2. Determinar sexo de la cría
    sexo = "H"
    if any(w in texto_lower for w in ["ternero", "becerro", "macho", "choto", "torito"]):
        sexo = "M"
    elif any(w in texto_lower for w in ["ternera", "becerra", "hembra", "novilla"]):
        sexo = "H"

    # 3. Determinar raza
    razas_comunes = {
        "limusin": "Limusina",
        "limusina": "Limusina",
        "retinta": "Retinta",
        "charol": "Charolais",
        "charolais": "Charolais",
        "charolesa": "Charolais",
        "avileña": "Avileña-Negra Ibérica",
        "avilena": "Avileña-Negra Ibérica",
        "morucha": "Morucha",
        "angus": "Aberdeen Angus",
        "rubia gallega": "Rubia Gallega",
        "berrenda": "Berrenda en Colorado",
    }
    raza = "Retinta"
    for k, v in razas_comunes.items():
        if k in texto_lower:
            raza = v
            break

    # 4. Determinar recinto
    recinto = "CEBADERO" if "cebadero" in texto_lower else "PASTO"

    # 5. Extraer crotales (números de 4 dígitos)
    crotales_4d = re.findall(r'\b\d{4}\b', texto)
    crotal_madre = None
    cria_crotal = None

    # Intentar capturar por contexto sintáctico
    m_madre = re.search(r'(?:vaca|madre|la)\s*(?:#|n[ºo]|número)?\s*(\d{4})\b', texto, re.IGNORECASE)
    m_cria = re.search(r'(?:terner[ao]|becerr[ao]|cría|cria|hijo|hija|crotal)\s*(?:#|n[ºo]|número)?\s*(\d{4})\b', texto, re.IGNORECASE)

    if m_madre:
        crotal_madre = m_madre.group(1)
    if m_cria:
        cria_crotal = m_cria.group(1)

    # Si no se capturaron por palabras clave, usar el orden de aparición
    if not crotal_madre and len(crotales_4d) >= 1:
        crotal_madre = crotales_4d[0]
    if not cria_crotal and len(crotales_4d) >= 2:
        # Tomar el segundo crotal distinto
        for c in crotales_4d:
            if c != crotal_madre:
                cria_crotal = c
                break

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
        f"Reglas de extracción:\n"
        f"- crotal_madre: crotal de 4 dígitos de la vaca madre (ej. '3014').\n"
        f"- fecha_parto: fecha en formato YYYY-MM-DD. Si se indica 'hoy', usa {hoy_iso}. Si se indica 'ayer', usa la fecha de ayer.\n"
        f"- cria_crotal: crotal de 4 dígitos asignado a la cría nacida (ej. '5012').\n"
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

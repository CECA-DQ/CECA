import asyncio
import json
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from src.adapters.llm.factory import get_llm_provider

router = APIRouter(prefix="/api/cesta", tags=["cesta"])

# ---------------------------------------------------------------------------
# Shared input model
# ---------------------------------------------------------------------------

class ContenidoEditor(BaseModel):
    titular: str
    entradilla: str
    cuerpo: str


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_S_ARCHIVO_DOCUMENTAL = """Eres un documentalista de televisión experto en hemeroteca española.
Dado el tema de esta noticia, proporciona: 3-5 noticias anteriores relevantes
sobre el mismo tema (con fecha aproximada y medio), 2-3 datos estadísticos
históricos relacionados, y 1-2 antecedentes legislativos o institucionales
clave. Formato: secciones claras con títulos en mayúsculas."""

_S_DOCUMENTACION_HISTORICA = """Eres un historiador y analista de contexto para medios de comunicación.
Dado el tema de esta noticia, explica: el origen histórico del problema o
situación (mínimo 5 años atrás), los hitos principales que han llevado a la
situación actual, y qué intentos anteriores de solución hubo y por qué
fallaron. Máximo 300 palabras, tono divulgativo."""

_S_DEEP_RESEARCH = """Eres un analista de investigación periodística. Dado el contenido,
realiza un análisis en profundidad que incluya: los actores clave
implicados y sus intereses, los datos que faltan o que deberían verificarse,
las fuentes primarias que habría que consultar, las preguntas que un
periodista de investigación haría, y el posible impacto a 6-12 meses.
Sé específico y crítico."""

_S_WEBSCRAPING = """Eres un asistente de búsqueda de fuentes periodísticas. Dado el tema
de esta noticia, sugiere: 5 búsquedas concretas en Google que el redactor
debería hacer (escríbelas tal como se escribirían en el buscador), 3 fuentes
oficiales o institucionales donde buscar datos primarios (con URL si la
conoces), y 3 expertos o perfiles de Twitter/X relevantes sobre este tema."""

_S_ANALISIS_COMPETENCIA = """Eres un analista de medios de comunicación español. Dado el tema de
esta noticia, indica cómo probablemente están cubriendo esta noticia: El País,
El Mundo, La Vanguardia, elDiario.es y RTVE. Para cada uno: el ángulo que
están usando, el titular que probablemente tienen, y qué elemento diferenciador
podríamos añadir nosotros para destacar."""

_S_FAKE_NEWS = """Eres un fact-checker experto en medios españoles. Analiza el siguiente
texto periodístico e identifica: AFIRMACIONES SIN FUENTE (datos concretos que
se presentan sin citar origen), DATOS CUESTIONABLES (cifras o hechos que
parecen inexactos o exagerados), POSIBLES SESGOS (lenguaje cargado o
perspectiva unilateral), y ELEMENTOS VERIFICADOS (lo que sí parece contrastado).
Al final asigna un semáforo: VERDE (texto fiable), AMARILLO (requiere revisión
en algunos puntos), ROJO (requiere verificación urgente antes de publicar).
Justifica el semáforo en una frase."""

_S_PROPIEDAD_INTELECTUAL = """Eres un asesor legal especializado en propiedad intelectual para medios
de comunicación. Analiza este texto y señala: fragmentos que podrían ser citas
directas de terceros sin atribución, uso de datos que podrían requerir licencia
o permiso, nombres de marcas o productos que requieran tratamiento especial,
y cualquier riesgo legal de publicación. Sé conciso y práctico."""

_S_DETECTOR_REPUTACION = """Eres un especialista en comunicación de crisis y reputación corporativa.
Analiza este texto y detecta: si contiene afirmaciones que podrían considerarse
difamatorias hacia personas o entidades, si hay información que podría usarse
como ataque de reputación, si algún actor mencionado podría reclamar derecho
de rectificación, y el nivel de riesgo reputacional del texto (BAJO/MEDIO/ALTO)
con recomendación de acción."""

_S_EXECUTIVE_SUMMARY = """Eres un redactor jefe de televisión. Genera exactamente
5 puntos clave, cada uno de máximo 20 palabras, en presente de indicativo,
sin jerga técnica. El resumen debe poder leerse en 30 segundos. Formato:
lista numerada, sin introducción ni conclusión."""

_S_REVERSE_NEWS = """Eres un redactor especializado en periodismo de contraste. Dado el
contenido de esta noticia, escribe la versión desde el ángulo opuesto: qué
argumentaría el bando contrario, qué datos contradicen la versión principal,
qué voces críticas dirían y por qué. No inventes datos, usa el mismo contexto
pero desde la perspectiva contraria. Máximo 200 palabras."""

_S_ALTERNATIVE_HISTORY = """Eres un analista político y social especializado en escenarios
alternativos. Dado el evento descrito en esta noticia, desarrolla el escenario
alternativo más relevante: qué hubiera pasado si la decisión principal hubiera
sido la contraria. Describe las consecuencias probables en 3 puntos concretos.
Tono analítico, no especulativo, apóyate en precedentes históricos reales."""

_S_POTENTIAL_HIGHLIGHTS = """Eres un productor de televisión experto en identificar los momentos
más impactantes de una noticia. Extrae los 5 datos, cifras o hechos más
llamativos de este texto. Para cada uno: el dato en sí (máx 15 palabras),
por qué es impactante, y una frase de titular que lo aproveche. Ordénalos
de más a menos impacto para la audiencia televisiva."""

_S_ANALISIS_ESTRATEGIAS = """Eres un analista de comunicación política y corporativa. Dado el
contenido de esta noticia, analiza: qué estrategia comunicativa hay detrás
del mensaje principal, quién se beneficia de que la noticia se cuente así,
qué se está omitiendo deliberadamente, y cuál es el timing estratégico
(por qué se publica o anuncia ahora). Sé directo y analítico."""

_S_SEO_TITLES = """Eres un experto en SEO para medios de comunicación digitales en español.
Genera 5 titulares distintos optimizados para buscadores.
Cada titular debe: tener entre 50-60 caracteres, incluir la palabra clave
principal, ser diferente en estructura a los otros cuatro (pregunta, dato
numérico, verbo de acción, nombre propio, consecuencia). Muéstralos numerados
con el conteo de caracteres entre paréntesis."""

_S_RRSS = """Eres un community manager experto en medios de comunicación españoles.
Genera tres posts para esta noticia, claramente separados con headers:

**TWITTER/X** (máx 280 caracteres, incluye 2-3 hashtags relevantes, tono directo)

**INSTAGRAM** (3 párrafos cortos, emojis pertinentes al tema, llamada a la
acción al final, tono más cercano)

**LINKEDIN** (tono profesional, contexto del impacto en el sector,
2-3 párrafos, sin emojis excesivos)

No superes el límite de Twitter bajo ningún concepto."""

_S_NEWSLETTER = """Eres un redactor especializado en newsletters periodísticas en español.
Adapta esta noticia en una pieza para newsletter con este formato exacto:

ASUNTO DEL EMAIL: (una línea llamativa, máx 60 caracteres)
SALUDO: (una frase corta y cercana)
LO QUE PASÓ: (2-3 frases del hecho principal, tono conversacional)
POR QUÉ IMPORTA: (1-2 frases de contexto o impacto para el lector)
LO QUE VIENE: (qué hay que seguir en las próximas horas o días)
CIERRE: (frase breve de despedida con marca del medio)"""

_S_CONTEXTUAL_IMAGE = """Eres un director de arte para medios de comunicación digitales.
Dado el tema de esta noticia, genera UN prompt en inglés listo para usar
en Midjourney o DALL-E 3 que produzca una imagen periodística apropiada.
El prompt debe incluir: descripción de la escena, estilo fotográfico
(documentary, photojournalism, editorial), iluminación, composición,
y aspectos técnicos (--ar 16:9 para Midjourney).
NO uses nombres de personas reales. Formato: solo el prompt, sin explicación."""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _build_user_message(contenido: ContenidoEditor) -> str:
    return (
        f"TITULAR: {contenido.titular}\n\n"
        f"ENTRADILLA: {contenido.entradilla}\n\n"
        f"CUERPO: {contenido.cuerpo}"
    )


async def _llamar_llm(contenido: ContenidoEditor, system: str, max_tokens: int = 1500) -> str:
    llm = get_llm_provider()
    response = await llm.generate(
        system=system,
        messages=[{"role": "user", "content": _build_user_message(contenido)}],
        temperature=0.7,
        max_tokens=max_tokens,
    )
    return response.text


def _extract_semaforo(text: str) -> str:
    upper = text.upper()
    # Check most severe first
    for color in ("ROJO", "AMARILLO", "VERDE"):
        if color in upper:
            return color
    return "AMARILLO"


# ---------------------------------------------------------------------------
# Investigación
# ---------------------------------------------------------------------------

@router.post("/archivo-documental")
async def archivo_documental(contenido: ContenidoEditor) -> dict:
    resultado = await _llamar_llm(contenido, _S_ARCHIVO_DOCUMENTAL)
    return {"resultado": resultado}


@router.post("/documentacion-historica")
async def documentacion_historica(contenido: ContenidoEditor) -> dict:
    resultado = await _llamar_llm(contenido, _S_DOCUMENTACION_HISTORICA, max_tokens=800)
    return {"resultado": resultado}


@router.post("/deep-research")
async def deep_research(contenido: ContenidoEditor) -> dict:
    resultado = await _llamar_llm(contenido, _S_DEEP_RESEARCH, max_tokens=2000)
    return {"resultado": resultado}


@router.post("/webscraping")
async def webscraping(contenido: ContenidoEditor) -> dict:
    resultado = await _llamar_llm(contenido, _S_WEBSCRAPING)
    return {"resultado": resultado}


@router.post("/analisis-competencia")
async def analisis_competencia(contenido: ContenidoEditor) -> dict:
    resultado = await _llamar_llm(contenido, _S_ANALISIS_COMPETENCIA)
    return {"resultado": resultado}


# ---------------------------------------------------------------------------
# Verificación
# ---------------------------------------------------------------------------

@router.post("/fake-news-control")
async def fake_news_control(contenido: ContenidoEditor) -> dict:
    resultado = await _llamar_llm(contenido, _S_FAKE_NEWS, max_tokens=1200)
    return {
        "resultado": resultado,
        "semaforo": _extract_semaforo(resultado),
    }


@router.post("/verificacion-propiedad-intelectual")
async def verificacion_propiedad_intelectual(contenido: ContenidoEditor) -> dict:
    resultado = await _llamar_llm(contenido, _S_PROPIEDAD_INTELECTUAL, max_tokens=800)
    return {"resultado": resultado}


@router.post("/detector-reputacion")
async def detector_reputacion(contenido: ContenidoEditor) -> dict:
    resultado = await _llamar_llm(contenido, _S_DETECTOR_REPUTACION, max_tokens=800)
    return {"resultado": resultado}


# ---------------------------------------------------------------------------
# Generación de contenido
# ---------------------------------------------------------------------------

@router.post("/executive-summary")
async def executive_summary(contenido: ContenidoEditor) -> dict:
    resultado = await _llamar_llm(contenido, _S_EXECUTIVE_SUMMARY, max_tokens=600)
    return {"resultado": resultado}


@router.post("/reverse-news")
async def reverse_news(contenido: ContenidoEditor) -> dict:
    resultado = await _llamar_llm(contenido, _S_REVERSE_NEWS, max_tokens=600)
    return {"resultado": resultado}


@router.post("/alternative-history")
async def alternative_history(contenido: ContenidoEditor) -> dict:
    resultado = await _llamar_llm(contenido, _S_ALTERNATIVE_HISTORY, max_tokens=800)
    return {"resultado": resultado}


@router.post("/potential-highlights")
async def potential_highlights(contenido: ContenidoEditor) -> dict:
    resultado = await _llamar_llm(contenido, _S_POTENTIAL_HIGHLIGHTS, max_tokens=1000)
    return {"resultado": resultado}


@router.post("/analisis-estrategias")
async def analisis_estrategias(contenido: ContenidoEditor) -> dict:
    resultado = await _llamar_llm(contenido, _S_ANALISIS_ESTRATEGIAS, max_tokens=1000)
    return {"resultado": resultado}


@router.post("/seo-titles")
async def seo_titles(contenido: ContenidoEditor) -> dict:
    resultado = await _llamar_llm(contenido, _S_SEO_TITLES, max_tokens=600)
    return {"resultado": resultado}


# ---------------------------------------------------------------------------
# Formatos de distribución
# ---------------------------------------------------------------------------

@router.post("/rrss-generation")
async def rrss_generation(contenido: ContenidoEditor) -> dict:
    resultado = await _llamar_llm(contenido, _S_RRSS, max_tokens=1000)
    return {"resultado": resultado}


@router.post("/newsletter")
async def newsletter(contenido: ContenidoEditor) -> dict:
    resultado = await _llamar_llm(contenido, _S_NEWSLETTER, max_tokens=800)
    return {"resultado": resultado}


@router.post("/contextual-image")
async def contextual_image(contenido: ContenidoEditor) -> dict:
    resultado = await _llamar_llm(contenido, _S_CONTEXTUAL_IMAGE, max_tokens=400)
    return {"resultado": resultado}


# ---------------------------------------------------------------------------
# Piezas TV desde texto editorial
# ---------------------------------------------------------------------------

_S_PIEZAS_TV = """Eres un redactor jefe de informativos de televisión con 20 años de experiencia.
A partir del contenido editorial de una noticia escrita, generas las piezas de producción televisiva
necesarias para emitir la noticia en antena.

Devuelve ÚNICAMENTE un objeto JSON válido con exactamente estas cinco claves:
presenter_lead, soundbites, vtr_script, graphics, rundown.
Sin texto previo, sin markdown, sin explicaciones, sin bloques de código.

presenter_lead — Cola de presentador (objeto):
- slug: identificador en MAYÚSCULAS (ej: "PREMIOS OSCAR 2027")
- text: texto que leerá el presentador en plató, máximo 5 líneas, estilo directo, presente de indicativo
- background_shots: array de 3-5 sugerencias de planos de fondo
- estimated_duration_seconds: número entre 20 y 40

soundbites — Totales (array de hasta 3 objetos):
- slug: "TOT " + NOMBRE + " " + TEMA
- speaker_name: nombre del declarante mencionado en el texto
- speaker_role: cargo o descripción
- quote: fragmento literal o paráfrasis del texto
- timecode: "00:00"
- duration_seconds: estimación

vtr_script — Guión VTR (objeto):
- presenter_intro: 2-3 líneas del presentador para dar paso
- narration: objeto con apertura, desarrollo (con marcas ((ENTRA TOTAL 1)) donde corresponda) y cierre
- estimated_duration: string (ej: "1:30")

graphics — Grafismo (objeto):
- headline: titular en MAYÚSCULAS, máximo 8 palabras
- bullets: array de 3-4 datos clave del texto
- source: fuente de los datos
- graphic_type: "PANTALLÓN COMPLETO" | "LOWER THIRD" | "ZÓCALO"

rundown — Escaleta (objeto):
- items: array ordenado con order, element, duration_seconds, notes
- total_duration_seconds: suma total
- total_duration_formatted: formato "M:SS" """

_S_VOICEOVER_SCRIPT = """Eres un redactor experto en informativos de televisión española.
A partir del contenido editorial de una noticia escrita, redacta la voz en off para una pieza informativa.

Reglas estrictas:
- Solo el texto narrado. Sin títulares, sin metadatos, sin explicaciones.
- Frases cortas. Estilo periodístico directo. Tiempo presente o pasado reciente.
- Entre 60 y 120 palabras. No más, no menos.
- El texto debe fluir de forma natural al escucharse en televisión.
- No repitas literalmente el titular. Empieza con gancho informativo."""

_VOICEOVER_STORAGE_DIR = Path("data/storage/voiceover_cesta")


@router.post("/piezas-tv")
async def piezas_tv_desde_texto(contenido: ContenidoEditor) -> dict:
    """Generate TV broadcast pieces (cola, VTR, totales, graphics, rundown) from editorial text."""
    raw = await _llamar_llm(contenido, _S_PIEZAS_TV, max_tokens=2000)
    try:
        # Strip markdown code fences if the model wraps the JSON
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        pieces = json.loads(cleaned.strip())
    except (json.JSONDecodeError, IndexError):
        pieces = {"raw": raw}
    return {"ok": True, "piezas": pieces}


@router.post("/voiceover-texto")
async def voiceover_desde_texto(contenido: ContenidoEditor) -> dict:
    """Generate voiceover script from editorial text, synthesize with TTS, return audio URL."""
    # 1. Generate script
    script = await _llamar_llm(contenido, _S_VOICEOVER_SCRIPT, max_tokens=300)

    # 2. Synthesize audio
    from src.adapters.tts.factory import get_tts_provider
    from src.config import settings
    tts = get_tts_provider()
    result = await tts.synthesize(
        text=script,
        voice_id=settings.tts_default_voice_id,
        language="es",
    )

    # 3. Store audio
    _VOICEOVER_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    audio_key = f"{uuid4()}.mp3"
    (_VOICEOVER_STORAGE_DIR / audio_key).write_bytes(result.audio)

    return {
        "ok": True,
        "script": script,
        "audio_url": f"/api/cesta/audio/{audio_key}",
        "duration_seconds": result.duration_seconds,
    }


@router.get("/audio/{audio_key}")
async def stream_cesta_audio(audio_key: str) -> Response:
    """Stream a voiceover MP3 generated by the cesta."""
    audio_path = _VOICEOVER_STORAGE_DIR / audio_key
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio not found")
    return Response(
        content=audio_path.read_bytes(),
        media_type="audio/mpeg",
        headers={"Content-Disposition": f"inline; filename=\"{audio_key}\""},
    )


# ---------------------------------------------------------------------------
# Combined endpoint
# ---------------------------------------------------------------------------

# Maps slug → (system_prompt, max_tokens)
_FUNCIONES: dict[str, tuple[str, int]] = {
    "archivo-documental":               (_S_ARCHIVO_DOCUMENTAL, 1500),
    "documentacion-historica":          (_S_DOCUMENTACION_HISTORICA, 800),
    "deep-research":                    (_S_DEEP_RESEARCH, 2000),
    "webscraping":                      (_S_WEBSCRAPING, 1500),
    "analisis-competencia":             (_S_ANALISIS_COMPETENCIA, 1500),
    "fake-news-control":                (_S_FAKE_NEWS, 1200),
    "verificacion-propiedad-intelectual": (_S_PROPIEDAD_INTELECTUAL, 800),
    "detector-reputacion":              (_S_DETECTOR_REPUTACION, 800),
    "executive-summary":                (_S_EXECUTIVE_SUMMARY, 600),
    "reverse-news":                     (_S_REVERSE_NEWS, 600),
    "alternative-history":              (_S_ALTERNATIVE_HISTORY, 800),
    "potential-highlights":             (_S_POTENTIAL_HIGHLIGHTS, 1000),
    "analisis-estrategias":             (_S_ANALISIS_ESTRATEGIAS, 1000),
    "seo-titles":                       (_S_SEO_TITLES, 600),
    "rrss-generation":                  (_S_RRSS, 1000),
    "newsletter":                       (_S_NEWSLETTER, 800),
    "contextual-image":                 (_S_CONTEXTUAL_IMAGE, 400),
}


class SeleccionRequest(BaseModel):
    contenido: ContenidoEditor
    funciones: list[str]


async def _run_funcion(nombre: str, contenido: ContenidoEditor) -> tuple[str, dict]:
    # Special cases with their own logic
    if nombre == "piezas-tv":
        try:
            result = await piezas_tv_desde_texto(contenido)
            return nombre, result
        except Exception as exc:
            return nombre, {"ok": False, "error": str(exc)}

    if nombre == "voiceover-texto":
        try:
            result = await voiceover_desde_texto(contenido)
            return nombre, result
        except Exception as exc:
            return nombre, {"ok": False, "error": str(exc)}

    if nombre not in _FUNCIONES:
        return nombre, {"ok": False, "error": f"Función '{nombre}' no reconocida"}
    system, max_tokens = _FUNCIONES[nombre]
    try:
        resultado = await _llamar_llm(contenido, system, max_tokens=max_tokens)
        entry: dict = {"ok": True, "resultado": resultado}
        if nombre == "fake-news-control":
            entry["semaforo"] = _extract_semaforo(resultado)
        return nombre, entry
    except Exception as exc:
        return nombre, {"ok": False, "error": str(exc)}


@router.post("/procesar-seleccion")
async def procesar_seleccion(body: SeleccionRequest) -> dict:
    tasks = [_run_funcion(nombre, body.contenido) for nombre in body.funciones]
    pairs = await asyncio.gather(*tasks)
    return {"resultados": dict(pairs)}

"""Optional AI helpers: metadata suggestions, summaries and translations from crawled content.

Providers (set AI_PROVIDER in .env):
  anthropic - Claude via the official SDK (ANTHROPIC_API_KEY or an `ant auth login` profile). Default model claude-opus-5-5.
  openai    - OpenAI Chat Completions (OPENAI_API_KEY).
  gemini    - Google Gemini generateContent (GEMINI_API_KEY).
  groq      - Groq (GROQ_API_KEY), OpenAI-compatible, with a free plan.
  ollama    - a local model through Ollama's HTTP API; nothing leaves your computer.
Nothing is sent anywhere unless you configure a provider and press a button for a page.
"""
from __future__ import annotations

import os
import time

import httpx

from .config import get_settings

DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"
# Defaults for the other providers; set AI_MODEL in .env to the model you have access to.
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_MODELS = ("openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b", "llama-3.1-8b-instant")
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"   # alias: Google points it to its current Flash model
TASKS = {
    "meta": ("Sugerir título y meta descripción",
             "Propón para esta página un <title> SEO de 50-60 caracteres y una meta descripción de 140-155 caracteres, "
             "en el mismo idioma que el contenido, con la palabra clave principal al principio y una llamada a la acción. "
             "Responde exactamente con dos líneas: «Título: ...» y «Descripción: ...»."),
    "summary": ("Resumir la página", "Resume el contenido de esta página en 3 frases claras para un cliente no técnico."),
    "h1": ("Sugerir H1 y estructura de encabezados",
           "Propón un H1 y 4-6 H2 que ordenen este contenido de forma lógica para SEO. Responde en una lista."),
    "translate_en": ("Traducir título y descripción al inglés",
                     "Traduce al inglés el título y la meta descripción de esta página, manteniendo la intención SEO. "
                     "Responde con dos líneas: «Title: ...» y «Description: ...»."),
    "faq": ("Proponer preguntas frecuentes (FAQ)",
            "Propón 5 preguntas frecuentes con respuestas breves basadas solo en el contenido de esta página."),
    # short answers, designed to become a column when run over many pages
    "intent": ("Clasificar la intención de búsqueda",
               "Clasifica la intención de búsqueda principal de esta página con UNA de estas palabras: informacional, "
               "comercial, transaccional, navegacional o local. Responde solo con esa palabra, en minúsculas."),
    "meta_rewrite": ("Reescribir la meta description",
                     "Escribe una meta description nueva para esta página: 140-155 caracteres, en el idioma del contenido, "
                     "con la palabra clave principal y una llamada a la acción. Responde solo con el texto, sin comillas."),
    "topic": ("Palabra clave y grupo temático",
              "Indica la palabra clave principal de esta página y el grupo temático (clúster) al que pertenece. "
              "Responde en una sola línea con el formato: palabra clave | grupo temático"),
    "custom": ("Mi propia instrucción", ""),
}
BULK_TASKS = ("intent", "meta_rewrite", "topic", "custom", "summary", "translate_en")
MAX_CUSTOM_PROMPT = 2000
SYSTEM = ("Eres un consultor SEO que trabaja para el propietario del sitio. Usa solo la información de la página; "
          "no inventes datos (precios, teléfonos, direcciones) que no aparezcan. Sé conciso.")


class AIUnavailable(RuntimeError):
    pass


def status() -> dict:
    s = get_settings()
    if s.ai_provider == "anthropic":
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return {"enabled": False, "reason": "Falta el paquete anthropic (pip install -r requirements.txt)."}
        return {"enabled": True, "provider": "anthropic", "model": s.ai_model or DEFAULT_CLAUDE_MODEL,
                "note": "El contenido de la página se envía a la API de Anthropic."}
    if s.ai_provider == "ollama":
        return {"enabled": True, "provider": "ollama", "model": s.ai_model or "llama3.1",
                "note": "Modelo local con Ollama; el contenido no sale de tu equipo."}
    if s.ai_provider == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            return {"enabled": False, "reason": "Falta la clave de OpenAI: pégala en Configuración › Inteligencia artificial (IA)."}
        return {"enabled": True, "provider": "openai", "model": s.ai_model or DEFAULT_OPENAI_MODEL,
                "note": "El contenido de la página se envía a la API de OpenAI."}
    if s.ai_provider == "groq":
        if not os.getenv("GROQ_API_KEY"):
            return {"enabled": False, "reason": "Falta la clave de Groq: consíguela gratis en https://console.groq.com/keys y pégala en Configuración › Inteligencia artificial (IA)."}
        return {"enabled": True, "provider": "groq", "model": s.ai_model or DEFAULT_GROQ_MODEL,
                "note": "El contenido de la página se envía a la API de Groq."}
    if s.ai_provider == "gemini":
        if not os.getenv("GEMINI_API_KEY"):
            return {"enabled": False, "reason": "Falta la clave de Gemini: consíguela gratis en https://aistudio.google.com/api-keys y pégala en Configuración › Inteligencia artificial (IA)."}
        return {"enabled": True, "provider": "gemini", "model": s.ai_model or DEFAULT_GEMINI_MODEL,
                "note": "El contenido de la página se envía a la API de Google Gemini."}
    return {"enabled": False, "reason": "La IA aún no está activada: elige un proveedor y pega tu clave en Configuración › Inteligencia artificial (IA)."}


def _page_context(page: dict) -> str:
    text = (page.get("rendered_text") or page.get("text_excerpt") or "")[:12000]
    return (f"URL: {page.get('url')}\nTítulo actual: {page.get('title') or '(ninguno)'}\n"
            f"Meta descripción actual: {page.get('meta_description') or '(ninguna)'}\n"
            f"H1: {' | '.join(page.get('h1') or []) or '(ninguno)'}\n\nContenido visible:\n{text}")


def run_task(task: str, page: dict, custom_prompt: str = "") -> tuple[str, str]:
    """Returns (result_text, model). Raises AIUnavailable with a user-facing message on any failure."""
    if task not in TASKS:
        raise AIUnavailable("Tarea desconocida.")
    instruction = TASKS[task][1]
    if task == "custom":
        instruction = (custom_prompt or "").strip()[:MAX_CUSTOM_PROMPT]
        if not instruction:
            raise AIUnavailable("Escribe la instrucción que quieres aplicar a cada página.")
        instruction += "\nResponde de forma breve (una línea si es posible)."
    st = status()
    if not st["enabled"]:
        raise AIUnavailable(st["reason"])
    prompt = instruction + "\n\n" + _page_context(page)
    if st["provider"] == "anthropic":
        return _claude(prompt, st["model"]), st["model"]
    if st["provider"] == "openai":
        return _openai(prompt, st["model"]), st["model"]
    if st["provider"] == "gemini":
        return _gemini(prompt, st["model"]), st["model"]
    if st["provider"] == "groq":
        return _groq(prompt, st["model"]), st["model"]
    return _ollama(prompt, st["model"]), st["model"]


CONSULTANT = (
    "Eres el consultor SEO integrado en LetsCrawlit, un rastreador y auditor SEO técnico. Hablas con el dueño de la web "
    "o con quien la gestiona, que puede no ser técnico.\n"
    "- Responde en el idioma en que te escriban, con palabras sencillas y de forma breve: explica por encima y "
    "ofrece entrar en detalle si lo quieren.\n"
    "- Si te piden ayuda (qué arreglar primero, cómo corregir algo, redactar un título o una descripción…), da pasos "
    "concretos y ordenados.\n"
    "- Si tienes datos de una auditoría, básate en ellos y cita cifras y URLs reales. No inventes datos que no estén "
    "ahí; si algo no aparece, dilo y explica dónde mirarlo en LetsCrawlit (pestañas como Internos, Códigos de respuesta, "
    "Títulos de página, Errores o el informe «Qué arreglar primero»).\n"
    "- No prometas posiciones en Google ni plazos.")
MANUAL_PATH = os.path.join(os.path.dirname(__file__), "knowledge", "manual_seo.md")


def manual() -> str:
    """The consultant's SEO method (app/knowledge/manual_seo.md), read on every answer so edits apply at once."""
    try:
        with open(MANUAL_PATH, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


MAX_CHAT_TURNS = 20      # earlier messages are dropped so the conversation never grows without limit
MAX_CHAT_MESSAGE = 4000


def chat(messages: list[dict], context: str = "", page: str = "") -> tuple[str, str]:
    """A conversation with the SEO consultant. `messages` alternate user/assistant and end with the user's question
    (a message may carry `model_content`, what the model reads instead of what the user sees). `context` is a
    plain-text summary of the open audit and `page` the data of the URL being asked about. Returns (answer, model)."""
    st = status()
    if not st["enabled"]:
        raise AIUnavailable(st["reason"])
    msgs = [{"role": m["role"], "content": str(m.get("model_content") or m["content"])[:MAX_CHAT_MESSAGE + 300]}
            for m in messages[-MAX_CHAT_TURNS:]]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if not msgs:
        raise AIUnavailable("Escribe tu pregunta.")
    m = manual()
    system = CONSULTANT + ("\n\nSigue este manual de trabajo (es tu método; no lo cites como documento):\n\n" + m if m else "") + \
        ("\n\nDatos de la auditoría abierta:\n" + context if context else
                           "\n\nAhora mismo no hay ninguna auditoría abierta: responde de forma general.") + \
        ("\n\nLa última pregunta es sobre esta URL concreta. Analízala con el manual: di qué está bien, qué falla y "
         "cómo arreglarlo, en orden de prioridad, y propón textos concretos (title, description, H1) cuando ayude:\n" + page
         if page else "")
    call = {"anthropic": _claude, "openai": _openai, "gemini": _gemini, "groq": _groq}.get(st["provider"], _ollama)
    return call("", st["model"], system=system, messages=msgs), st["model"]


def _http_error(name: str, r: httpx.Response, model: str) -> AIUnavailable:
    if r.status_code in (401, 403):
        return AIUnavailable(f"Clave de {name} no válida o sin permiso.")
    if r.status_code == 404:
        return AIUnavailable(f"{name} ya no ofrece el modelo «{model}» (o no existe). Elige otro en "
                             "Configuración › Inteligencia artificial (IA), o deja el campo Modelo vacío para usar el recomendado.")
    if r.status_code in (500, 502, 503, 504):
        return AIUnavailable(f"{name} está saturado o caído ahora mismo (no es un fallo tuyo); prueba de nuevo en un minuto.")
    if r.status_code == 429:
        return AIUnavailable(f"Límite de uso de {name} alcanzado; prueba en un minuto.")
    return AIUnavailable(f"{name} devolvió HTTP {r.status_code}.")


def _chat_completions(base: str, key_var: str, name: str, model: str, system: str, msgs: list[dict]) -> httpx.Response:
    try:
        return httpx.post(base + "/chat/completions", timeout=120, headers={"Authorization": f"Bearer {os.getenv(key_var, '')}"},
                          json={"model": model, "messages": [{"role": "system", "content": system}, *msgs]})
    except httpx.HTTPError:
        raise AIUnavailable(f"No hay conexión con la API de {name}.")


def _completion_text(r: httpx.Response, name: str) -> str:
    try:
        text = (r.json()["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError, ValueError):
        text = ""
    if not text:
        raise AIUnavailable(f"{name} devolvió una respuesta vacía.")
    return text


def _openai(prompt: str, model: str, system: str = SYSTEM, messages: list[dict] | None = None) -> str:
    r = _chat_completions("https://api.openai.com/v1", "OPENAI_API_KEY", "OpenAI", model, system,
                          messages or [{"role": "user", "content": prompt}])
    if r.status_code != 200:
        raise _http_error("OpenAI", r, model)
    return _completion_text(r, "OpenAI")


GROQ_BASE = "https://api.groq.com/openai/v1"


def _groq_replacement(current: str) -> str | None:
    """Groq retires models now and then: pick a chat model the key can still use (preferred ones first)."""
    try:
        r = httpx.get(GROQ_BASE + "/models", timeout=30, headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY', '')}"})
        ids = [m["id"] for m in r.json().get("data", []) if m.get("active", True)] if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
    chat = [i for i in ids if i != current and not any(x in i.lower() for x in ("whisper", "guard", "tts", "orpheus", "embed"))]
    for pref in GROQ_MODELS:
        if pref in chat:
            return pref
    return next((i for i in chat if any(x in i.lower() for x in ("gpt-oss", "llama", "qwen", "kimi"))), None)


def _groq(prompt: str, model: str, system: str = SYSTEM, messages: list[dict] | None = None) -> str:
    msgs = messages or [{"role": "user", "content": prompt}]
    r = _chat_completions(GROQ_BASE, "GROQ_API_KEY", "Groq", model, system, msgs)
    if r.status_code in (400, 404) and "model" in r.text.lower():   # retired or unknown model
        alt = _groq_replacement(model)
        if alt:
            r = _chat_completions(GROQ_BASE, "GROQ_API_KEY", "Groq", alt, system, msgs)
    if r.status_code != 200:
        raise _http_error("Groq", r, model)
    return _completion_text(r, "Groq")


def _gemini(prompt: str, model: str, system: str = SYSTEM, messages: list[dict] | None = None) -> str:
    msgs = messages or [{"role": "user", "content": prompt}]
    contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in msgs]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    for attempt in range(2):   # Gemini often answers 503 «high demand» for a moment: retry once
        try:
            r = httpx.post(url, timeout=120, headers={"x-goog-api-key": os.getenv("GEMINI_API_KEY", "")},
                           json={"systemInstruction": {"parts": [{"text": system}]}, "contents": contents})
        except httpx.HTTPError:
            raise AIUnavailable("No hay conexión con la API de Gemini.")
        if r.status_code != 503 or attempt:
            break
        time.sleep(3)
    if r.status_code != 200:
        raise _http_error("Gemini", r, model)
    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, ValueError):
        text = ""
    if not text:
        raise AIUnavailable("Gemini devolvió una respuesta vacía (o la bloqueó por sus filtros).")
    return text


def _claude(prompt: str, model: str, system: str = SYSTEM, messages: list[dict] | None = None) -> str:
    import anthropic

    client = anthropic.Anthropic(max_retries=2, timeout=120.0)
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            output_config={"effort": "low"},
            # Server-side refusal fallback (routes a declined request to a suitable model automatically).
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=messages or [{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError:
        raise AIUnavailable("Clave de Anthropic no válida o ausente (ANTHROPIC_API_KEY o `ant auth login`).")
    except anthropic.PermissionDeniedError:
        raise AIUnavailable("La clave de Anthropic no tiene permiso para este modelo.")
    except anthropic.NotFoundError:
        raise AIUnavailable(f"Modelo no encontrado: {model}. Revisa AI_MODEL en .env.")
    except anthropic.RateLimitError:
        raise AIUnavailable("Límite de uso de la API alcanzado; prueba en un minuto.")
    except anthropic.BadRequestError as e:
        raise AIUnavailable(f"Petición rechazada por la API: {e.message}")
    except anthropic.APIStatusError as e:
        raise AIUnavailable(f"Error de la API de Anthropic ({e.status_code}); inténtalo más tarde.")
    except anthropic.APIConnectionError:
        raise AIUnavailable("No hay conexión con la API de Anthropic.")
    if response.stop_reason == "refusal":
        raise AIUnavailable("El modelo declinó esta petición.")
    text = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise AIUnavailable("La respuesta llegó vacía.")
    return text


def _ollama(prompt: str, model: str, system: str = SYSTEM, messages: list[dict] | None = None) -> str:
    url = get_settings().ollama_url.rstrip("/") + "/api/chat"
    try:
        r = httpx.post(url, json={"model": model, "stream": False, "messages": [
            {"role": "system", "content": system}, *(messages or [{"role": "user", "content": prompt}])]}, timeout=180)
    except httpx.HTTPError:
        raise AIUnavailable(f"Ollama no responde en {get_settings().ollama_url}. ¿Está abierto?")
    if r.status_code == 404:
        raise AIUnavailable(f"Ollama no tiene el modelo «{model}». Ejecuta: ollama pull {model}")
    if r.status_code != 200:
        raise AIUnavailable(f"Ollama devolvió HTTP {r.status_code}.")
    text = (r.json().get("message") or {}).get("content", "").strip()
    if not text:
        raise AIUnavailable("Ollama devolvió una respuesta vacía.")
    return text

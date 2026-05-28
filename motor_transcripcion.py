import os
import json
import logging
import time
import vertexai
from vertexai.generative_models import GenerativeModel, Part, HarmCategory, HarmBlockThreshold

logger = logging.getLogger(__name__)

# Inicializamos Vertex AI. Al estar en Cloud Run o Local, tomará el PROJECT_ID
vertexai.init(location="global")

def transcribir_segmento(ruta_audio: str, num_segmento: int, modelo_id: str = "gemini-3.5-flash") -> dict:
    """
    Envía un segmento de audio a Gemini para transcripción y análisis de correcciones.
    """
    logger.info(f"[PROCESANDO] Enviando segmento {num_segmento} a {modelo_id}...")
    
    # Configuramos la seguridad para evitar bloqueos en testimonios sensibles
    configuracion_segura = {
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
    }
    
    # AQUÍ ESTÁ EL CAMBIO: Forzamos salida JSON y extendemos el límite de tokens al máximo
    configuracion_generacion = {
        "response_mime_type": "application/json",
        "max_output_tokens": 60000,
    }
    
    modelo = GenerativeModel(modelo_id)
    
    # Cargamos el audio en memoria como un objeto Part
    with open(ruta_audio, "rb") as f:
        audio_bytes = f.read()
    audio_part = Part.from_data(data=audio_bytes, mime_type="audio/flac")
    
    prompt = """
    Eres un perito legal experto en transcripciones de casos.
    Escucha atentamente el siguiente segmento de audio y procesa la información solicitada.
    
    INSTRUCCIONES CRÍTICAS:
    1. PREGUNTA CLAVE: ¿En algún punto del audio el cliente solicita correcciones o comenta que algo está erróneo en su proceso? Responde claro y detalla el comentario si existe.
    2. ROLES: Identifica quién es el 'Abogado' (quien dirige/pregunta) y quién el 'Cliente' (quien da testimonio). 
    3. TIEMPOS: Extrae el tiempo de inicio de cada intervención.
    4. FORMATO: Devuelve EXCLUSIVAMENTE un objeto JSON válido con esta estructura exacta:
    {
      "analisis_correcciones": "Tu respuesta a la pregunta clave basándote en este audio.",
      "transcripcion": [
        {"tiempo_ms": 1000, "hablante": "Abogado", "texto": "Buenos días, cuénteme su caso."},
        {"tiempo_ms": 5000, "hablante": "Cliente", "texto": "Buenos días abogado, mi historia es..."}
      ]
    }
    No incluyas markdown ni texto fuera del JSON.
    """

    max_intentos = 3
    for intento in range(max_intentos):
        try:
            respuesta = modelo.generate_content(
                [audio_part, prompt],
                safety_settings=configuracion_segura,
                generation_config=configuracion_generacion
            )
            
            if respuesta.text:
                return json.loads(respuesta.text)
                
        except Exception as e:
            logger.warning(f"Intento {intento + 1} fallido en segmento {num_segmento}: {str(e)}")
            time.sleep(5) 
            
    logger.error(f"[ERROR CRÍTICO] No se pudo transcribir el segmento {num_segmento}.")
    return {"analisis_correcciones": "Error: IA no pudo analizar este segmento.", "transcripcion": []}

def procesar_transcripcion_completa(carpeta_segmentos: str) -> dict:
    """
    Orquesta la transcripción de todos los segmentos, ajusta los tiempos y consolida el análisis.
    """
    segmentos_audio = sorted([f for f in os.listdir(carpeta_segmentos) if f.endswith(".flac")])
    transcripcion_completa = []
    analisis_global = []
    
    # Sabemos por el preprocesador que cada segmento mide exactamente 15 minutos (900,000 ms)
    ms_por_segmento = 3000000

    for i, nombre_audio in enumerate(segmentos_audio):
        ruta_audio = os.path.join(carpeta_segmentos, nombre_audio)
        inicio_ms_real = i * ms_por_segmento
        
        datos_segmento = transcribir_segmento(ruta_audio, num_segmento=i+1)
        
        # Guardamos el análisis de este segmento específico
        analisis_global.append(f"--- Análisis Segmento {i+1} ---\n{datos_segmento.get('analisis_correcciones', 'Sin análisis')}")
        
        bloques = datos_segmento.get('transcripcion', [])
            
        for b in bloques:
            # Sumamos el tiempo base del segmento
            ms_reales = int(b.get('tiempo_ms', 0)) + inicio_ms_real
            minutos = ms_reales // 60000
            segundos = (ms_reales % 60000) // 1000
            
            transcripcion_completa.append({
                "tiempo_formato": f"{minutos:02d}:{segundos:02d}",
                "hablante": b.get('hablante', 'Desconocido'),
                "texto": b.get('texto', '')
            })

    return {
        "analisis_completo": "\n\n".join(analisis_global),
        "lineas_transcripcion": transcripcion_completa
    }

def generar_texto_para_doc(datos_procesados: dict) -> str:
    """
    Convierte el diccionario de resultados en un formato de texto limpio
    ideal para inyectarlo como contenido en el Google Doc.
    """
    texto_doc = "=== ANÁLISIS DE CORRECCIONES SOLICITADAS POR EL CLIENTE ===\n\n"
    texto_doc += datos_procesados.get('analisis_completo', '') + "\n\n"
    texto_doc += "===========================================================\n"
    texto_doc += "=== TRANSCRIPCIÓN COMPLETA ===\n\n"
    
    for linea in datos_procesados.get('lineas_transcripcion', []):
        texto_doc += f"[{linea['tiempo_formato']}] {linea['hablante']}: {linea['texto']}\n"
        
    return texto_doc
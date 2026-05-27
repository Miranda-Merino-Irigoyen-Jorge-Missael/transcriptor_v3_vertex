import os
import json
import logging
import time
import vertexai
from vertexai.generative_models import GenerativeModel, Part, HarmCategory, HarmBlockThreshold

logger = logging.getLogger(__name__)

# Inicializamos Vertex AI. Al estar en Cloud Run, tomará el PROJECT_ID y LOCATION
vertexai.init(location="global")

def transcribir_segmento(ruta_audio: str, num_segmento: int, modelo_id: str = "gemini-3.5-flash") -> list:
    """
    Envía un segmento de audio a Gemini para transcripción y diarización directa.
    """
    logger.info(f"[PROCESANDO] Enviando segmento {num_segmento} a {modelo_id}...")
    
    # Configuramos la seguridad para evitar bloqueos en testimonios legales sensibles
    configuracion_segura = {
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
    }
    
    # Forzamos la salida en JSON para que el frontend no se rompa
    configuracion_generacion = {
        "response_mime_type": "application/json",
    }
    
    modelo = GenerativeModel(modelo_id)
    
    # Cargamos el audio en memoria como un objeto Part
    with open(ruta_audio, "rb") as f:
        audio_bytes = f.read()
    audio_part = Part.from_data(data=audio_bytes, mime_type="audio/flac")
    
    prompt = """
    Eres un perito legal experto en transcripciones de casos de inmigración (VAWA/Visa T).
    Escucha atentamente el siguiente audio y transcribe la conversación.
    
    INSTRUCCIONES CRÍTICAS:
    1. ROLES: Identifica quién es el 'Abogado' (quien dirige/pregunta) y quién el 'Cliente' (quien da testimonio). 
    2. TIEMPOS: Extrae el tiempo de inicio de cada intervención.
    3. FORMATO: Devuelve EXCLUSIVAMENTE un arreglo JSON válido con este formato exacto:
    [
      {"tiempo_ms": 1000, "hablante": "Abogado", "texto": "Buenos días, cuénteme su caso."},
      {"tiempo_ms": 5000, "hablante": "Cliente", "texto": "Buenos días abogado, mi historia es..."}
    ]
    No incluyas markdown ni texto fuera del arreglo JSON.
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
            time.sleep(5) # Pequeña pausa antes de reintentar
            
    logger.error(f"[ERROR CRÍTICO] No se pudo transcribir el segmento {num_segmento}.")
    return []

def procesar_transcripcion_completa(carpeta_segmentos: str, archivo_final_json: str) -> str:
    """
    Orquesta la transcripción de todos los segmentos y ajusta los tiempos.
    """
    segmentos_audio = sorted([f for f in os.listdir(carpeta_segmentos) if f.endswith(".flac")])
    transcripcion_completa = []
    
    # Sabemos por el preprocesador que cada segmento mide exactamente 1 hora (3600 segundos)
    # Convertimos a milisegundos para el ajuste
    ms_por_segmento = 3600000 

    for i, nombre_audio in enumerate(segmentos_audio):
        ruta_audio = os.path.join(carpeta_segmentos, nombre_audio)
        inicio_ms_real = i * ms_por_segmento
        
        bloques = transcribir_segmento(ruta_audio, num_segmento=i+1)
        
        if not bloques:
            transcripcion_completa.append({
                "error": True,
                "segmento": i+1,
                "mensaje": "Fallo en el procesamiento de este bloque por la IA."
            })
            continue
            
        for b in bloques:
            # Sumamos el tiempo base del segmento para que el reproductor web salte correctamente
            ms_reales = int(b.get('tiempo_ms', 0)) + inicio_ms_real
            minutos = ms_reales // 60000
            segundos = (ms_reales % 60000) // 1000
            
            transcripcion_completa.append({
                "tiempo_ms": ms_reales,
                "tiempo_formato": f"{minutos:02d}:{segundos:02d}",
                "hablante": b.get('hablante', 'Desconocido'),
                "texto": b.get('texto', '')
            })

    # Guardamos el resultado unificado
    with open(archivo_final_json, "w", encoding="utf-8") as f:
        json.dump(transcripcion_completa, f, ensure_ascii=False, indent=4)
        
    logger.info(f"Transcripción final ensamblada en: {archivo_final_json}")
    return archivo_final_json
import os
import json
import logging
import time
import vertexai
from vertexai.generative_models import GenerativeModel, Part, HarmCategory, HarmBlockThreshold
import gestor_almacenamiento 

logger = logging.getLogger(__name__)
vertexai.init(location="global")

def transcribir_segmento(uri_gcs: str, num_segmento: int, modelo_id: str = "gemini-3.5-flash") -> dict:
    logger.info(f"[PROCESANDO] Enviando segmento {num_segmento} a {modelo_id} desde {uri_gcs}...")
    
    configuracion_segura = {
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
    }
    
    configuracion_generacion = {
        "response_mime_type": "application/json",
        "max_output_tokens": 60000, 
    }
    
    modelo = GenerativeModel(modelo_id)
    audio_part = Part.from_uri(uri=uri_gcs, mime_type="audio/flac")
    
    prompt = """
    Eres un perito legal experto en transcripciones.
    Escucha atentamente el audio COMPLETO y transcríbelo TODO palabra por palabra. No resumas.
    
    INSTRUCCIONES CRÍTICAS:
    1. ROLES: Identifica quién es el 'Abogado' y quién el 'Cliente'. Si no estás seguro o es un tercero, usa 'Hablante 1', 'Hablante 2', 'Operadora', etc.
    2. TIEMPOS: Extrae el tiempo de inicio de cada intervención en milisegundos.
    3. FORMATO: Devuelve EXCLUSIVAMENTE un objeto JSON válido.
    
    ¡IMPORTANTE! Jamás debes devolver el array de "transcripcion" vacío. Si hay voces o ruido, transcríbelo o descríbelo.
    
    Estructura exacta esperada:
    {
      "transcripcion": [
        {"tiempo_ms": 1000, "hablante": "Abogado", "texto": "Buenos días."},
        {"tiempo_ms": 5000, "hablante": "Operadora", "texto": "Por favor espere..."}
      ]
    }
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
                logger.info(f"[DEBUG IA] Respuesta cruda (Inicio): {respuesta.text[:500]}")
                return json.loads(respuesta.text)
                
        except Exception as e:
            logger.warning(f"Intento {intento + 1} fallido en segmento {num_segmento}: {str(e)}")
            time.sleep(5) 
            
    logger.error(f"[ERROR CRÍTICO] No se pudo transcribir el segmento {num_segmento}.")
    return {"transcripcion": []}

def procesar_transcripcion_completa(carpeta_segmentos: str, nombre_bucket: str) -> dict:
    segmentos_audio = sorted([f for f in os.listdir(carpeta_segmentos) if f.endswith(".flac")])
    transcripcion_completa = []
    ms_por_segmento = 3600000 # Emparejado con la segmentación de ffmpeg (1 hora)

    for i, nombre_audio in enumerate(segmentos_audio):
        ruta_audio_local = os.path.join(carpeta_segmentos, nombre_audio)
        ruta_destino_gcs = f"tmp_procesamiento/segmento_{i+1}_{int(time.time())}.flac"
        inicio_ms_real = i * ms_por_segmento
        uri_gcs = ""
        
        try:
            uri_gcs = gestor_almacenamiento.subir_a_gcs(ruta_audio_local, nombre_bucket, ruta_destino_gcs)
            datos_segmento = transcribir_segmento(uri_gcs, num_segmento=i+1)
            
            bloques = datos_segmento.get('transcripcion', [])
            if not bloques:
                logger.warning(f"⚠️ El segmento {i+1} no generó ningún texto de transcripción en el JSON.")
                
            for b in bloques:
                ms_reales = int(b.get('tiempo_ms', 0)) + inicio_ms_real
                minutos = ms_reales // 60000
                segundos = (ms_reales % 60000) // 1000
                
                transcripcion_completa.append({
                    "tiempo_formato": f"{minutos:02d}:{segundos:02d}",
                    "hablante": b.get('hablante', 'Desconocido'),
                    "texto": b.get('texto', '')
                })
        
        finally:
            if uri_gcs:
                gestor_almacenamiento.eliminar_de_gcs(nombre_bucket, ruta_destino_gcs)

    return {"lineas_transcripcion": transcripcion_completa}

def generar_texto_para_doc(datos_procesados: dict) -> str:
    texto_doc = "=== TRANSCRIPCIÓN COMPLETA ===\n\n"
    for linea in datos_procesados.get('lineas_transcripcion', []):
        texto_doc += f"[{linea['tiempo_formato']}] {linea['hablante']}: {linea['texto']}\n"
    return texto_doc
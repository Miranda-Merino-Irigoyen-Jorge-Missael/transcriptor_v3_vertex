from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import firestore
from pydantic import BaseModel
import os
import shutil
import logging
from dotenv import load_dotenv

# Cargar las variables de entorno desde el archivo .env al inicio
load_dotenv()

# Importamos nuestros nuevos módulos limpios
import preprocesar_audio
import motor_transcripcion
import gestor_almacenamiento

# Configuración de logging profesional
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="API de Transcripción VAWA/RFE - V3 Vertex")

# Configuración de CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inicializamos el cliente de Firestore
db = firestore.Client()
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")

# Definimos estrictamente la estructura de entrada esperada
class TranscripcionRequest(BaseModel):
    enlace_drive: str
    cliente_id: str

def tarea_orquestador(enlace_drive: str, cliente_id: str):
    """
    Función en segundo plano que dirige la sinfonía de los módulos.
    Usa /tmp porque es el almacenamiento en memoria permitido en Cloud Run.
    """
    carpeta_trabajo = os.path.join("/tmp", f"temp_{cliente_id}")
    carpeta_segmentos = os.path.join(carpeta_trabajo, "segmentos")
    archivo_json_final = os.path.join(carpeta_trabajo, f"Transcripcion_{cliente_id}.json")
    
    doc_ref = db.collection('trabajos_transcripcion').document(cliente_id)

    try:
        os.makedirs(carpeta_segmentos, exist_ok=True)
        logger.info(f"Iniciando orquestación para cliente: {cliente_id}")

        # 1. Validación de seguridad y Descarga
        ruta_audio = gestor_almacenamiento.validar_y_descargar_drive(enlace_drive, carpeta_trabajo)

        # 2. Preprocesamiento (FFMPEG)
        rutas_segmentos = preprocesar_audio.preprocesar_con_ffmpeg(ruta_audio, carpeta_segmentos)

        # 3. Transcripción Inteligente (Vertex AI con Gemini 3.5 Flash)
        ruta_resultado = motor_transcripcion.procesar_transcripcion_completa(carpeta_segmentos, archivo_json_final)

        # 4. Subida al Bucket (GCS)
        uri_final = gestor_almacenamiento.subir_resultado_gcs(ruta_resultado, GCS_BUCKET_NAME, cliente_id)

        # 5. Notificación de Éxito al Frontend
        doc_ref.update({
            'status': 'completado',
            'resultado_url': uri_final,
            'completado_en': firestore.SERVER_TIMESTAMP
        })
        logger.info(f"Proceso finalizado con éxito. Resultado en: {uri_final}")

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error crítico en el caso {cliente_id}: {error_msg}")
        
        # Notificación de Error al Frontend
        doc_ref.update({
            'status': 'error',
            'error_mensaje': error_msg,
            'completado_en': firestore.SERVER_TIMESTAMP
        })

    finally:
        # Limpieza obligatoria para no desbordar la memoria RAM de Cloud Run
        if os.path.exists(carpeta_trabajo):
            shutil.rmtree(carpeta_trabajo, ignore_errors=True)
            logger.info(f"Recursos temporales liberados para: {cliente_id}")

@app.post("/iniciar-transcripcion")
async def iniciar_transcripcion(payload: TranscripcionRequest, background_tasks: BackgroundTasks):
    """
    Endpoint principal. Registra la intención en BD y libera la conexión HTTP al instante.
    """
    doc_ref = db.collection('trabajos_transcripcion').document(payload.cliente_id)
    
    # Marcamos el inicio en la base de datos
    doc_ref.set({
        'status': 'procesando',
        'cliente_id': payload.cliente_id,
        'creado_en': firestore.SERVER_TIMESTAMP,
        'resultado_url': None,
        'error_mensaje': None
    })

    # Lanzamos el trabajo pesado al fondo
    background_tasks.add_task(tarea_orquestador, payload.enlace_drive, payload.cliente_id)
    
    return {
        "status": "procesamiento_iniciado", 
        "cliente": payload.cliente_id,
        "mensaje": "Procesamiento asíncrono iniciado correctamente."
    }
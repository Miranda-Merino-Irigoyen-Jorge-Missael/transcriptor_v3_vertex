import os
import time
import shutil
import tempfile
import logging
from dotenv import load_dotenv

# Cargar las variables de entorno desde el archivo .env al inicio
load_dotenv()

# Configuración de logging para verlo en consola local
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

import gspread
import gestor_almacenamiento
import preprocesar_audio
import motor_transcripcion

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
FOLDER_DOCS_ID = os.getenv("FOLDER_DOCS_ID")

def obtener_hoja():
    """Autentica y obtiene la hoja de cálculo usando las credenciales de OAuth locales."""
    creds = gestor_almacenamiento.obtener_credenciales_oauth()
    cliente_sheets = gspread.authorize(creds)
    spreadsheet = cliente_sheets.open_by_key(SPREADSHEET_ID)
    return spreadsheet.worksheet("8x8 Call Records")

def procesar_fila(hoja, num_fila: int, enlace_audio: str):
    """Ejecuta el flujo completo para un enlace y actualiza la hoja."""
    logger.info(f"--- Iniciando procesamiento para la fila {num_fila} ---")
    
    # Creamos una carpeta temporal segura en tu entorno local
    carpeta_trabajo = tempfile.mkdtemp(prefix=f"transcripcion_fila_{num_fila}_")
    carpeta_segmentos = os.path.join(carpeta_trabajo, "segmentos")
    os.makedirs(carpeta_segmentos, exist_ok=True)
    
    try:
        # 1. Descargar el audio
        logger.info(f"Paso 1: Descargando audio desde -> {enlace_audio}")
        ruta_audio = gestor_almacenamiento.descargar_audio(enlace_audio, carpeta_trabajo)
        
        # 2. Preprocesar (FFMPEG)
        logger.info("Paso 2: Preprocesando y segmentando con FFMPEG...")
        rutas_segmentos = preprocesar_audio.preprocesar_con_ffmpeg(ruta_audio, carpeta_segmentos)
        
        # 3. Transcribir y Analizar (Vertex AI)
        logger.info("Paso 3: Transcribiendo y analizando con Vertex AI...")
        datos_procesados = motor_transcripcion.procesar_transcripcion_completa(carpeta_segmentos)
        
        # 4. Generar Google Doc
        logger.info("Paso 4: Generando el Google Doc...")
        texto_doc = motor_transcripcion.generar_texto_para_doc(datos_procesados)
        titulo_doc = f"Transcripción y Análisis - Fila {num_fila}"
        url_doc = gestor_almacenamiento.crear_google_doc_con_transcripcion(titulo_doc, texto_doc, FOLDER_DOCS_ID)
        
        # 5. Escribir resultado en la columna R (Columna 18 en índice de Sheets)
        logger.info(f"Paso 5: Actualizando Google Sheet en la fila {num_fila}...")
        hoja.update_cell(num_fila, 18, url_doc)
        logger.info(f"✅ Fila {num_fila} completada con éxito.")

    except Exception as e:
        error_msg = f"ERROR: {str(e)}"
        logger.error(f"❌ Fallo en la fila {num_fila}: {error_msg}")
        # Escribimos el error en la hoja para no volver a procesarla y saber qué pasó
        hoja.update_cell(num_fila, 18, error_msg)

    finally:
        # Limpieza obligatoria para no saturar tu disco duro local
        shutil.rmtree(carpeta_trabajo, ignore_errors=True)
        logger.info(f"Recursos temporales liberados para fila {num_fila}.\n")

def ejecutar_pipeline():
    """Lee la hoja, busca audios pendientes y los procesa uno por uno."""
    logger.info("Conectando a Google Sheets...")
    try:
        if not SPREADSHEET_ID or not FOLDER_DOCS_ID:
            raise ValueError("Falta SPREADSHEET_ID o FOLDER_DOCS_ID en tu archivo .env")

        hoja = obtener_hoja()
        # Descargamos todo el cuadro de datos de una vez (es más rápido y no satura la API)
        valores = hoja.get_all_values()
        
        # Iteramos desde la fila 6 (índice 5 en programación)
        # Columna Q es el índice 16, Columna R es el índice 17
        for idx in range(5, len(valores)):
            fila = valores[idx]
            num_fila_real = idx + 1 
            
            # Validamos que la fila no esté vacía
            enlace_q = fila[16].strip() if len(fila) > 16 else ""
            resultado_r = fila[17].strip() if len(fila) > 17 else ""
            
            # Si hay enlace en Q y la R está vacía (no se ha procesado ni hay error)
            if enlace_q and not resultado_r:
                procesar_fila(hoja, num_fila_real, enlace_q)
                
        logger.info("Se ha barrido toda la hoja y no hay más casos pendientes.")

    except Exception as e:
        logger.error(f"Error crítico en el orquestador: {e}")

if __name__ == "__main__":
    print("\n" + "="*50)
    print("INICIANDO TRANSCRIPTOR BATCH (LOCAL)")
    print("="*50 + "\n")
    ejecutar_pipeline()
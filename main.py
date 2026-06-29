import os
import time
import shutil
import tempfile
import logging
from dotenv import load_dotenv
import gspread
import gestor_almacenamiento
import preprocesar_audio
import motor_transcripcion

# Cargar las variables de entorno desde el archivo .env al inicio
load_dotenv()

# Configuración de logging para verlo en consola local
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- VARIABLES DINÁMICAS DESDE .ENV ---
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
FOLDER_DOCS_ID = os.getenv("FOLDER_DOCS_ID")
GCP_BUCKET_NAME = os.getenv("GCP_BUCKET_NAME")           # NUEVO: Nombre de tu bucket en GCP
SHEET_NAME = os.getenv("SHEET_NAME", "8x8 Call Records") # Valor por defecto
COL_INPUT_LETTER = os.getenv("COL_INPUT", "Q")           # Columna donde buscará los enlaces
COL_OUTPUT_LETTER = os.getenv("COL_OUTPUT", "R")         # Columna donde escribirá el Google Doc

def letra_a_columna(letra: str) -> int:
    """
    Convierte una letra de columna de Excel/Sheets a un índice numérico base 1.
    """
    letra = letra.upper()
    columna = 0
    for char in letra:
        columna = columna * 26 + (ord(char) - ord('A')) + 1
    return columna

def obtener_hoja():
    """Autentica y obtiene la hoja de cálculo usando las credenciales de OAuth locales."""
    creds = gestor_almacenamiento.obtener_credenciales_oauth()
    cliente_sheets = gspread.authorize(creds)
    spreadsheet = cliente_sheets.open_by_key(SPREADSHEET_ID)
    return spreadsheet.worksheet(SHEET_NAME)

def procesar_fila(hoja, num_fila: int, enlace_multimedia: str, col_output_idx: int):
    """Ejecuta el flujo completo para un enlace (audio/video) y actualiza la hoja."""
    logger.info(f"--- Iniciando procesamiento para la fila {num_fila} ---")
    
    carpeta_trabajo = tempfile.mkdtemp(prefix=f"transcripcion_fila_{num_fila}_")
    carpeta_segmentos = os.path.join(carpeta_trabajo, "segmentos")
    os.makedirs(carpeta_segmentos, exist_ok=True)
    
    try:
        if not GCP_BUCKET_NAME:
            raise ValueError("Falta GCP_BUCKET_NAME en tu archivo .env. Configúralo antes de continuar.")

        # 1. Descargar el archivo (Audio o Video)
        logger.info(f"Paso 1: Descargando archivo desde -> {enlace_multimedia}")
        ruta_multimedia = gestor_almacenamiento.descargar_audio(enlace_multimedia, carpeta_trabajo)
        
        # 2. Preprocesar (Extraer audio si es video, validar duración y segmentar)
        logger.info("Paso 2: Preprocesando (Extracción infalible y segmentación)...")
        rutas_segmentos = preprocesar_audio.preprocesar_con_ffmpeg(ruta_multimedia, carpeta_segmentos)
        
        # 3. Transcribir y Analizar (Vertex AI leyendo desde GCS Bucket)
        logger.info("Paso 3: Subiendo a Bucket y Transcribiendo con Vertex AI...")
        # Pasamos el nombre del bucket para que el motor orqueste la subida y limpieza
        datos_procesados = motor_transcripcion.procesar_transcripcion_completa(carpeta_segmentos, GCP_BUCKET_NAME)
        
        # 4. Generar Google Doc
        logger.info("Paso 4: Generando el Google Doc...")
        texto_doc = motor_transcripcion.generar_texto_para_doc(datos_procesados)
        titulo_doc = f"Transcripción y Análisis - Fila {num_fila}"
        url_doc = gestor_almacenamiento.crear_google_doc_con_transcripcion(titulo_doc, texto_doc, FOLDER_DOCS_ID)
        
        # 5. Escribir resultado en la columna dinámica
        logger.info(f"Paso 5: Actualizando Google Sheet en la fila {num_fila}, columna {COL_OUTPUT_LETTER}...")
        hoja.update_cell(num_fila, col_output_idx, url_doc)
        logger.info(f"✅ Fila {num_fila} completada con éxito.")

    except Exception as e:
        error_msg = f"ERROR: {str(e)}"
        logger.error(f"❌ Fallo en la fila {num_fila}: {error_msg}")
        hoja.update_cell(num_fila, col_output_idx, error_msg)

    finally:
        shutil.rmtree(carpeta_trabajo, ignore_errors=True)
        logger.info(f"Recursos temporales liberados para fila {num_fila}.\n")

def ejecutar_pipeline():
    """Lee la hoja, busca audios/videos pendientes y los procesa uno por uno."""
    logger.info(f"Conectando a Google Sheets (Pestaña: {SHEET_NAME})...")
    try:
        if not SPREADSHEET_ID or not FOLDER_DOCS_ID:
            raise ValueError("Falta SPREADSHEET_ID o FOLDER_DOCS_ID en tu archivo .env")

        hoja = obtener_hoja()
        valores = hoja.get_all_values()
        
        col_input_idx_base1 = letra_a_columna(COL_INPUT_LETTER)
        col_output_idx_base1 = letra_a_columna(COL_OUTPUT_LETTER)
        
        indice_array_input = col_input_idx_base1 - 1
        indice_array_output = col_output_idx_base1 - 1

        for idx in range(1, len(valores)):
            fila = valores[idx]
            num_fila_real = idx + 1 
            
            enlace_q = fila[indice_array_input].strip() if len(fila) > indice_array_input else ""
            resultado_r = fila[indice_array_output].strip() if len(fila) > indice_array_output else ""
            
            if enlace_q and not resultado_r:
                procesar_fila(hoja, num_fila_real, enlace_q, col_output_idx_base1)
                
        logger.info("Se ha barrido toda la hoja y no hay más casos pendientes.")

    except Exception as e:
        logger.error(f"Error crítico en el orquestador: {e}")

if __name__ == "__main__":
    print("\n" + "="*50)
    print("INICIANDO TRANSCRIPTOR BATCH (SOPORTE AUDIO/VIDEO & GCP BUCKET)")
    print("="*50 + "\n")
    ejecutar_pipeline()